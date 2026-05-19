#!/usr/bin/env python3
"""Tello 无人机控制器——持久 TCP 服务器进程

该进程是所有 CLI 脚本与 DJITelloPy 之间的唯一桥梁。
内部通过 DJITelloPy 库（UDP 协议）与 Tello 无人机通信，
对外通过 TCP（127.0.0.1:9999）暴露文本命令接口。
单线程 + Lock 确保命令串行执行，守护线程负责心跳。
"""

import json
import logging
import signal
import socket
import sys
import time
from threading import Lock, Thread

from djitellopy import Tello

logging.basicConfig(level=logging.INFO, format='[controller] %(message)s')
logger = logging.getLogger(__name__)

TCP_HOST = '127.0.0.1'
TCP_PORT = 9999


class TelloController:
    def __init__(self):
        self.tello = Tello()
        self._lock = Lock()
        self._running = False
        self._last_cmd_time = time.time()
        self._heartbeat_interval = 10  # 秒

        # --- 录像相关 ---
        self._recording = False
        self._recorder_thread = None
        self._recording_filename = None
        self._frame_read = None

        # --- YOLO 相关 ---
        self._yolo_model = None
        self._tracked_target = None  # IoU 跟踪锁定目标 {'bbox': [x1,y1,x2,y2], 'id': int}

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------

    def connect(self):
        self.tello.connect()
        logger.info(f"已连接无人机，电量: {self.tello.get_battery()}%")

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    def _heartbeat_loop(self):
        """每 10 秒检查一次，空闲超时发送 rc_control(0,0,0,0)"""
        while self._running:
            time.sleep(self._heartbeat_interval)
            if not self._running:
                break
            with self._lock:
                elapsed = time.time() - self._last_cmd_time
            if elapsed >= self._heartbeat_interval:
                with self._lock:
                    try:
                        self.tello.send_rc_control(0, 0, 0, 0)
                        logger.debug("心跳发送")
                    except Exception as e:
                        logger.warning(f"心跳异常: {e}")

    # ------------------------------------------------------------------
    # 命令执行入口
    # ------------------------------------------------------------------

    def execute(self, cmd: str) -> str:
        """解析并执行命令，返回响应字符串"""
        with self._lock:
            self._last_cmd_time = time.time()
            try:
                return self._dispatch(cmd.strip())
            except Exception as e:
                return f"error: {e}"

    def _dispatch(self, cmd: str) -> str:
        parts = cmd.split()
        if not parts:
            return "error: empty command"

        module = parts[0]
        action = parts[1] if len(parts) > 1 else ""

        if module == "flight":
            return self._handle_flight(action, parts[2:])
        elif module == "led":
            return self._handle_led(action, parts[2:])
        elif module == "matrix":
            return self._handle_matrix(action, parts[2:])
        elif module == "sensor":
            return self._handle_sensor(action)
        elif module == "vision":
            return self._handle_vision(action, parts[2:])
        elif module == "yolo":
            # 格式: yolo <action> [--model seg|pose]
            model_type = "pose"  # 默认
            remaining = parts[1:]
            for i, p in enumerate(remaining):
                if p == "--model" and i + 1 < len(remaining):
                    model_type = remaining[i + 1]
                    break
            return self._handle_yolo(action, model_type)
        elif module == "mission_pad":
            return self._handle_mission_pad(action, parts[2:])
        else:
            return f"error: unknown module '{module}'"

    # ------------------------------------------------------------------
    # 飞行模块
    # ------------------------------------------------------------------

    def _handle_flight(self, action, args):
        if action == "takeoff":
            self.tello.takeoff()
            return "ok"
        elif action == "land":
            self.tello.land()
            return "ok"
        elif action == "move":
            if len(args) < 2:
                return "error: missing required arguments"
            direction = args[0]
            dist = int(args[1])
            moves = {
                'f': lambda: self.tello.move_forward(dist),
                'b': lambda: self.tello.move_back(dist),
                'l': lambda: self.tello.move_left(dist),
                'r': lambda: self.tello.move_right(dist),
                'u': lambda: self.tello.move_up(dist),
                'd': lambda: self.tello.move_down(dist),
            }
            if direction not in moves:
                return f"error: invalid direction '{direction}'"
            moves[direction]()
            return "ok"
        elif action == "rotate":
            if len(args) < 2:
                return "error: missing required arguments"
            direction = args[0]
            deg = int(args[1])
            if direction == "cw":
                self.tello.rotate_clockwise(deg)
            elif direction == "ccw":
                self.tello.rotate_counter_clockwise(deg)
            else:
                return f"error: invalid rotate direction '{direction}'"
            return "ok"
        elif action == "rc":
            lr = int(args[0]) if len(args) > 0 else 0
            fb = int(args[1]) if len(args) > 1 else 0
            ud = int(args[2]) if len(args) > 2 else 0
            yaw = int(args[3]) if len(args) > 3 else 0
            self.tello.send_rc_control(lr, fb, ud, yaw)
            return "ok"
        else:
            return f"error: unknown flight action '{action}'"

    # ------------------------------------------------------------------
    # LED 模块
    # ------------------------------------------------------------------

    def _handle_led(self, action, args):
        if action == "solid":
            if len(args) < 3:
                return "error: missing required arguments"
            r, g, b = int(args[0]), int(args[1]), int(args[2])
            self.tello.send_expansion_command(f"led {r} {g} {b}")
        elif action == "breathe":
            if len(args) < 4:
                return "error: missing required arguments"
            freq = float(args[0])
            r, g, b = int(args[1]), int(args[2]), int(args[3])
            self.tello.send_expansion_command(f"led br {freq} {r} {g} {b}")
        elif action == "blink":
            if len(args) < 7:
                return "error: missing required arguments"
            freq = float(args[0])
            r1, g1, b1 = int(args[1]), int(args[2]), int(args[3])
            r2, g2, b2 = int(args[4]), int(args[5]), int(args[6])
            self.tello.send_expansion_command(
                f"led bl {freq} {r1} {g1} {b1} {r2} {g2} {b2}"
            )
        elif action == "off":
            self.tello.send_expansion_command("led 0 0 0")
        else:
            return f"error: unknown led action '{action}'"
        return "ok"

    # ------------------------------------------------------------------
    # 点阵屏模块
    # ------------------------------------------------------------------

    def _handle_matrix(self, action, args):
        if action == "scroll":
            if len(args) < 4:
                return "error: missing required arguments"
            direction = args[0]
            color = args[1]
            freq = float(args[2])
            text = " ".join(args[3:])
            self.tello.send_expansion_command(
                f"mled {direction} {color} {freq} {text}"
            )
        elif action == "static":
            if len(args) < 2:
                return "error: missing required arguments"
            color = args[0]
            text = " ".join(args[1:])
            self.tello.send_expansion_command(f"mled s {color} {text}")
        elif action == "off":
            self.tello.send_expansion_command("mled s b ")
        else:
            return f"error: unknown matrix action '{action}'"
        return "ok"

    # ------------------------------------------------------------------
    # 传感器模块
    # ------------------------------------------------------------------

    def _handle_sensor(self, action):
        if action == "battery":
            return str(self.tello.get_battery())
        elif action == "tof":
            return str(self.tello.send_read_command("EXT tof?"))
        elif action == "attitude":
            p = self.tello.get_pitch()
            r = self.tello.get_roll()
            y = self.tello.get_yaw()
            return f"{p} {r} {y}"
        elif action == "acceleration":
            ax = self.tello.get_acceleration_x()
            ay = self.tello.get_acceleration_y()
            az = self.tello.get_acceleration_z()
            return f"{ax} {ay} {az}"
        elif action == "height":
            return str(self.tello.get_height())
        elif action == "flight_time":
            return str(self.tello.get_flight_time())
        elif action == "barometer":
            return str(self.tello.get_barometer())
        else:
            return f"error: unknown sensor action '{action}'"

    # ------------------------------------------------------------------
    # 视觉模块（含录像）
    # ------------------------------------------------------------------

    def _handle_vision(self, action, args):
        import cv2
        import os
        from datetime import datetime

        # 确保 images/ 和 videos/ 目录存在
        os.makedirs("images", exist_ok=True)
        os.makedirs("videos", exist_ok=True)

        if action == "stream_on":
            self.tello.streamon()
            self._frame_read = self.tello.get_frame_read()
        elif action == "stream_off":
            self.tello.streamoff()
            self._frame_read = None
        elif action == "photo":
            name = args[0] if args else ""
            if not name:
                name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            path = os.path.join("images", name)
            cv2.imwrite(path, self._frame_read.frame)
        elif action == "record_start":
            name = args[0] if args else ""
            if self._recording:
                return "error: already recording"
            if not name:
                name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi"
            self._recording_filename = os.path.join("videos", name)
            self._recording = True
            self._recorder_thread = Thread(target=self._record_loop, daemon=True)
            self._recorder_thread.start()
        elif action == "record_stop":
            self._recording = False
            if self._recorder_thread:
                self._recorder_thread.join(timeout=3)
            self._recorder_thread = None
        else:
            return f"error: unknown vision action '{action}'"
        return "ok"

    def _record_loop(self):
        import cv2

        try:
            if self._frame_read is None:
                return
            h, w, _ = self._frame_read.frame.shape
            out = cv2.VideoWriter(
                self._recording_filename,
                cv2.VideoWriter_fourcc(*'XVID'),
                30, (w, h),
            )
            while self._recording:
                out.write(self._frame_read.frame)
                time.sleep(0.01)
            out.release()
        except Exception as e:
            logger.error(f"录制异常: {e}")

    # ------------------------------------------------------------------
    # YOLO 模块
    # ------------------------------------------------------------------

    def _ensure_yolo_model(self, model_type="pose"):
        if self._yolo_model is not None:
            return
        from ultralytics import YOLO
        model_path = f"models/yolo26n-{model_type}.pt"
        logger.info(f"加载 YOLO 模型: {model_path}")
        self._yolo_model = YOLO(model_path)

    def _handle_yolo(self, action, model_type="pose"):
        self._ensure_yolo_model(model_type)
        if self._frame_read is None:
            return "error: stream not started"

        frame = self._frame_read.frame
        fh, fw = frame.shape[:2]
        frame_cx, frame_cy = fw // 2, fh // 2

        if action == "detect":
            results = self._yolo_model(frame, classes=[0], verbose=False)

            if model_type == "seg":
                return self._yolo_detect_seg(results, frame_cx, frame_cy)
            else:
                return self._yolo_detect_pose(results, frame_cx, frame_cy)

        elif action == "count":
            results = self._yolo_model(frame, classes=[0], verbose=False)
            count = sum(1 for r in results for _ in r.boxes)
            return str(count)

        return "error: unknown yolo action"

    def _yolo_detect_seg(self, results, frame_cx, frame_cy):
        """分割模式检测：返回 area（掩码面积），IoU 跟踪锁定"""
        import cv2

        result = results[0]
        all_detections = []

        if result.boxes is not None:
            boxes_data = result.boxes.data.cpu().numpy()
            masks_available = result.masks is not None and result.masks.xy

            for i, box in enumerate(boxes_data):
                x1, y1, x2, y2 = map(int, box[:4])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                area = 0
                if masks_available and i < len(result.masks.xy):
                    contour = result.masks.xy[i]
                    if len(contour) > 0:
                        area = float(cv2.contourArea(contour))
                all_detections.append({
                    'bbox': [x1, y1, x2, y2],
                    'center': [cx, cy],
                    'area': area,
                })

        if not all_detections:
            self._tracked_target = None
            return json.dumps({}, ensure_ascii=False)

        # IoU 跟踪
        target = self._iou_match(all_detections, frame_cx, frame_cy)
        if target is None:
            return json.dumps({}, ensure_ascii=False)

        return json.dumps(target, ensure_ascii=False)

    def _yolo_detect_pose(self, results, frame_cx, frame_cy):
        """姿态模式检测：返回 torso_height + has_hips（COCO 关键点），IoU 跟踪锁定"""
        result = results[0]
        all_detections = []

        if result.keypoints is not None and result.boxes is not None:
            keypoints = result.keypoints.data.cpu().numpy()
            boxes_data = result.boxes.data.cpu().numpy()

            for i, box in enumerate(boxes_data):
                x1, y1, x2, y2 = map(int, box[:4])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                kpts = keypoints[i]
                l_shoulder = kpts[5]
                r_shoulder = kpts[6]
                l_hip = kpts[11]
                r_hip = kpts[12]

                torso_height = 0
                has_hips = False

                if l_shoulder[2] > 0.5 and r_shoulder[2] > 0.5:
                    shoulder_cx = (l_shoulder[0] + r_shoulder[0]) / 2
                    shoulder_cy = (l_shoulder[1] + r_shoulder[1]) / 2
                    center = [int(shoulder_cx), int(shoulder_cy)]

                    if l_hip[2] > 0.5 and r_hip[2] > 0.5:
                        hip_cx = (l_hip[0] + r_hip[0]) / 2
                        hip_cy = (l_hip[1] + r_hip[1]) / 2
                        torso_height = float(
                            ((shoulder_cx - hip_cx) ** 2 + (shoulder_cy - hip_cy) ** 2) ** 0.5
                        )
                        has_hips = True
                else:
                    center = [cx, cy]

                all_detections.append({
                    'bbox': [x1, y1, x2, y2],
                    'center': center,
                    'torso_height': torso_height,
                    'has_hips': has_hips,
                })

        if not all_detections:
            self._tracked_target = None
            return json.dumps({}, ensure_ascii=False)

        target = self._iou_match(all_detections, frame_cx, frame_cy)
        if target is None:
            return json.dumps({}, ensure_ascii=False)

        return json.dumps(target, ensure_ascii=False)

    def _iou_match(self, detections, frame_cx, frame_cy):
        """IoU 匹配：首次锁定离中心最近的人，后续跟踪同一人，丢失返回 None"""
        if self._tracked_target is None:
            # 首次：选离画面中心最近的人
            best = min(detections, key=lambda d:
                (d['center'][0] - frame_cx) ** 2 + (d['center'][1] - frame_cy) ** 2)
            self._tracked_target = {'bbox': best['bbox'], 'id': 0}
            return best

        # 已有锁定目标：计算 IoU
        tx1, ty1, tx2, ty2 = self._tracked_target['bbox']
        t_area = (tx2 - tx1) * (ty2 - ty1)
        if t_area <= 0:
            self._tracked_target = None
            return None

        best_iou = 0.3  # 最小 IoU 阈值
        best_det = None
        for d in detections:
            dx1, dy1, dx2, dy2 = d['bbox']
            ix1 = max(tx1, dx1)
            iy1 = max(ty1, dy1)
            ix2 = min(tx2, dx2)
            iy2 = min(ty2, dy2)
            if ix1 < ix2 and iy1 < iy2:
                i_area = (ix2 - ix1) * (iy2 - iy1)
                union = t_area + (dx2 - dx1) * (dy2 - dy1) - i_area
                iou = i_area / union if union > 0 else 0
                if iou > best_iou:
                    best_iou = iou
                    best_det = d

        if best_det is None:
            self._tracked_target = None
            logger.info("IoU 跟踪丢失，无人机悬停等待指令")
            return None

        self._tracked_target['bbox'] = best_det['bbox']
        return best_det

    # ------------------------------------------------------------------
    # 挑战卡模块
    # ------------------------------------------------------------------

    def _handle_mission_pad(self, action, args):
        if action == "enable":
            self.tello.enable_mission_pads()
            self.tello.set_mission_pad_detection_direction(0)
        elif action == "disable":
            self.tello.disable_mission_pads()
        elif action == "id":
            return str(self.tello.get_mission_pad_id())
        elif action == "xyz":
            x = self.tello.get_mission_pad_distance_x()
            y = self.tello.get_mission_pad_distance_y()
            z = self.tello.get_mission_pad_distance_z()
            return f"{x} {y} {z}"
        elif action == "fly":
            if len(args) < 2 or args[0] != "--id":
                return "error: missing required arguments"
            pad_id = int(args[1])
            self.tello.go_xyz_speed_mid(0, 0, 60, 30, pad_id)
        else:
            return f"error: unknown mission_pad action '{action}'"
        return "ok"

    # ------------------------------------------------------------------
    # TCP 服务器
    # ------------------------------------------------------------------

    def start_server(self):
        self._running = True
        self.connect()
        Thread(target=self._heartbeat_loop, daemon=True).start()
        logger.info("心跳守护线程已启动")

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((TCP_HOST, TCP_PORT))
        server.listen(5)
        logger.info(f"TCP 服务器监听 {TCP_HOST}:{TCP_PORT}")

        def cleanup():
            logger.info("正在关闭...")
            self._running = False
            try:
                self.tello.land()
            except Exception:
                pass
            try:
                self.tello.end()
            except Exception:
                pass
            server.close()
            logger.info("已关闭")
            sys.exit(0)

        signal.signal(signal.SIGINT, lambda s, f: cleanup())
        signal.signal(signal.SIGTERM, lambda s, f: cleanup())

        while self._running:
            try:
                server.settimeout(1.0)
                try:
                    client, addr = server.accept()
                except socket.timeout:
                    continue
                data = client.recv(4096).decode().strip()
                if data:
                    logger.info(f"命令: {data}")
                    response = self.execute(data)
                    client.send((response + '\n').encode())
                client.close()
            except Exception as e:
                logger.error(f"服务异常: {e}")


if __name__ == '__main__':
    controller = TelloController()
    controller.start_server()
