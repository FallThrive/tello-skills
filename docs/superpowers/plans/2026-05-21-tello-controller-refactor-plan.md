# Tello Controller 三合一改造：实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 controller 为三锁多线程架构，用 ultralytics BoT-SORT 内置追踪替换手动 IoU，将 task follow 闭环移入 controller。

**Architecture:** 三把锁（_flight_lock, _model_lock, _state_lock）替换单锁，ThreadPoolExecutor(max_workers=4) 处理并发命令。task follow 在独立 daemon 线程中运行 YOLO+RC 闭环，阻塞一个 pool worker 但保留 3 个 worker 处理紧急命令。

**Tech Stack:** Python 3, DJITelloPy, ultralytics YOLO (BoT-SORT), threading, concurrent.futures

---

## 文件结构

| 文件 | 职责 | 改动 |
|------|------|:--:|
| `scripts/controller.py` | 三锁 + 多线程 + model.track() + task follow 闭环 | 565 → ~650 行 |
| `scripts/_client.py` | 新增可选 timeout 参数 | 28 → 30 行 |
| `scripts/tasks/task_follow.py` | 简化为发送 task follow 命令 | 174 → ~65 行 |

---

### Task 1: 更新 imports 和 __init__（controller.py）

**文件：** `scripts/controller.py:10-44`

- [ ] **Step 1: 更新 imports（第 16 行）**

将：
```python
from threading import Lock, Thread
```
改为：
```python
from threading import Lock, Thread, Event
from concurrent.futures import ThreadPoolExecutor
```

- [ ] **Step 2: 重写 __init__（第 28-44 行）**

将：
```python
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
    self._loaded_model_type = None
    self._tracked_target = None  # IoU 跟踪锁定目标 {'bbox': [x1,y1,x2,y2], 'id': int}
```

改为：
```python
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
```

- [ ] **Step 3: 验证 Python 语法**

```bash
uv run python -c "from scripts.controller import TelloController; c = TelloController(); print('__init__ OK')"
```

预期：`__init__ OK`

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: 三锁替换单锁，新增 task 状态变量（controller __init__）"
```

---

### Task 2: 新增 _update_cmd_time 并重写 _heartbeat_loop（controller.py）

**文件：** `scripts/controller.py:46-72`

- [ ] **Step 1: 在 connect() 之后插入 _update_cmd_time()**

在第 52 行（`connect` 方法结束后）插入：
```python
    def _update_cmd_time(self):
        """更新最后一次命令时间（用于心跳判断），在 _flight_lock 保护下调用"""
        with self._state_lock:
            self._last_cmd_time = time.time()
```

- [ ] **Step 2: 重写 _heartbeat_loop（第 58-72 行）**

将：
```python
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
```

改为：
```python
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
```

- [ ] **Step 3: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; c = TelloController(); c._update_cmd_time(); print('heartbeat OK')"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: 新增 _update_cmd_time，心跳适配 _state_lock + _flight_lock"
```

---

### Task 3: 重写 execute() 和 _dispatch()（controller.py）

**文件：** `scripts/controller.py:74-117`

- [ ] **Step 1: 重写 execute()（第 78-85 行）**

将：
```python
    def execute(self, cmd: str) -> str:
        """解析并执行命令，返回响应字符串"""
        with self._lock:
            self._last_cmd_time = time.time()
            try:
                return self._dispatch(cmd.strip())
            except Exception as e:
                return f"error: {e}"
```

改为：
```python
    def execute(self, cmd: str) -> str:
        """解析并执行命令，返回响应字符串。锁由 _dispatch 内部分派。"""
        try:
            return self._dispatch(cmd.strip())
        except Exception as e:
            return f"error: {e}"
```

- [ ] **Step 2: 重写 _dispatch()（第 87-117 行）**

将：
```python
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
```

改为：
```python
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
```

- [ ] **Step 3: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('dispatch OK')"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: _dispatch 按模块分派锁，execute 去掉全局锁"
```

---

### Task 4: 更新 _handle_vision 和 _record_loop（controller.py）

**文件：** `scripts/controller.py:259-316`

- [ ] **Step 1: 重写 _handle_vision()（第 259-297 行）**

将：
```python
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
```

改为：
```python
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
```

- [ ] **Step 2: 重写 _record_loop()（第 299-316 行）**

将：
```python
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
```

改为：
```python
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
```

- [ ] **Step 3: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('vision OK')"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: _handle_vision 和 _record_loop 适配 _state_lock，修复 out.release 未在 finally 中调用"
```

---

### Task 5: 重写 start_server + 新增 _handle_client（controller.py）

**文件：** `scripts/controller.py:515-565`

- [ ] **Step 1: 替换 start_server()（第 515-559 行）**

将整个 `start_server` 方法（第 515-559 行）改为：
```python
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
```

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; c = TelloController(); print('start_server OK')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: start_server 引入 ThreadPoolExecutor，新增 _handle_client 池线程处理"
```

---

### Task 6: 新增 _parse_track_detections 和 _track_match（controller.py）

**文件：** `scripts/controller.py`（在 YOLO 模块区域，_ensure_yolo_model 之后插入）

- [ ] **Step 1: 在 _ensure_yolo_model 方法后插入 _parse_track_detections()**

在第 329 行后插入：
```python
    def _parse_track_detections(self, result, model_type):
        """从 model.track() 结果中统一提取检测列表（含 track_id）。
        供 _yolo_detect_pose, _yolo_detect_seg, _task_follow_loop 复用。
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
```

- [ ] **Step 2: 在 _parse_track_detections 后插入 _track_match()**

```python
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
```

- [ ] **Step 3: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('parse+match OK')"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 新增 _parse_track_detections 和 _track_match，统一解析 + track ID 匹配"
```

---

### Task 7: 重写 _handle_yolo + _yolo_detect_pose/seɡ（controller.py）

**文件：** `scripts/controller.py:331-443`

- [ ] **Step 1: 重写 _handle_yolo（第 331-353 行）**

将：
```python
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
```

改为：
```python
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
```

- [ ] **Step 2: 删除 _yolo_detect_seg 和 _yolo_detect_pose 方法**

删除第 355-443 行的 `_yolo_detect_seg` 和 `_yolo_detect_pose` 两个方法（已被 `_parse_track_detections` + `_track_match` 替代）。

- [ ] **Step 3: 删除 _iou_match 方法**

删除第 445-483 行的 `_iou_match` 方法。

- [ ] **Step 4: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('yolo OK')"
```

- [ ] **Step 5: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: _handle_yolo 改用 model.track(BoT-SORT)，删除 _iou_match 和 _yolo_detect_*，由 _parse_track_detections 统一处理"
```

---

### Task 8: 新增 _handle_task + _start_task_follow（controller.py）

**文件：** `scripts/controller.py`（在 Mission Pad 模块之前插入，即原第 485 行附近）

- [ ] **Step 1: 在 Mission Pad 模块前插入 task 模块方法**

插入位置：在 `# --- 挑战卡模块 ---` 注释（原第 485-486 行）之前：

```python
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
        dist_low = None  # None = 使用模式默认值
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

        # 设置模式默认距离参数
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

        ft.join()  # 阻塞当前 pool worker，等待跟踪完成
        return "ok"
```

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; c = TelloController(); print('task dispatch OK')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 新增 _handle_task 和 _start_task_follow，支持 task follow/stop/status 命令"
```

---

### Task 9: 新增 _task_follow_loop（controller.py）

**文件：** `scripts/controller.py`（在 _start_task_follow 方法之后插入）

- [ ] **Step 1: 插入 _task_follow_loop()**

```python
    def _task_follow_loop(self, model_type, duration, kp_yaw, kp_ud, fb_speed_val,
                           dist_low, dist_high):
        """YOLO track + P 控制闭环。在独立 daemon 线程中运行。"""
        start_time = time.time()
        local_track_id = None
        frame_cx, frame_cy = 480, 360

        # 初始化任务状态
        with self._state_lock:
            self._follow_status.update({
                "running": True, "model": model_type, "elapsed": 0.0,
                "duration": duration, "track_id": None,
                "rc_speed": {"lr": 0, "fb": 0, "ud": 0, "yaw": 0},
                "tof_distance": 0, "target": None
            })

        # 加载模型（仅在循环外加载一次）
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
```

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('follow_loop OK')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 新增 _task_follow_loop 闭环跟踪（YOLO+RC+TOF+LED矩阵+状态推送）"
```

---

### Task 10: 新增 _compute_p_controls（controller.py）

**文件：** `scripts/controller.py`（在 _task_follow_loop 之后插入）

- [ ] **Step 1: 插入 _compute_p_controls()**

```python
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
```

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('p_controls OK')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 新增 _compute_p_controls 统一 P 控制器"
```

---

### Task 11: controller.py 最终验证

**文件：** `scripts/controller.py`

- [ ] **Step 1: 完整语法和导入检查**

```bash
uv run python -c "
from scripts.controller import TelloController
c = TelloController()
# 验证所有关键属性存在
attrs = ['_flight_lock', '_model_lock', '_state_lock', '_follow_stop',
         '_follow_thread', '_follow_target_id', '_follow_status']
for a in attrs:
    assert hasattr(c, a), f'Missing: {a}'
# 验证所有关键方法存在
methods = ['_update_cmd_time', '_handle_client', '_handle_task',
           '_parse_track_detections', '_track_match', '_compute_p_controls',
           '_task_follow_loop', '_start_task_follow']
for m in methods:
    assert hasattr(c, m), f'Missing method: {m}'
# 验证已删除的方法不存在
removed = ['_lock', '_tracked_target', '_iou_match',
           '_yolo_detect_seg', '_yolo_detect_pose']
for r in removed:
    assert not hasattr(c, r), f'Should be removed: {r}'
print('All checks passed')
"
```

预期：`All checks passed`

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "verify: controller.py 所有属性和方法检查通过"
```

---

### Task 12: 更新 _client.py timeout 参数

**文件：** `scripts/_client.py:13-27`

- [ ] **Step 1: 修改 send_command 函数签名**

将：
```python
def send_command(cmd: str) -> str:
    """向 controller 发送命令并返回响应"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
```

改为：
```python
def send_command(cmd: str, timeout: float = 5.0) -> str:
    """向 controller 发送命令并返回响应。timeout 单位为秒，默认 5.0。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
```

函数其余部分不变。

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "from scripts._client import send_command; print('client OK')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/_client.py
git commit -m "feat: send_command 新增可选 timeout 参数（默认 5.0）"
```

---

### Task 13: 重写 task_follow.py

**文件：** `scripts/tasks/task_follow.py`（完整重写，174 行 → ~65 行）

- [ ] **Step 1: 完全重写文件**

```python
#!/usr/bin/env python3
"""实时人员跟随——通过 controller 内闭环 task follow 命令实现"""
import argparse
import time
import signal
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='实时人员跟随')
    parser.add_argument('--duration', type=int, default=120, help='跟随时长(秒)')
    parser.add_argument('--model', choices=['seg', 'pose'], default='pose',
                        help='跟踪模型')
    parser.add_argument('--kp-yaw', type=float, default=0.2, help='水平偏转系数')
    parser.add_argument('--kp-ud', type=float, default=0.3, help='垂直方向系数')
    parser.add_argument('--fb-speed', type=int, default=15, help='前后移动速度(cm/s)')
    parser.add_argument('--dist-low', type=float, default=None,
                        help='距离下限（pose: 躯干高度px, seg: 掩码面积px）')
    parser.add_argument('--dist-high', type=float, default=None,
                        help='距离上限（pose: 躯干高度px, seg: 掩码面积px）')
    args = parser.parse_args()

    def sigint_handler(signum, frame):
        print("\n收到中断信号，停止跟随...")
        try:
            send_command("task stop")
        except Exception as e:
            print(f"无法发送停止命令: {e}")

    signal.signal(signal.SIGINT, sigint_handler)

    # 开启视频流
    resp = send_command("vision stream_on")
    if resp.startswith("error"):
        print(f"开启视频流失败: {resp}")
        return
    time.sleep(1)

    send_command("led solid 255 0 0")

    # 构建 task follow 命令
    cmd_parts = [f"task follow --model {args.model} --duration {args.duration}"]
    if args.kp_yaw is not None:
        cmd_parts.append(f"--kp-yaw {args.kp_yaw}")
    if args.kp_ud is not None:
        cmd_parts.append(f"--kp-ud {args.kp_ud}")
    if args.fb_speed is not None:
        cmd_parts.append(f"--fb-speed {args.fb_speed}")
    if args.dist_low is not None:
        cmd_parts.append(f"--dist-low {args.dist_low}")
    if args.dist_high is not None:
        cmd_parts.append(f"--dist-high {args.dist_high}")
    cmd = " ".join(cmd_parts)

    print(f"跟随模式开始，时长 {args.duration} 秒，模型: {args.model}")

    response = send_command(cmd, timeout=args.duration + 15)
    print(f"跟随结果: {response}")

    # 清理
    send_command("flight rc 0 0 0 0")
    send_command("led off")
    send_command("matrix off")
    send_command("vision stream_off")
    print("跟随结束")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "import scripts.tasks.task_follow; print('task_follow OK')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/tasks/task_follow.py
git commit -m "refactor: task_follow.py 简化为发送 task follow 闭环命令，支持 P 参数覆盖"
```

---

### Task 14: 更新 SKILL.md 中的参数说明

**文件：** `.claude/skills/tello/SKILL.md`（或项目根目录的 `SKILL.md`）

- [ ] **Step 1: 查找 SKILL.md 中 task_follow 相关部分**

```bash
grep -n "task_follow\|follow\|跟踪\|跟随" SKILL.md .claude/skills/tello/SKILL.md 2>/dev/null
```

- [ ] **Step 2: 更新跟踪章节，补充默认参数表**

在跟踪相关章节中追加：

```markdown
### task follow 默认参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model` | `pose` | 跟踪模型（pose/seg） |
| `--duration` | `120` | 跟随时长（秒） |
| `--kp-yaw` | `0.2` | 水平偏转比例系数 |
| `--kp-ud` | `0.3` | 垂直方向比例系数 |
| `--fb-speed` | `15` | 前后移动速度（cm/s） |
| `--dist-low` | pose: 200px, seg: 100000px | 目标距离下限 |
| `--dist-high` | pose: 250px, seg: 150000px | 目标距离上限 |

### 其他模块默认参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `frame-cx` | `480` | 画面中心 X（720p/2） |
| `frame-cy` | `360` | 画面中心 Y（720p/2） |
| RC 限幅 | `±50` | 速度命令上下限 |
| TOF 紧急停止 | `100-500cm` | 检测距离范围 |
| IoU 阈值（已废弃） | `0.3` | 替换为 BoT-SORT track ID |
| YOLO model.track | `botsort.yaml` | 跟踪器 (BoT-SORT) |
| 心跳间隔 | `10s` | idle 后发送 rc(0,0,0,0) |
| TCP 端口 | `9999` | controller 监听端口 |
| 线程池大小 | `4` | ThreadPoolExecutor max_workers |
```

- [ ] **Step 3: 提交**

```bash
git add SKILL.md .claude/skills/tello/SKILL.md 2>/dev/null || echo "检查 SKILL.md 路径"
git commit -m "docs: SKILL.md 补充全部默认参数说明"
```

---

### Task 15: 手动验证检查清单

由于项目无自动化测试套件，以下为手动验证步骤。需在真实 Tello 无人机连接环境下执行。

- [ ] **Step 1: 基础命令回归**

```bash
# controller 启动
uv run scripts/controller.py &
sleep 2

# 基础命令
uv run scripts/vision.py stream_on
uv run scripts/sensor.py battery
uv run scripts/sensor.py tof
uv run scripts/led.py solid 255 0 0
uv run scripts/led.py off
uv run scripts/vision.py photo
uv run scripts/vision.py record_start && sleep 2 && uv run scripts/vision.py record_stop
uv run scripts/vision.py stream_off
```

确认：每个命令返回 `ok` 或预期数值，无 error。

- [ ] **Step 2: YOLO 检测兼容性**

```bash
uv run scripts/vision.py stream_on
uv run scripts/yolo.py detect
uv run scripts/yolo.py count
uv run scripts/vision.py stream_off
```

确认：`detect` 返回 JSON 含 `track_id` 字段，`count` 返回数字，无检测时返回 `{}`。

- [ ] **Step 3: task status**

```bash
uv run scripts/vision.py stream_on
uv run python -c "from scripts._client import send_command; print(send_command('task status'))"
```

确认：返回 `{"running": false}`（无跟踪运行时）。

- [ ] **Step 4: 跟踪闭环（需安全环境）**

```bash
# 终端 1
uv run scripts/tasks/task_follow.py --duration 10 --model pose

# 终端 2（跟踪期间）
uv run python -c "from scripts._client import send_command; print(send_command('task status'))"

# 终端 2 紧急降落
uv run scripts/flight.py land
```

确认：Ctrl+C 正常中断并悬停；`task status` 返回运行状态；`flight land` 正常降落。

---

## 实现顺序总结

```
Task 1  → Task 2  → Task 3  → Task 4  → Task 5   (方向 2: 锁分离 + 多线程)
                                    ↓
                              Task 6  → Task 7       (方向 1: model.track + BoT-SORT)
                                                ↓
                              Task 8  → Task 9  → Task 10  (方向 3: task follow 闭环)
                                                                       ↓
                                                                 Task 11 (验证)
                                                                       ↓
                        Task 12  → Task 13  → Task 14  → Task 15     (简化 + 文档 + 手动测试)
```

每个 Task 提交一次，共 15 次提交。
