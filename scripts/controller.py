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
from threading import Lock, Thread, Event
from concurrent.futures import ThreadPoolExecutor

from djitellopy import Tello

logging.basicConfig(level=logging.INFO, format='[controller] %(message)s')
logger = logging.getLogger(__name__)

TCP_HOST = '127.0.0.1'
TCP_PORT = 9999


class TelloController:
    def __init__(self):
        self.tello = Tello()
        self._flight_lock = Lock()   # 序列化所有 self.tello.* UDP 通信
        self._model_lock = Lock()    # 序列化 YOLO 模型推理
        self._state_lock = Lock()    # 保护共享状态变量（最内层锁）
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
        self._loaded_model_type = None
        self._follow_target_id = None  # CLI yolo detect 的跟踪目标 ID（track ID，int）

        # --- Task 相关 ---
        self._follow_stop = Event()    # 通知 task follow 线程停止
        self._follow_thread = None     # task follow 线程引用

        # --- task status 查询共享状态（_state_lock 保护） ---
        self._follow_status = {
            "running": False, "model": "", "elapsed": 0.0, "duration": 0,
            "track_id": None, "rc_speed": {"lr": 0, "fb": 0, "ud": 0, "yaw": 0},
            "tof_distance": 0, "target": None
        }

        # --- 预览相关（_state_lock 保护） ---
        self._preview_stops = {}       # str -> Event
        self._preview_threads = {}     # str -> Thread

        # --- YOLO 预览共享状态（_state_lock 保护） ---
        self._preview_yolo_stop = Event()
        self._preview_yolo_thread = None
        self._yolo_shared = {
            "model_type": "pose",
            "detections": [],
            "kpts_data": None,
            "masks_xy": None,
            "frame_cx": 480,
            "frame_cy": 360,
            "locked_id": None,
            "fresh": False,
        }

        # --- 挑战卡预览共享状态（_state_lock 保护） ---
        self._preview_pad_stop = Event()
        self._preview_pad_thread = None
        self._pad_shared = {
            "id": -1, "x": 0, "y": 0, "z": 0,
            "active": False,
        }

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------

    def connect(self):
        self.tello.connect()
        logger.info(f"已连接无人机，电量: {self.tello.get_battery()}%")

    def _update_cmd_time(self):
        """更新最后一次命令时间（用于心跳判断），在 _state_lock 保护下调用"""
        with self._state_lock:
            self._last_cmd_time = time.time()

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    def _heartbeat_loop(self):
        """每 10 秒检查一次，空闲超时发送 rc_control(0,0,0,0)"""
        while self._running:
            time.sleep(self._heartbeat_interval)
            if not self._running:
                break
            with self._state_lock:
                elapsed = time.time() - self._last_cmd_time
            if elapsed >= self._heartbeat_interval:
                with self._flight_lock:
                    try:
                        self.tello.send_rc_control(0, 0, 0, 0)
                        logger.debug("心跳发送")
                    except Exception as e:
                        logger.warning(f"心跳异常: {e}")

    # ------------------------------------------------------------------
    # 命令执行入口
    # ------------------------------------------------------------------

    def execute(self, cmd: str) -> str:
        """解析并执行命令，返回响应字符串，锁由 _dispatch 内部分派。"""
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
        remaining = parts[2:]

        # ---- 需飞行锁：与无人机 UDP 通信 ----
        if module == "flight":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_flight(action, remaining)
        elif module == "led":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_led(action, remaining)
        elif module == "matrix":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_matrix(action, remaining)
        elif module == "sensor":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_sensor(action)
        elif module == "mission_pad":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_mission_pad(action, remaining)
        elif module == "vision":
            if action in ("stream_on", "stream_off"):
                with self._flight_lock:
                    self._update_cmd_time()
                    return self._handle_vision(action, remaining)
            else:
                # photo, record_start, record_stop: 只读帧或写磁盘，不需飞行锁
                return self._handle_vision(action, remaining)

        # ---- 需模型锁：YOLO 推理 ----
        elif module == "yolo":
            model_type = "pose"
            remaining_for_yolo = parts[1:]
            for i, p in enumerate(remaining_for_yolo):
                if p == "--model" and i + 1 < len(remaining_for_yolo):
                    model_type = remaining_for_yolo[i + 1]
                    break
            with self._model_lock:
                return self._handle_yolo(action, model_type)

        # ---- 任务模块：内部管理锁 ----
        elif module == "task":
            return self._handle_task(action, remaining)

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

        os.makedirs("images", exist_ok=True)
        os.makedirs("videos", exist_ok=True)

        if action == "stream_on":
            # 调用者在 _flight_lock 下
            self.tello.streamon()
            with self._state_lock:
                self._frame_read = self.tello.get_frame_read()
        elif action == "stream_off":
            # 调用者在 _flight_lock 下
            self.tello.streamoff()
            with self._state_lock:
                self._frame_read = None
        elif action == "photo":
            name = args[0] if args else ""
            if not name:
                name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            path = os.path.join("images", name)
            with self._state_lock:
                fr = self._frame_read
            if fr is None:
                return "error: stream not started"
            cv2.imwrite(path, fr.frame)
        elif action == "record_start":
            name = args[0] if args else ""
            with self._state_lock:
                if self._recording:
                    return "error: already recording"
                if not name:
                    name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi"
                self._recording_filename = os.path.join("videos", name)
                self._recording = True
            self._recorder_thread = Thread(target=self._record_loop, daemon=True)
            self._recorder_thread.start()
        elif action == "record_stop":
            with self._state_lock:
                self._recording = False
            rt = self._recorder_thread
            if rt:
                rt.join(timeout=3)
            with self._state_lock:
                self._recorder_thread = None
        else:
            return f"error: unknown vision action '{action}'"
        return "ok"

    def _record_loop(self):
        import cv2

        try:
            with self._state_lock:
                fr = self._frame_read
                filename = self._recording_filename
            if fr is None:
                return
            h, w, _ = fr.frame.shape
            out = cv2.VideoWriter(
                filename, cv2.VideoWriter_fourcc(*'XVID'), 30, (w, h),
            )
            while True:
                with self._state_lock:
                    if not self._recording:
                        break
                    fr = self._frame_read
                if fr is not None:
                    out.write(fr.frame)
                time.sleep(0.01)
        except Exception as e:
            logger.error(f"录制异常: {e}")
        finally:
            try:
                out.release()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 帧处理辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _process_forward_frame(frame):
        import cv2
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)  # 如不需要可注释
        return frame

    @staticmethod
    def _process_downward_frame(frame):
        import cv2
        frame = frame[:240, :]
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        return frame

    # ------------------------------------------------------------------
    # 预览线程
    # ------------------------------------------------------------------

    def _preview_clean_loop(self, direction):
        import cv2

        window_name = f"Tello {direction.upper()}"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        process = (self._process_forward_frame if direction == "forward"
                   else self._process_downward_frame)
        dir_label = "FWD" if direction == "forward" else "DOWN"
        stop_event = self._preview_stops[direction]
        battery = "??"
        frame_count = 0

        while not stop_event.is_set():
            with self._state_lock:
                fr = self._frame_read
            if fr is None or fr.frame is None:
                time.sleep(0.05)
                continue

            frame = fr.frame.copy()
            frame = process(frame)

            frame_count += 1
            if frame_count % 30 == 0:
                with self._flight_lock:
                    try:
                        battery = str(self.tello.get_battery())
                    except Exception:
                        pass

            h, w = frame.shape[:2]
            bar_h = 30
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
            frame = cv2.addWeighted(frame, 0.7, overlay, 0.3, 0)
            text = f"{dir_label}  Bat: {battery}%"
            cv2.putText(frame, text, (5, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1)

            cv2.imshow(window_name, frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cv2.destroyWindow(window_name)
        with self._state_lock:
            self._preview_threads.pop(direction, None)

    # ------------------------------------------------------------------
    # YOLO 模块
    # ------------------------------------------------------------------

    def _ensure_yolo_model(self, model_type="pose"):
        if self._yolo_model is not None and getattr(self, '_loaded_model_type', None) == model_type:
            return
        from ultralytics import YOLO
        model_path = f"models/yolo26n-{model_type}.pt"
        logger.info(f"加载 YOLO 模型: {model_path}")
        self._yolo_model = YOLO(model_path)
        self._loaded_model_type = model_type

    def _parse_track_detections(self, result, model_type):
        """从 model.track() 结果中统一提取检测列表（含 track_id）。
        供 _handle_yolo 和 _task_follow_loop 复用。
        """
        import cv2

        detections = []
        boxes = result.boxes
        if boxes is None:
            return detections

        boxes_data = boxes.data.cpu().numpy()
        track_ids = boxes.id  # tensor(N,) 或 None

        if model_type == "seg":
            masks_available = result.masks is not None and result.masks.xy
            for i, box in enumerate(boxes_data):
                x1, y1, x2, y2 = map(int, box[:4])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                tid = int(track_ids[i].item()) if track_ids is not None else -1
                area = 0.0
                if masks_available and i < len(result.masks.xy):
                    contour = result.masks.xy[i]
                    if len(contour) > 0:
                        area = float(cv2.contourArea(contour))
                detections.append({
                    'bbox': [x1, y1, x2, y2],
                    'center': [cx, cy],
                    'area': area,
                    'track_id': tid,
                })
        else:
            keypoints = result.keypoints
            if keypoints is not None:
                kpts_data = keypoints.data.cpu().numpy()
                for i, box in enumerate(boxes_data):
                    x1, y1, x2, y2 = map(int, box[:4])
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    tid = int(track_ids[i].item()) if track_ids is not None else -1

                    kpts = kpts_data[i]
                    l_shoulder = kpts[5]
                    r_shoulder = kpts[6]
                    l_hip = kpts[11]
                    r_hip = kpts[12]

                    torso_height = 0.0
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

                    detections.append({
                        'bbox': [x1, y1, x2, y2],
                        'center': center,
                        'torso_height': torso_height,
                        'has_hips': has_hips,
                        'track_id': tid,
                    })

        return detections

    def _track_match(self, detections, frame_cx, frame_cy):
        """通过 ultralytics track ID 匹配目标。
        首次调用选离画面中心最近的人并记录 track_id，
        后续帧通过 track_id 精确匹配同一人。
        跟踪丢失返回 None。
        """
        if self._follow_target_id is not None:
            for d in detections:
                if d['track_id'] == self._follow_target_id:
                    return d
            self._follow_target_id = None
            logger.info("跟踪目标丢失（track ID 不再出现）")
            return None

        if not detections:
            return None

        best = min(detections, key=lambda d:
            (d['center'][0] - frame_cx) ** 2 + (d['center'][1] - frame_cy) ** 2)
        self._follow_target_id = best['track_id']
        logger.info(f"锁定新跟踪目标: track_id={self._follow_target_id}")
        return best

    def _handle_yolo(self, action, model_type="pose"):
        self._ensure_yolo_model(model_type)

        with self._state_lock:
            fr = self._frame_read
        if fr is None:
            return "error: stream not started"

        frame = fr.frame
        fh, fw = frame.shape[:2]
        frame_cx, frame_cy = fw // 2, fh // 2

        if action == "detect":
            results = self._yolo_model.track(
                frame, classes=[0], persist=True,
                tracker='botsort.yaml', verbose=False
            )
            result = results[0]
            detections = self._parse_track_detections(result, model_type)

            if not detections:
                self._follow_target_id = None
                return json.dumps({}, ensure_ascii=False)

            target = self._track_match(detections, frame_cx, frame_cy)
            if target is None:
                return json.dumps({}, ensure_ascii=False)

            return json.dumps(target, ensure_ascii=False)

        elif action == "count":
            results = self._yolo_model(frame, classes=[0], verbose=False)
            count = sum(1 for r in results for _ in r.boxes)
            return str(count)

        return "error: unknown yolo action"

    # ------------------------------------------------------------------
    # 任务模块（闭环控制）
    # ------------------------------------------------------------------

    def _handle_task(self, action, args):
        if action == "follow":
            return self._start_task_follow(args)
        elif action == "stop":
            with self._state_lock:
                self._follow_stop.set()
            return "ok"
        elif action == "status":
            with self._state_lock:
                status = dict(self._follow_status)
            return json.dumps(status, ensure_ascii=False)
        else:
            return f"error: unknown task action '{action}'"

    def _start_task_follow(self, args):
        """解析参数，检查冲突，启动跟踪线程并阻塞等待完成"""
        model_type = "pose"
        duration = 60
        kp_yaw = 0.2
        kp_ud = 0.3
        fb_speed = 15
        dist_low = None
        dist_high = None

        i = 0
        while i < len(args):
            if args[i] == "--model" and i + 1 < len(args):
                model_type = args[i + 1]
                i += 2
            elif args[i] == "--duration" and i + 1 < len(args):
                duration = int(args[i + 1])
                i += 2
            elif args[i] == "--kp-yaw" and i + 1 < len(args):
                kp_yaw = float(args[i + 1])
                i += 2
            elif args[i] == "--kp-ud" and i + 1 < len(args):
                kp_ud = float(args[i + 1])
                i += 2
            elif args[i] == "--fb-speed" and i + 1 < len(args):
                fb_speed = int(args[i + 1])
                i += 2
            elif args[i] == "--dist-low" and i + 1 < len(args):
                dist_low = float(args[i + 1])
                i += 2
            elif args[i] == "--dist-high" and i + 1 < len(args):
                dist_high = float(args[i + 1])
                i += 2
            else:
                i += 1

        if model_type not in ("pose", "seg"):
            return f"error: unknown model type '{model_type}'"

        if dist_low is None:
            dist_low = 200 if model_type == "pose" else 100000
        if dist_high is None:
            dist_high = 250 if model_type == "pose" else 150000

        with self._state_lock:
            if self._follow_thread is not None and self._follow_thread.is_alive():
                return "error: task follow already running"
            self._follow_stop.clear()
            self._follow_thread = Thread(
                target=self._task_follow_loop,
                args=(model_type, duration, kp_yaw, kp_ud, fb_speed, dist_low, dist_high),
                daemon=True, name="task-follow"
            )
            self._follow_thread.start()
            ft = self._follow_thread

        ft.join()
        return "ok"

    def _task_follow_loop(self, model_type, duration, kp_yaw, kp_ud, fb_speed_val,
                           dist_low, dist_high):
        """YOLO track + P 控制闭环。在独立 daemon 线程中运行。"""
        start_time = time.time()
        local_track_id = None
        frame_cx, frame_cy = 480, 360

        with self._state_lock:
            self._follow_status.update({
                "running": True, "model": model_type, "elapsed": 0.0,
                "duration": duration, "track_id": None,
                "rc_speed": {"lr": 0, "fb": 0, "ud": 0, "yaw": 0},
                "tof_distance": 0, "target": None
            })

        with self._model_lock:
            self._ensure_yolo_model(model_type)

        logger.info(f"task follow 开始: model={model_type}, duration={duration}s")

        try:
            while (time.time() - start_time) < duration:
                if self._follow_stop.is_set():
                    logger.info("task follow 收到外部停止信号")
                    break

                elapsed = time.time() - start_time

                # ---- TOF 紧急检测 ----
                tof_dist = -1
                with self._flight_lock:
                    self._update_cmd_time()
                    try:
                        resp = self.tello.send_read_command("EXT tof?")
                        tof_dist = int(resp.strip()) if resp.strip() else -1
                    except Exception:
                        pass

                if 100 <= tof_dist < 500:
                    logger.warning(f"TOF 紧急停止: 距离={tof_dist}cm")
                    break

                # ---- 获取帧 ----
                with self._state_lock:
                    fr = self._frame_read
                if fr is None:
                    time.sleep(0.05)
                    continue

                frame = fr.frame
                fh, fw = frame.shape[:2]
                frame_cx, frame_cy = fw // 2, fh // 2

                # ---- YOLO track 推理 ----
                with self._model_lock:
                    results = self._yolo_model.track(
                        frame, classes=[0], persist=True,
                        tracker='botsort.yaml', verbose=False
                    )

                detections = self._parse_track_detections(results[0], model_type)

                # ---- track ID 匹配 ----
                target = None
                if local_track_id is not None:
                    target = next(
                        (d for d in detections if d['track_id'] == local_track_id), None
                    )
                    if target is None:
                        local_track_id = None
                        logger.info("task follow: 跟踪目标丢失")

                if local_track_id is None and detections:
                    target = min(detections, key=lambda d:
                        (d['center'][0] - frame_cx) ** 2 + (d['center'][1] - frame_cy) ** 2)
                    local_track_id = target['track_id']
                    logger.info(f"task follow: 锁定目标 track_id={local_track_id}")

                # ---- P 控制计算 + 发送 RC ----
                if target:
                    lr, fb, ud, yaw = self._compute_p_controls(
                        target, model_type, frame_cx, frame_cy,
                        kp_yaw, kp_ud, fb_speed_val, dist_low, dist_high
                    )
                else:
                    lr, fb, ud, yaw = 0, 0, 0, 0

                with self._flight_lock:
                    self._update_cmd_time()
                    self.tello.send_rc_control(lr, fb, ud, yaw)

                # ---- LED 矩阵显示 ----
                with self._flight_lock:
                    if model_type == "seg":
                        area_k = int(target.get('area', 0) // 1000) if target else 0
                        self.tello.send_expansion_command(f"mled s r {area_k}k")
                    else:
                        h = int(target.get('torso_height', 0)) if target else 0
                        self.tello.send_expansion_command(f"mled s r {h}h")

                # ---- 更新共享状态（供 task status 查询） ----
                with self._state_lock:
                    self._follow_status.update({
                        "elapsed": round(elapsed, 1),
                        "track_id": local_track_id,
                        "rc_speed": {"lr": lr, "fb": fb, "ud": ud, "yaw": yaw},
                        "tof_distance": tof_dist,
                        "target": target
                    })

                time.sleep(0.05)

        finally:
            with self._flight_lock:
                try:
                    self.tello.send_rc_control(0, 0, 0, 0)
                except Exception:
                    pass
            with self._state_lock:
                self._follow_status["running"] = False
            logger.info("task follow 结束，无人机已悬停")

    def _compute_p_controls(self, target, model_type, frame_cx, frame_cy,
                             kp_yaw, kp_ud, fb_speed_val, dist_low, dist_high):
        """统一 P 控制器：pose 用 torso_height 控制前后距离，seg 用 area。"""
        cx, cy = target['center']

        error_x = cx - frame_cx
        yaw = max(-50, min(50, int(kp_yaw * error_x)))

        error_y = frame_cy - cy
        ud = max(-50, min(50, int(kp_ud * error_y)))

        if model_type == "pose":
            if target.get('has_hips', False):
                th = target['torso_height']
                if th < dist_low:
                    fb = fb_speed_val
                elif th > dist_high:
                    fb = -fb_speed_val
                else:
                    fb = 0
            else:
                fb = 0
        else:
            area = target.get('area', dist_low + 1)
            if area < dist_low:
                fb = fb_speed_val
            elif area > dist_high:
                fb = -fb_speed_val
            else:
                fb = 0

        return (0, fb, ud, yaw)

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

    def _handle_client(self, client: socket.socket, data: str):
        """在池线程中处理单个客户端连接"""
        try:
            logger.info(f"命令: {data}")
            response = self.execute(data)
            client.send((response + '\n').encode())
        except Exception as e:
            logger.error(f"处理异常: {e}")
            try:
                client.send((f"error: {e}\n").encode())
            except Exception:
                pass
        finally:
            client.close()

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

        executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cmd")

        def cleanup():
            logger.info("正在关闭...")
            self._running = False
            with self._state_lock:
                self._follow_stop.set()
            executor.shutdown(wait=False)
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
                try:
                    client.settimeout(5.0)
                    data = client.recv(4096).decode().strip()
                except socket.timeout:
                    client.close()
                    continue
                if data:
                    executor.submit(self._handle_client, client, data)
            except Exception as e:
                logger.error(f"服务异常: {e}")


if __name__ == '__main__':
    controller = TelloController()
    controller.start_server()
