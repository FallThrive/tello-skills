# Tello 无人机控制 Skill 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 Tello 无人机 CLI 控制脚本系统，通过持久化 controller 进程管理连接/心跳/录像，AI 通过 SKILL.md 指引拆解自然语言指令为 CLI 脚本调用序列。

**Architecture:** controller.py 持久进程（TCP 服务器 + DJITelloPy 单例 + 心跳守护线程 + 录像线程），各 CLI 脚本通过 TCP 向 controller 发送文本命令并接收响应。`tasks/` 目录下两个实时闭环脚本直接复用 controller 内部模块实现高频循环。

**Tech Stack:** Python 3.10+, DJITelloPy, OpenCV, PyTorch, ultralytics (YOLO11n), uv

---

### Task 1: Controller 持久进程（TCP 服务器 + DJITelloPy + 心跳）

**Files:**
- Create: `scripts/controller.py`

Controller 是系统核心，持久运行的单进程 TCP 服务器，所有 CLI 脚本通过它操作无人机。

- [ ] **Step 1: 创建 controller.py 骨架——TCP 服务器 + DJITelloPy 连接**

```python
#!/usr/bin/env python3
"""Tello 无人机控制器——持久 TCP 服务器进程"""
import socket
import sys
import time
import signal
import logging
from threading import Thread, Lock
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

    def connect(self):
        self.tello.connect()
        logger.info(f"已连接无人机，电量: {self.tello.get_battery()}%")

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
            self.tello.send_expansion_command(f"led bl {freq} {r1} {g1} {b1} {r2} {g2} {b2}")
        elif action == "off":
            self.tello.send_expansion_command("led 0 0 0")
        else:
            return f"error: unknown led action '{action}'"
        return "ok"

    def _handle_matrix(self, action, args):
        if action == "scroll":
            direction = args[0]
            color = args[1]
            freq = float(args[2])
            text = " ".join(args[3:])
            self.tello.send_expansion_command(f"mled {direction} {color} {freq} {text}")
        elif action == "static":
            color = args[0]
            text = " ".join(args[1:])
            self.tello.send_expansion_command(f"mled s {color} {text}")
        elif action == "off":
            self.tello.send_expansion_command("mled s b ")
        else:
            return f"error: unknown matrix action '{action}'"
        return "ok"

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

    def _handle_vision(self, action, args):
        import cv2
        if action == "stream_on":
            self.tello.streamon()
        elif action == "stream_off":
            self.tello.streamoff()
        elif action == "photo":
            name = args[0] if args else "photo.jpg"
            frame = self.tello.get_frame_read().frame
            cv2.imwrite(name, frame)
        elif action == "record_start":
            name = args[0] if args else "video.avi"
            # 录像线程在主进程中启动（参见后续步骤）
            pass
        elif action == "record_stop":
            pass
        else:
            return f"error: unknown vision action '{action}'"
        return "ok"

    def _handle_yolo(self, action):
        # 后续任务实现
        return f"error: yolo not implemented yet"

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
```

- [ ] **Step 2: 添加录像线程支持**

在 `TelloController.__init__` 中增加录像状态：
```python
self._recording = False
self._recorder_thread = None
self._recording_filename = None
self._frame_read = None
```

在 `_handle_vision` 中完善录像逻辑：
```python
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
        30, (w, h)
    )
    while self._recording:
        out.write(self._frame_read.frame)
        time.sleep(0.01)
    out.release()
```

- [ ] **Step 3: 实现 YOLO 检测处理器 + 卡尔曼滤波**

在 `_handle_yolo` 中实现检测逻辑。卡尔曼滤波器平滑边界框中心：
```python
import numpy as np
from collections import deque

class KalmanBoxTracker:
    """简单卡尔曼滤波跟踪边界框中心点"""
    def __init__(self):
        self._history = deque(maxlen=5)

    def update(self, cx, cy):
        self._history.append((cx, cy))
        if len(self._history) >= 3:
            return np.mean(self._history, axis=0)
        return np.array([cx, cy])

# 在 TelloController.__init__ 中:
self._yolo_model = None
self._kalman_trackers = {}  # ID -> KalmanBoxTracker

def _ensure_yolo_model(self):
    if self._yolo_model is None:
        from ultralytics import YOLO
        self._yolo_model = YOLO("yolo11n.pt")

def _handle_yolo(self, action):
    import cv2
    self._ensure_yolo_model()
    if self._frame_read is None:
        return "error: stream not started"
    frame = self._frame_read.frame

    if action == "detect":
        results = self._yolo_model(frame, classes=[0], verbose=False)  # class 0 = person
        persons = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                # 卡尔曼滤波平滑
                tracker = KalmanBoxTracker()
                smooth_cx, smooth_cy = tracker.update(cx, cy)
                persons.append({
                    'bbox': (x1, y1, x2, y2),
                    'center': (int(smooth_cx), int(smooth_cy)),
                    'confidence': conf
                })
        # 输出 JSON
        import json
        return json.dumps(persons, ensure_ascii=False)

    elif action == "count":
        results = self._yolo_model(frame, classes=[0], verbose=False)
        count = sum(1 for r in results for _ in r.boxes)
        return str(count)

    return "error: unknown yolo action"
```

- [ ] **Step 4: 实现 controller 启动/停止管理脚本**

CLI 脚本需要自动启动 controller（如果未运行）。增加端口检测逻辑：
```python
# scripts/_client.py (被所有 CLI 脚本导入)
import socket

TCP_HOST = '127.0.0.1'
TCP_PORT = 9999

def send_command(cmd: str) -> str:
    """向 controller 发送命令并返回响应"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((TCP_HOST, TCP_PORT))
        sock.send(cmd.encode())
        response = sock.recv(4096).decode().strip()
        return response
    except ConnectionRefusedError:
        return "error: controller not running. Start with: uv run scripts/controller.py &"
    finally:
        sock.close()
```

- [ ] **Step 5: Commit**

```bash
git add scripts/controller.py scripts/_client.py
git commit -m "feat: 实现 controller 持久进程（TCP服务器+DJITelloPy+心跳+录像+YOLO）"
```

---

### Task 2: flight.py CLI 脚本

**Files:**
- Create: `scripts/flight.py`
- Create: `tests/test_flight.py`（可选，实机测试为主）

- [ ] **Step 1: 创建 flight.py**

```python
#!/usr/bin/env python3
"""飞行控制 CLI"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 飞行控制')
    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('takeoff', help='起飞')
    sub.add_parser('land', help='降落')

    p_move = sub.add_parser('move', help='距离移动')
    p_move.add_argument('--direction', '-d', required=True,
                        choices=['f', 'b', 'l', 'r', 'u', 'd'],
                        help='方向: f=前 b=后 l=左 r=右 u=上 d=下')
    p_move.add_argument('--dist', type=int, required=True, help='距离(cm)')

    p_rotate = sub.add_parser('rotate', help='旋转')
    p_rotate.add_argument('--direction', '-d', required=True,
                          choices=['cw', 'ccw'], help='cw=顺时针 ccw=逆时针')
    p_rotate.add_argument('--deg', type=int, required=True, help='角度')

    p_rc = sub.add_parser('rc', help='速度控制')
    p_rc.add_argument('--lr', type=int, default=0, help='左右速度(右+左-)')
    p_rc.add_argument('--fb', type=int, default=0, help='前后速度(前+后-)')
    p_rc.add_argument('--ud', type=int, default=0, help='上下速度(上+下-)')
    p_rc.add_argument('--yaw', type=int, default=0, help='偏航速度(顺+逆-)')

    args = parser.parse_args()

    if args.action in ('takeoff', 'land'):
        cmd = f"flight {args.action}"
    elif args.action == 'move':
        cmd = f"flight move {args.direction} {args.dist}"
    elif args.action == 'rotate':
        cmd = f"flight rotate {args.direction} {args.deg}"
    elif args.action == 'rc':
        cmd = f"flight rc {args.lr} {args.fb} {args.ud} {args.yaw}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/flight.py
git commit -m "feat: 添加 flight.py CLI 脚本"
```

---

### Task 3: led.py + matrix.py CLI 脚本

**Files:**
- Create: `scripts/led.py`
- Create: `scripts/matrix.py`

- [ ] **Step 1: 创建 led.py**

```python
#!/usr/bin/env python3
"""LED 彩灯控制 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello LED 控制')
    sub = parser.add_subparsers(dest='action', required=True)

    p_solid = sub.add_parser('solid', help='常亮')
    p_solid.add_argument('--r', type=int, required=True)
    p_solid.add_argument('--g', type=int, required=True)
    p_solid.add_argument('--b', type=int, required=True)

    p_breathe = sub.add_parser('breathe', help='呼吸灯')
    p_breathe.add_argument('--freq', type=float, required=True)
    p_breathe.add_argument('--r', type=int, required=True)
    p_breathe.add_argument('--g', type=int, required=True)
    p_breathe.add_argument('--b', type=int, required=True)

    p_blink = sub.add_parser('blink', help='交替闪烁')
    p_blink.add_argument('--freq', type=float, required=True)
    p_blink.add_argument('--r1', type=int, required=True)
    p_blink.add_argument('--g1', type=int, required=True)
    p_blink.add_argument('--b1', type=int, required=True)
    p_blink.add_argument('--r2', type=int, required=True)
    p_blink.add_argument('--g2', type=int, required=True)
    p_blink.add_argument('--b2', type=int, required=True)

    sub.add_parser('off', help='关闭')

    args = parser.parse_args()

    if args.action == 'solid':
        cmd = f"led solid {args.r} {args.g} {args.b}"
    elif args.action == 'breathe':
        cmd = f"led breathe {args.freq} {args.r} {args.g} {args.b}"
    elif args.action == 'blink':
        cmd = f"led blink {args.freq} {args.r1} {args.g1} {args.b1} {args.r2} {args.g2} {args.b2}"
    elif args.action == 'off':
        cmd = "led off"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 创建 matrix.py**（与 led.py 结构一致，使用 matrix 模块名）

```python
#!/usr/bin/env python3
"""LED 点阵屏控制 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 点阵屏控制')
    sub = parser.add_subparsers(dest='action', required=True)

    p_scroll = sub.add_parser('scroll', help='滚动显示')
    p_scroll.add_argument('--direction', '-d', required=True,
                          choices=['l', 'r', 'u', 'd'])
    p_scroll.add_argument('--color', '-c', required=True,
                          choices=['r', 'b', 'p'])
    p_scroll.add_argument('--freq', type=float, required=True)
    p_scroll.add_argument('--text', '-t', required=True)

    p_static = sub.add_parser('static', help='静态显示')
    p_static.add_argument('--color', '-c', required=True,
                          choices=['r', 'b', 'p'])
    p_static.add_argument('--text', '-t', required=True)

    sub.add_parser('off', help='关闭')

    args = parser.parse_args()

    if args.action == 'scroll':
        cmd = f"matrix scroll {args.direction} {args.color} {args.freq} {args.text}"
    elif args.action == 'static':
        cmd = f"matrix static {args.color} {args.text}"
    elif args.action == 'off':
        cmd = "matrix off"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: Commit**

```bash
git add scripts/led.py scripts/matrix.py
git commit -m "feat: 添加 led.py 和 matrix.py CLI 脚本"
```

---

### Task 4: sensor.py + vision.py CLI 脚本

**Files:**
- Create: `scripts/sensor.py`
- Create: `scripts/vision.py`

- [ ] **Step 1: 创建 sensor.py**

```python
#!/usr/bin/env python3
"""传感器数据 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 传感器')
    sub = parser.add_subparsers(dest='action', required=True)

    for action in ['battery', 'tof', 'attitude', 'acceleration',
                   'height', 'flight_time', 'barometer']:
        sub.add_parser(action, help=f'获取 {action}')

    args = parser.parse_args()
    print(send_command(f"sensor {args.action}"))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 创建 vision.py**

```python
#!/usr/bin/env python3
"""视觉 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 视觉')
    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('stream_on', help='开启视频流')
    sub.add_parser('stream_off', help='关闭视频流')

    p_photo = sub.add_parser('photo', help='拍照')
    p_photo.add_argument('--name', '-n', default='photo.jpg')

    p_rec_start = sub.add_parser('record_start', help='开始录像')
    p_rec_start.add_argument('--name', '-n', default='video.avi')

    sub.add_parser('record_stop', help='停止录像')

    args = parser.parse_args()

    if args.action == 'photo':
        cmd = f"vision photo {args.name}"
    elif args.action == 'record_start':
        cmd = f"vision record_start {args.name}"
    else:
        cmd = f"vision {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: Commit**

```bash
git add scripts/sensor.py scripts/vision.py
git commit -m "feat: 添加 sensor.py 和 vision.py CLI 脚本"
```

---

### Task 5: yolo.py + mission_pad.py CLI 脚本

**Files:**
- Create: `scripts/yolo.py`
- Create: `scripts/mission_pad.py`

- [ ] **Step 1: 创建 yolo.py**

```python
#!/usr/bin/env python3
"""YOLO 人员检测 CLI（含卡尔曼滤波）"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello YOLO 检测')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('detect', help='检测人员（输出边界框+中心+置信度 JSON）')
    sub.add_parser('count', help='统计人数')
    args = parser.parse_args()
    print(send_command(f"yolo {args.action}"))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 创建 mission_pad.py**

```python
#!/usr/bin/env python3
"""挑战卡 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 挑战卡')
    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('enable', help='开启挑战卡识别')
    sub.add_parser('disable', help='关闭挑战卡识别')
    sub.add_parser('id', help='获取挑战卡 ID')
    sub.add_parser('xyz', help='获取相对坐标')

    p_fly = sub.add_parser('fly', help='飞至挑战卡上方')
    p_fly.add_argument('--id', type=int, required=True, help='挑战卡 ID(1-8)')

    args = parser.parse_args()

    if args.action == 'fly':
        cmd = f"mission_pad fly --id {getattr(args, 'id')}"
    else:
        cmd = f"mission_pad {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: Commit**

```bash
git add scripts/yolo.py scripts/mission_pad.py
git commit -m "feat: 添加 yolo.py 和 mission_pad.py CLI 脚本"
```

---

### Task 6: task_search_pad.py —— 方向搜索挑战卡

**Files:**
- Create: `scripts/tasks/__init__.py`
- Create: `scripts/tasks/task_search_pad.py`

这是第一个实时闭环脚本，直接通过 `_client.send_command()` 与 controller 交互。

- [ ] **Step 1: 创建 task_search_pad.py**

```python
#!/usr/bin/env python3
"""方向搜索挑战卡——小步飞行→等待→检测挑战卡→飞上方+亮灯+屏显"""
import argparse
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='方向搜索挑战卡')
    parser.add_argument('--direction', '-d', required=True,
                        choices=['f', 'b', 'l', 'r'])
    parser.add_argument('--step', type=int, default=30, help='每步距离 cm')
    parser.add_argument('--max-attempts', type=int, default=10, help='最大尝试次数')

    args = parser.parse_args()
    direction = args.direction
    step = args.step
    max_attempts = args.max_attempts

    # 映射方向到 move 参数
    dir_map = {'f': 'f', 'b': 'b', 'l': 'l', 'r': 'r'}

    # 开启挑战卡识别
    send_command("mission_pad enable")

    found = False
    for i in range(max_attempts):
        # 小步飞行
        resp = send_command(f"flight move {dir_map[direction]} {step}")
        if resp.startswith("error"):
            print(f"飞行错误: {resp}")
            break

        # 等待稳定
        time.sleep(0.5)

        # 检测挑战卡
        pad_id = send_command("mission_pad id")
        if pad_id.startswith("error"):
            print(f"检测错误: {pad_id}")
            continue

        try:
            pad_id = int(pad_id)
        except ValueError:
            pad_id = -1

        if pad_id > 0:
            # 发现挑战卡——飞至正上方
            print(f"检测到挑战卡 #{pad_id}")
            send_command(f"mission_pad fly --id {pad_id}")
            # 蓝灯 + 屏显 ID
            send_command(f"led solid 0 0 255")
            send_command(f"matrix static b {pad_id}")
            found = True
            break

        print(f"尝试 {i+1}/{max_attempts}，未检测到挑战卡")

    if not found:
        print(f"超 max_attempts={max_attempts}，未找到挑战卡")
        # 红灯提示
        send_command("led solid 255 0 0")
        time.sleep(1)
        send_command("led off")

    # 关闭挑战卡识别
    send_command("mission_pad disable")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/tasks/__init__.py scripts/tasks/task_search_pad.py
git commit -m "feat: 实现 task_search_pad 方向搜索挑战卡脚本"
```

---

### Task 7: task_follow.py —— YOLO + 比例控制实时跟随

**Files:**
- Create: `scripts/tasks/task_follow.py`
- Reference: `ref/tello_track/modules/tracking_controller.py`

复用 tello_track 的比例控制逻辑，通过 controller 获取 YOLO 检测结果并发送 rc_control。

- [ ] **Step 1: 创建 task_follow.py**

```python
#!/usr/bin/env python3
"""实时人员跟随——YOLO检测→比例控制→rc_control闭环"""
import argparse
import time
import json
import signal
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command
from collections import deque
import numpy as np


class FollowController:
    """比例控制器——参考 ref/tello_track/modules/tracking_controller.py"""

    def __init__(self, center_x=480, center_y=360):
        self.center_x = center_x
        self.center_y = center_y
        self.kp_yaw = 0.2
        self.kp_ud = 0.3
        self.fb_speed_val = 15
        self.target_history = deque(maxlen=5)

        # 距离控制阈值
        self.area_min = 100000   # 像素面积 < 此值→前进
        self.area_max = 150000   # 像素面积 > 此值→后退

    def update(self, target_info):
        """输入目标信息，输出 rc_control 参数"""
        if target_info is None:
            return (0, 0, 0, 0)

        cx, cy = target_info['center']
        area = target_info.get('area', self.area_min + 1)

        # 平滑中心点
        self.target_history.append((cx, cy))
        if len(self.target_history) >= 3:
            avg = np.mean(self.target_history, axis=0).astype(int)
            smooth_cx, smooth_cy = avg
        else:
            smooth_cx, smooth_cy = cx, cy

        # 偏航 (yaw)
        error_x = smooth_cx - self.center_x
        yaw_speed = int(self.kp_yaw * error_x)
        yaw_speed = max(-50, min(50, yaw_speed))

        # 升降 (ud)
        error_y = self.center_y - smooth_cy
        ud_speed = int(self.kp_ud * error_y)
        ud_speed = max(-50, min(50, ud_speed))

        # 前后 (fb) —— 基于像素面积
        if area < self.area_min:
            fb_speed = self.fb_speed_val    # 前进
        elif area > self.area_max:
            fb_speed = -self.fb_speed_val   # 后退
        else:
            fb_speed = 0

        return (0, fb_speed, ud_speed, yaw_speed)


def emergency_check():
    """TOF 紧急安全检查"""
    tof = send_command("sensor tof")
    try:
        dist = int(tof)
    except ValueError:
        return False
    return 100 <= dist < 500  # TOF 有效且距离 < 50cm 触发紧急


def main():
    parser = argparse.ArgumentParser(description='实时人员跟随')
    parser.add_argument('--duration', type=int, default=120, help='跟随时长(秒)')
    parser.add_argument('--model', choices=['seg', 'pose'], default='seg',
                        help='跟踪模型')
    args = parser.parse_args()

    # 信号处理：Ctrl+C 安全降落
    running = True

    def sigint_handler(signum, frame):
        nonlocal running
        print("\n收到中断信号，安全降落...")
        running = False

    signal.signal(signal.SIGINT, sigint_handler)

    # 确保视频流开启
    send_command("vision stream_on")
    time.sleep(1)

    # 初始化跟随控制器
    # TODO: 相机分辨率从实际获取
    controller = FollowController(center_x=480, center_y=360)

    # LED 红灯 → 跟随中
    send_command("led solid 255 0 0")

    start_time = time.time()
    print(f"跟随模式开始，时长 {args.duration} 秒，模型: {args.model}")

    while running and (time.time() - start_time < args.duration):
        # 紧急 TOF 检查
        if emergency_check():
            print("TOF 紧急停止——距离过近")
            send_command("flight rc 0 0 0 0")
            break

        # YOLO 检测
        result = send_command("yolo detect")
        if result.startswith("error"):
            print(f"检测错误: {result}")
            send_command("flight rc 0 0 0 0")
            time.sleep(0.1)
            continue

        try:
            persons = json.loads(result)
        except json.JSONDecodeError:
            persons = []

        if not persons:
            # 无目标，停止移动
            send_command("flight rc 0 0 0 0")
            send_command("matrix static b ?")
            time.sleep(0.1)
            continue

        # 选置信度最高的人作为目标
        target = max(persons, key=lambda p: p['confidence'])
        # 计算面积作为距离指标
        x1, y1, x2, y2 = target['bbox']
        target['area'] = (x2 - x1) * (y2 - y1)

        lr, fb, ud, yaw = controller.update(target)
        send_command(f"flight rc {lr} {fb} {ud} {yaw}")

        # 屏显距离（粗略：基于面积）
        area_str = f"{target['area'] // 1000}k"
        send_command(f"matrix static r {area_str}")

        time.sleep(0.05)  # ~20Hz

    # 停止
    send_command("flight rc 0 0 0 0")
    send_command("led off")
    send_command("matrix off")
    send_command("vision stream_off")
    print("跟随结束")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/tasks/task_follow.py
git commit -m "feat: 实现 task_follow YOLO+比例控制实时跟随"
```

---

### Task 8: SKILL.md 最终确认 + 集成验证

**Files:**
- Verify: `SKILL.md`（已完成）
- Verify: `evals/evals.json`（已完成）

- [ ] **Step 1: 确认所有脚本可被 SKILL.md 引用的格式一致**

逐一验证 CLI 命令格式与 SKILL.md 描述匹配：
```bash
cd /home/fallthrive/WorkSpace/tello/tello_skills
uv run scripts/flight.py --help
uv run scripts/led.py --help
uv run scripts/matrix.py --help
uv run scripts/sensor.py --help
uv run scripts/vision.py --help
uv run scripts/yolo.py --help
uv run scripts/mission_pad.py --help
uv run scripts/tasks/task_search_pad.py --help
uv run scripts/tasks/task_follow.py --help
```

- [ ] **Step 2: 确认依赖完整**

```bash
uv sync
# 验证: djitellopy, torch, torchvision, ultralytics 均已安装
```

- [ ] **Step 3: Commit**

```bash
git add SKILL.md evals/evals.json
git commit -m "docs: 确认 SKILL.md 与 CLI 脚本一致性"
```

---

### 架构总结

```
用户 → AI (Claude/OpenClaw) → CLI 脚本
                                  │
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
    flight.py                vision.py                task_follow.py
    led.py                   sensor.py                task_search_pad.py
    matrix.py                yolo.py
    mission_pad.py
         │                        │                        │
         └────────────────────────┼────────────────────────┘
                                  │ TCP (127.0.0.1:9999)
                                  ▼
                        ┌─────────────────┐
                        │  controller.py   │
                        │  (持久进程)      │
                        │                 │
                        │  主线程: TCP服务 │
                        │  守护线程: 心跳  │
                        │  工作线程: 录像  │
                        └────────┬────────┘
                                 │ DJITelloPy (UDP)
                                 ▼
                          Tello TT 无人机
```

### 测试说明

- **无实机测试**：所有单元测试应 mock `send_command` 或使用模拟 controller
- **实机测试**：先启动 `controller.py &`，再逐个 CLI 脚本测试，最后跑完整流程
- **安全优先**：任何测试前确保无人机在开阔安全区域，电池 > 50%
