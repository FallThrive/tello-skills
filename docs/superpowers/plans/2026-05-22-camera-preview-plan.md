# 摄像头预览 + 标注画面：实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Tello controller 新增 4 种 OpenCV 预览窗口（纯净前视/下视、YOLO 标注、挑战卡标注），支持多窗口共存，同时修复 RGB/BGR 通道 bug 和挑战卡编号校验。

**Architecture:** 预览窗口作为 controller 内部 daemon 线程运行，模式与现有 recorder-daemon 一致。`preview-forward/downward` 纯显示，`preview-yolo` 自己跑 `model.track()` 推理+绘制，`preview-pad` 显示共享状态中的挑战卡数据。`mission_pad detect` 非阻塞启动预览，`detect_stop` 停止并返回 JSON。

**Tech Stack:** Python 3, OpenCV (cv2), DJITelloPy, ultralytics YOLO, threading

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `scripts/controller.py` | 所有预览线程 + 共享状态 + dispatch 路由 + RGB/BGR 修复 + pad_id 校验 | 修改 |
| `scripts/vision.py` | `preview_start/stop`、`preview_yolo_stop` CLI 子命令 | 修改 |
| `scripts/mission_pad.py` | `detect`、`detect_stop` CLI 子命令 | 修改 |
| `scripts/tasks/task_search_pad.py` | 搜索开始时启动挑战卡预览，结束时关闭；pad_id 1-8 校验 | 修改 |

---

### Task 1: 添加预览共享状态字段

**Files:**
- Modify: `scripts/controller.py:29-58` (`__init__`)

- [ ] **Step 1: 在 `__init__` 中添加预览相关状态字段**

在 `# --- task status 查询共享状态 ---` 块之后（第 58 行后）添加：

```python
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
```

- [ ] **Step 2: 确认无语法错误**

```bash
uv run python -c "from scripts.controller import TelloController; c = TelloController(); print('ok')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 添加预览共享状态字段"
```

---

### Task 2: 帧处理辅助方法与纯净预览 daemon 线程

**Files:**
- Modify: `scripts/controller.py` (在 `_record_loop` 之后新增方法)

- [ ] **Step 1: 添加帧处理静态方法和预览线程**

在 `_record_loop` 方法之后（第 379 行后）添加以下全部代码：

```python
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
```

- [ ] **Step 2: 确认语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('ok')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 添加帧处理辅助方法与纯净预览 daemon 线程"
```

---

### Task 3: 实现 preview_start / preview_stop / preview_yolo_stop

**Files:**
- Modify: `scripts/controller.py:_dispatch` (第 134-141 行)
- Modify: `scripts/controller.py:_handle_vision` (第 301-350 行)

- [ ] **Step 1: 更新 `_dispatch` vision 模块路由**

替换第 134-141 行：

```python
        elif module == "vision":
            if action in ("stream_on", "stream_off",
                          "preview_start", "preview_stop"):
                with self._flight_lock:
                    self._update_cmd_time()
                    return self._handle_vision(action, remaining)
            else:
                return self._handle_vision(action, remaining)
```

- [ ] **Step 2: 在 `_handle_vision` 开头插入新的 action 分支**

在 `import cv2` 行之后、`if action == "stream_on":` 之前插入：

```python
        if action == "preview_start":
            direction = args[0] if args else ""
            if direction not in ("forward", "downward"):
                return "error: direction must be forward or downward"
            with self._state_lock:
                if direction in self._preview_threads:
                    t = self._preview_threads[direction]
                    if t is not None and t.is_alive():
                        return "ok"
            cam_dir = (self.tello.CAMERA_FORWARD if direction == "forward"
                       else self.tello.CAMERA_DOWNWARD)
            self.tello.set_video_direction(cam_dir)
            with self._state_lock:
                fr = self._frame_read
            if fr is None:
                self.tello.streamon()
                with self._state_lock:
                    self._frame_read = self.tello.get_frame_read()
            stop_ev = Event()
            with self._state_lock:
                self._preview_stops[direction] = stop_ev
            t = Thread(target=self._preview_clean_loop, args=(direction,),
                       daemon=True, name=f"preview-{direction}")
            t.start()
            with self._state_lock:
                self._preview_threads[direction] = t
            logger.info(f"预览窗口已开启: {direction}")
            return "ok"

        elif action == "preview_stop":
            direction = args[0] if args else ""
            if direction not in ("forward", "downward"):
                return "error: direction must be forward or downward"
            with self._state_lock:
                stop_ev = self._preview_stops.pop(direction, None)
            if stop_ev:
                stop_ev.set()
            with self._state_lock:
                t = self._preview_threads.pop(direction, None)
            if t:
                t.join(timeout=2)
            cv2.destroyWindow(f"Tello {direction.upper()}")
            return "ok"

        elif action == "preview_yolo_stop":
            self._preview_yolo_stop.set()
            t = self._preview_yolo_thread
            if t:
                t.join(timeout=3)
            with self._state_lock:
                self._preview_yolo_thread = None
            cv2.destroyWindow("Tello YOLO")
            return "ok"
```

**注意**：`from threading import Event, Thread` 已在 controller.py 第 16 行导入，无需额外添加 import。

- [ ] **Step 3: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('ok')"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 实现 preview_start/preview_stop/preview_yolo_stop"
```

---

### Task 4: 实现 preview-yolo daemon 与 YOLO 检测集成

**Files:**
- Modify: `scripts/controller.py` (在 `_preview_clean_loop` 之后新增方法)
- Modify: `scripts/controller.py:_dispatch` (yolo 模块路由，第 144-152 行)
- Modify: `scripts/controller.py:_handle_yolo` (detect 分支，第 503-519 行)

- [ ] **Step 1: 添加 COCO 骨架和 YOLO 绘制方法**

在 `_preview_clean_loop` 之后添加：

```python
    SKELETON_EDGES = [
        (0, 1), (0, 2), (1, 3), (2, 4),
        (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
        (5, 11), (6, 12), (11, 12),
        (11, 13), (13, 15), (12, 14), (14, 16)
    ]

    def _draw_yolo_overlay(self, frame, detections, kpts_data, masks_xy,
                           model_type, locked_id, battery):
        import cv2

        for i, d in enumerate(detections):
            tid = d['track_id']
            is_locked = (tid == locked_id)
            color = (0, 255, 0) if is_locked else (0, 0, 255)
            x1, y1, x2, y2 = d['bbox']

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"person [{tid}]"
            (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - lh - 6), (x1 + lw + 4, y1), color, -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

            if model_type == "pose" and kpts_data is not None and i < len(kpts_data):
                kpts = kpts_data[i]
                for s, e in self.SKELETON_EDGES:
                    if kpts[s][2] > 0.5 and kpts[e][2] > 0.5:
                        pt1 = (int(kpts[s][0]), int(kpts[s][1]))
                        pt2 = (int(kpts[e][0]), int(kpts[e][1]))
                        cv2.line(frame, pt1, pt2, color, 2)
                for kp in kpts:
                    if kp[2] > 0.5:
                        cv2.circle(frame, (int(kp[0]), int(kp[1])), 3, color, -1)

            if model_type == "seg" and masks_xy is not None and i < len(masks_xy):
                contour = masks_xy[i]
                if len(contour) > 0:
                    cv2.drawContours(frame, [contour.astype(int)], -1, color, 2)

        h, w = frame.shape[:2]
        bar_h = 30
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
        frame = cv2.addWeighted(frame, 0.7, overlay, 0.3, 0)

        if locked_id is not None:
            target = next((d for d in detections if d['track_id'] == locked_id), None)
            if target and model_type == "pose":
                metric = f"torso:{target.get('torso_height', 0):.0f}px"
            elif target and model_type == "seg":
                metric = f"area:{target.get('area', 0) // 1000:.0f}k"
            else:
                metric = ""
            status_text = f"Locked: {locked_id}  {metric}  Bat: {battery}%"
        else:
            status_text = f"Locked: ?  Bat: {battery}%"
        cv2.putText(frame, status_text, (5, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1)

        return frame
```

- [ ] **Step 2: 添加 `_preview_yolo_loop` daemon 线程**

在 `_draw_yolo_overlay` 之后添加：

```python
    def _preview_yolo_loop(self, model_type):
        import cv2

        window_name = "Tello YOLO"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        with self._model_lock:
            self._ensure_yolo_model(model_type)

        local_locked_id = None
        battery = "??"
        frame_count = 0

        while not self._preview_yolo_stop.is_set():
            with self._state_lock:
                fr = self._frame_read
            if fr is None or fr.frame is None:
                time.sleep(0.05)
                continue

            frame = fr.frame.copy()
            frame = self._process_forward_frame(frame)
            fh, fw = frame.shape[:2]
            frame_cx, frame_cy = fw // 2, fh // 2

            with self._model_lock:
                results = self._yolo_model.track(
                    frame, classes=[0], persist=True,
                    tracker='botsort.yaml', verbose=False
                )
            detections = self._parse_track_detections(results[0], model_type)

            kpts_data = None
            masks_xy = None
            if model_type == "pose":
                kp = results[0].keypoints
                if kp is not None:
                    kpts_data = kp.data.cpu().numpy()
            else:
                m = results[0].masks
                if m is not None and m.xy:
                    masks_xy = m.xy

            if local_locked_id is not None:
                target = next(
                    (d for d in detections if d['track_id'] == local_locked_id), None)
            else:
                target = None

            if local_locked_id is None and detections:
                target = min(detections, key=lambda d:
                    (d['center'][0] - frame_cx) ** 2 + (d['center'][1] - frame_cy) ** 2)
                local_locked_id = target['track_id']
                logger.info(f"preview-yolo: 锁定目标 track_id={local_locked_id}")

            with self._state_lock:
                self._yolo_shared.update({
                    "model_type": model_type,
                    "detections": detections,
                    "kpts_data": kpts_data,
                    "masks_xy": masks_xy,
                    "frame_cx": frame_cx,
                    "frame_cy": frame_cy,
                    "locked_id": local_locked_id,
                    "fresh": True,
                })

            frame_count += 1
            if frame_count % 30 == 0:
                with self._flight_lock:
                    try:
                        battery = str(self.tello.get_battery())
                    except Exception:
                        pass

            frame = self._draw_yolo_overlay(
                frame, detections, kpts_data, masks_xy, model_type,
                local_locked_id, battery)

            cv2.imshow(window_name, frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cv2.destroyWindow(window_name)
        self._preview_yolo_stop.clear()
        with self._state_lock:
            self._preview_yolo_thread = None
```

- [ ] **Step 3: 更新 `_dispatch` yolo 模块路由**

替换第 144-152 行：

```python
        elif module == "yolo":
            model_type = "pose"
            remaining_for_yolo = parts[1:]
            for i, p in enumerate(remaining_for_yolo):
                if p == "--model" and i + 1 < len(remaining_for_yolo):
                    model_type = remaining_for_yolo[i + 1]
                    break
            if action == "detect":
                return self._handle_yolo(action, model_type)
            else:
                with self._model_lock:
                    return self._handle_yolo(action, model_type)
```

- [ ] **Step 4: 更新 `_handle_yolo` detect 分支**

替换第 503-519 行的 detect 分支：

```python
        if action == "detect":
            with self._state_lock:
                yolo_running = (self._preview_yolo_thread is not None
                                and self._preview_yolo_thread.is_alive())
            if not yolo_running:
                self._preview_yolo_stop.clear()
                with self._state_lock:
                    self._yolo_shared["fresh"] = False
                t = Thread(target=self._preview_yolo_loop, args=(model_type,),
                           daemon=True, name="preview-yolo")
                t.start()
                with self._state_lock:
                    self._preview_yolo_thread = t
                waited = 0
                while waited < 3.0:
                    with self._state_lock:
                        fresh = self._yolo_shared["fresh"]
                    if fresh:
                        break
                    time.sleep(0.1)
                    waited += 0.1

            with self._state_lock:
                detections = list(self._yolo_shared["detections"])
                locked_id = self._yolo_shared["locked_id"]
                frame_cx = self._yolo_shared["frame_cx"]
                frame_cy = self._yolo_shared["frame_cy"]

            if not detections:
                self._follow_target_id = None
                return json.dumps({}, ensure_ascii=False)

            target = None
            if self._follow_target_id is not None:
                target = next(
                    (d for d in detections if d['track_id'] == self._follow_target_id), None)
            if target is None:
                target = min(detections, key=lambda d:
                    (d['center'][0] - frame_cx) ** 2 + (d['center'][1] - frame_cy) ** 2)
                self._follow_target_id = target['track_id']

            return json.dumps(target, ensure_ascii=False)
```

- [ ] **Step 5: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('ok')"
```

- [ ] **Step 6: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: preview-yolo daemon + yolo detect 自动弹窗集成"
```

---

### Task 5: 实现 mission_pad detect + preview-pad

**Files:**
- Modify: `scripts/controller.py:_dispatch` (mission_pad 路由，第 130-133 行)
- Modify: `scripts/controller.py:_handle_mission_pad` (第 764-784 行)
- Modify: `scripts/controller.py` (新增 `_preview_pad_loop` 方法)

- [ ] **Step 1: 更新 `_dispatch` mission_pad 路由**

替换第 130-133 行：

```python
        elif module == "mission_pad":
            if action in ("detect", "detect_stop"):
                return self._handle_mission_pad(action, remaining)
            else:
                with self._flight_lock:
                    self._update_cmd_time()
                    return self._handle_mission_pad(action, remaining)
```

- [ ] **Step 2: 添加 `_preview_pad_loop` 方法**

在 `_preview_yolo_loop` 之后添加：

```python
    def _preview_pad_loop(self):
        import cv2

        window_name = "Tello Pad"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        battery = "??"
        frame_count = 0

        while not self._preview_pad_stop.is_set():
            with self._state_lock:
                fr = self._frame_read
            if fr is None or fr.frame is None:
                time.sleep(0.05)
                continue

            frame = fr.frame.copy()
            frame = self._process_downward_frame(frame)

            with self._state_lock:
                pad_info = dict(self._pad_shared)

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

            pad_id = pad_info.get("id", -1)
            if 1 <= pad_id <= 8:
                x, y, z = pad_info.get("x", 0), pad_info.get("y", 0), pad_info.get("z", 0)
                status = f"Pad: #{pad_id}  ({x},{y},{z})  Bat: {battery}%"
            else:
                status = f"Pad: --  Bat: {battery}%"
            cv2.putText(frame, status, (5, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1)

            cv2.imshow(window_name, frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cv2.destroyWindow(window_name)
        self._preview_pad_stop.clear()
        with self._state_lock:
            self._preview_pad_thread = None
            self._pad_shared["active"] = False
```

- [ ] **Step 3: 在 `_handle_mission_pad` 中添加 detect / detect_stop**

在 `elif action == "fly":` 之前插入：

```python
        elif action == "detect":
            with self._state_lock:
                if self._preview_pad_thread is not None and self._preview_pad_thread.is_alive():
                    return "ok"
            self.tello.set_video_direction(self.tello.CAMERA_DOWNWARD)
            with self._state_lock:
                fr = self._frame_read
            if fr is None:
                self.tello.streamon()
                with self._state_lock:
                    self._frame_read = self.tello.get_frame_read()
            self.tello.enable_mission_pads()
            self.tello.set_mission_pad_detection_direction(0)
            self._preview_pad_stop.clear()
            with self._state_lock:
                self._pad_shared["active"] = True
            t = Thread(target=self._preview_pad_loop, daemon=True, name="preview-pad")
            t.start()
            with self._state_lock:
                self._preview_pad_thread = t
            logger.info("挑战卡预览窗口已开启")
            return "ok"

        elif action == "detect_stop":
            self._preview_pad_stop.set()
            t = self._preview_pad_thread
            if t:
                t.join(timeout=2)
            with self._state_lock:
                pad_id = self._pad_shared["id"]
                x = self._pad_shared["x"]
                y = self._pad_shared["y"]
                z = self._pad_shared["z"]
            cv2.destroyWindow("Tello Pad")
            self.tello.disable_mission_pads()
            return json.dumps({"id": pad_id, "x": x, "y": y, "z": z}, ensure_ascii=False)
```

- [ ] **Step 4: 修改 `id` / `xyz` action 同步更新共享状态**

替换 `id` 分支（第 770-771 行）：

```python
        elif action == "id":
            pad_id = self.tello.get_mission_pad_id()
            if 1 <= pad_id <= 8:
                with self._state_lock:
                    self._pad_shared["id"] = pad_id
                x = self.tello.get_mission_pad_distance_x()
                y = self.tello.get_mission_pad_distance_y()
                z = self.tello.get_mission_pad_distance_z()
                with self._state_lock:
                    self._pad_shared.update({"x": x, "y": y, "z": z})
            return str(pad_id)
```

替换 `xyz` 分支（第 772-776 行）：

```python
        elif action == "xyz":
            x = self.tello.get_mission_pad_distance_x()
            y = self.tello.get_mission_pad_distance_y()
            z = self.tello.get_mission_pad_distance_z()
            pad_id = self.tello.get_mission_pad_id()
            if 1 <= pad_id <= 8:
                with self._state_lock:
                    self._pad_shared.update({"id": pad_id, "x": x, "y": y, "z": z})
            return f"{x} {y} {z}"
```

- [ ] **Step 5: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('ok')"
```

- [ ] **Step 6: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: mission_pad detect/detect_stop + preview-pad daemon"
```

---

### Task 6: 修复 pad_id 校验 + RGB→BGR bug

**Files:**
- Modify: `scripts/controller.py:_handle_mission_pad` fly (第 777-781 行)
- Modify: `scripts/controller.py:_handle_vision` photo (第 328 行)
- Modify: `scripts/controller.py:_record_loop` (第 371 行)

- [ ] **Step 1: 修复 `fly` 的 pad_id 校验**

在 `fly` 分支 `pad_id = int(args[1])` 之后添加：

```python
            if not 1 <= pad_id <= 8:
                return "error: invalid pad id, must be 1-8"
```

- [ ] **Step 2: 修复 photo 的 RGB→BGR 转换**

替换第 328 行 `cv2.imwrite(path, fr.frame)`：

```python
            frame = fr.frame
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            cv2.imwrite(path, frame)
```

- [ ] **Step 3: 修复 `_record_loop` 的 RGB→BGR 转换**

替换第 371 行 `out.write(fr.frame)`：

```python
                    frame = fr.frame
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    out.write(frame)
```

- [ ] **Step 4: 验证语法**

```bash
uv run python -c "from scripts.controller import TelloController; print('ok')"
```

- [ ] **Step 5: 提交**

```bash
git add scripts/controller.py
git commit -m "fix: pad_id 1-8 校验 + photo/record RGB→BGR 转换"
```

---

### Task 7: 更新 vision.py CLI

**Files:**
- Modify: `scripts/vision.py` (整个文件)

- [ ] **Step 1: 重写 vision.py**

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
    p_photo.add_argument('--name', '-n', default='')

    p_rec_start = sub.add_parser('record_start', help='开始录像')
    p_rec_start.add_argument('--name', '-n', default='')

    sub.add_parser('record_stop', help='停止录像')

    p_prev_start = sub.add_parser('preview_start', help='开启纯净预览窗口')
    p_prev_start.add_argument('direction', choices=['forward', 'downward'])

    p_prev_stop = sub.add_parser('preview_stop', help='关闭纯净预览窗口')
    p_prev_stop.add_argument('direction', choices=['forward', 'downward'])

    sub.add_parser('preview_yolo_stop', help='关闭 YOLO 标注预览窗口')

    args = parser.parse_args()

    if args.action == 'photo':
        cmd = f"vision photo {args.name}"
    elif args.action == 'record_start':
        cmd = f"vision record_start {args.name}"
    elif args.action in ('preview_start', 'preview_stop'):
        cmd = f"vision {args.action} {args.direction}"
    else:
        cmd = f"vision {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证 CLI 帮助**

```bash
uv run python scripts/vision.py --help
```

- [ ] **Step 3: 提交**

```bash
git add scripts/vision.py
git commit -m "feat: vision.py 添加 preview_start/preview_stop/preview_yolo_stop"
```

---

### Task 8: 更新 mission_pad.py CLI

**Files:**
- Modify: `scripts/mission_pad.py` (整个文件)

- [ ] **Step 1: 重写 mission_pad.py**

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
    p_fly.add_argument('--id', type=int, required=True, help='挑战卡 ID (1-8)')

    sub.add_parser('detect', help='开启挑战卡预览窗口（非阻塞）')
    sub.add_parser('detect_stop', help='关闭挑战卡预览窗口并返回最后检测结果')

    args = parser.parse_args()

    if args.action == 'fly':
        cmd = f"mission_pad fly --id {getattr(args, 'id')}"
    else:
        cmd = f"mission_pad {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证 CLI 帮助**

```bash
uv run python scripts/mission_pad.py --help
```

- [ ] **Step 3: 提交**

```bash
git add scripts/mission_pad.py
git commit -m "feat: mission_pad.py 添加 detect/detect_stop"
```

---

### Task 9: 更新 task_search_pad.py

**Files:**
- Modify: `scripts/tasks/task_search_pad.py`

- [ ] **Step 1: 重写 task_search_pad.py，集成预览 + pad_id 校验**

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

    dir_map = {'f': 'f', 'b': 'b', 'l': 'l', 'r': 'r'}

    send_command("mission_pad enable")

    found = False
    preview_started = False
    for i in range(max_attempts):
        resp = send_command(f"flight move {dir_map[direction]} {step}")
        if resp.startswith("error"):
            print(f"飞行错误: {resp}")
            break

        time.sleep(0.5)

        if not preview_started:
            send_command("mission_pad detect")
            preview_started = True

        pad_id = send_command("mission_pad id")
        if pad_id.startswith("error"):
            print(f"检测错误: {pad_id}")
            continue

        try:
            pad_id = int(pad_id)
        except ValueError:
            pad_id = -1

        if 1 <= pad_id <= 8:
            print(f"检测到挑战卡 #{pad_id}")
            send_command(f"mission_pad fly --id {pad_id}")
            send_command("led solid 0 0 255")
            send_command(f"matrix static b {pad_id}")
            found = True
            break

        print(f"尝试 {i+1}/{max_attempts}，未检测到挑战卡")

    if not found:
        print(f"超 max_attempts={max_attempts}，未找到挑战卡")
        send_command("led solid 255 0 0")
        time.sleep(1)
        send_command("led off")

    if preview_started:
        result = send_command("mission_pad detect_stop")
        print(f"挑战卡预览关闭: {result}")

    send_command("mission_pad disable")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证语法**

```bash
uv run python -c "import py_compile; py_compile.compile('scripts/tasks/task_search_pad.py', doraise=True); print('ok')"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/tasks/task_search_pad.py
git commit -m "feat: task_search_pad 集成预览 + pad_id 1-8 校验"
```

---

### Task 10: 全量语法验证

- [ ] **Step 1: 验证所有改动的文件能正常导入**

```bash
uv run python -c "
from scripts.controller import TelloController
import scripts.vision
import scripts.mission_pad
print('所有模块语法正确')
"
```

- [ ] **Step 2: 验证 controller 能正常启动（无无人机连接时会在 connect() 失败，属预期行为）**

```bash
timeout 3 uv run python scripts/controller.py 2>&1 || true
```

- [ ] **Step 3: 提交（如有残留修改）**

```bash
git status
```
