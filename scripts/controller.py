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
from collections import deque
from threading import Lock, Thread

import numpy as np
from djitellopy import Tello

logging.basicConfig(level=logging.INFO, format='[controller] %(message)s')
logger = logging.getLogger(__name__)

TCP_HOST = '127.0.0.1'
TCP_PORT = 9999


class KalmanBoxTracker:
    """滑动窗口平滑边界框中心点（基于历史均值的卡尔曼替代方案）"""

    def __init__(self):
        self._history: deque = deque(maxlen=5)

    def update(self, cx: float, cy: float) -> np.ndarray:
        self._history.append((cx, cy))
        if len(self._history) >= 3:
            return np.mean(self._history, axis=0)
        return np.array([cx, cy])


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
        self._yolo_tracker = KalmanBoxTracker()

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
            return self._handle_yolo(action)
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
            direction = args[0] if len(args) > 0 else ""
            dist = int(args[1]) if len(args) > 1 else 0
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
            direction = args[0] if len(args) > 0 else ""
            deg = int(args[1]) if len(args) > 1 else 0
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
            r, g, b = int(args[0]), int(args[1]), int(args[2])
            self.tello.send_expansion_command(f"led {r} {g} {b}")
        elif action == "breathe":
            freq = float(args[0])
            r, g, b = int(args[1]), int(args[2]), int(args[3])
            self.tello.send_expansion_command(f"led br {freq} {r} {g} {b}")
        elif action == "blink":
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
            direction = args[0]
            color = args[1]
            freq = float(args[2])
            text = " ".join(args[3:])
            self.tello.send_expansion_command(
                f"mled {direction} {color} {freq} {text}"
            )
        elif action == "static":
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

        if action == "stream_on":
            self.tello.streamon()
            self._frame_read = self.tello.get_frame_read()
        elif action == "stream_off":
            self.tello.streamoff()
        elif action == "photo":
            name = args[0] if args else "photo.jpg"
            cv2.imwrite(name, self._frame_read.frame)
        elif action == "record_start":
            name = args[0] if args else "video.avi"
            if self._recording:
                return "error: already recording"
            self._recording = True
            self._recording_filename = name
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

    # ------------------------------------------------------------------
    # YOLO 模块
    # ------------------------------------------------------------------

    def _ensure_yolo_model(self):
        if self._yolo_model is None:
            from ultralytics import YOLO
            self._yolo_model = YOLO("yolo11n.pt")

    def _handle_yolo(self, action):
        self._ensure_yolo_model()
        if self._frame_read is None:
            return "error: stream not started"

        frame = self._frame_read.frame

        if action == "detect":
            results = self._yolo_model(frame, classes=[0], verbose=False)
            detections = []
            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    conf = float(box.conf[0])
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    detections.append({
                        'bbox': [x1, y1, x2, y2],
                        'center_raw': (cx, cy),
                        'confidence': conf,
                    })

            persons = []
            if detections:
                # 取离画面中心最近的人，用持久化 tracker 平滑其中心点
                fh, fw = frame.shape[:2]
                frame_cx, frame_cy = fw // 2, fh // 2
                best = min(detections, key=lambda d: (
                    (d['center_raw'][0] - frame_cx) ** 2
                    + (d['center_raw'][1] - frame_cy) ** 2
                ))
                best_cx, best_cy = best['center_raw']
                smooth_cx, smooth_cy = self._yolo_tracker.update(best_cx, best_cy)

                for d in detections:
                    if d is best:
                        persons.append({
                            'bbox': d['bbox'],
                            'center': [int(smooth_cx), int(smooth_cy)],
                            'confidence': d['confidence'],
                        })
                    else:
                        raw_cx, raw_cy = d['center_raw']
                        persons.append({
                            'bbox': d['bbox'],
                            'center': [raw_cx, raw_cy],
                            'confidence': d['confidence'],
                        })
            else:
                # 没有检测到人，重置 tracker
                self._yolo_tracker = KalmanBoxTracker()

            return json.dumps(persons, ensure_ascii=False)

        elif action == "count":
            results = self._yolo_model(frame, classes=[0], verbose=False)
            count = sum(1 for r in results for _ in r.boxes)
            return str(count)

        return "error: unknown yolo action"

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
            pad_id = int(args[1]) if len(args) > 1 and args[0] == "--id" else 1
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
