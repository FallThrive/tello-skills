# 视觉录像/拍照修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复录像慢动作、状态残留、静默失败、文件名无扩展名四个缺陷，并将默认视频格式从 AVI 改为 MP4。

**Architecture:** 所有修改集中在 `scripts/controller.py` 的 vision 和 flight 处理逻辑中，外加 `SKILL.md` 文档更新。无新增文件，无依赖变更。

**Tech Stack:** Python, OpenCV (cv2), DJITelloPy

---

### Task 1: `stream_on` 设置摄像头 30 FPS

**Files:**
- Modify: `scripts/controller.py:414`

- [ ] **Step 1: 在 streamon() 后添加 set_video_fps**

找到 `scripts/controller.py` 第 414 行：

```python
            self.tello.streamon()
```

替换为：

```python
            self.tello.streamon()
            self.tello.set_video_fps(self.tello.FPS_30)
```

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: stream_on 时设置摄像头为 30 FPS"
```

---

### Task 2: 默认视频格式 AVI → MP4

**Files:**
- Modify: `scripts/controller.py:440`
- Modify: `scripts/controller.py:468`

- [ ] **Step 1: 修改默认文件名扩展名**

找到 `scripts/controller.py` 第 440 行：

```python
                    name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi"
```

替换为：

```python
                    name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
```

- [ ] **Step 2: 修改编码器**

找到 `scripts/controller.py` 第 467-469 行：

```python
            out = cv2.VideoWriter(
                filename, cv2.VideoWriter_fourcc(*'XVID'), 30, (w, h),
            )
```

替换为：

```python
            out = cv2.VideoWriter(
                filename, cv2.VideoWriter_fourcc(*'mp4v'), 30, (w, h),
            )
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: 默认视频格式 AVI 改为 MP4（编码器 mp4v）"
```

---

### Task 3: `_record_loop` 去重写入

**Files:**
- Modify: `scripts/controller.py:470-479`

- [ ] **Step 1: 添加帧去重逻辑**

找到 `scripts/controller.py` 第 470-478 行：

```python
            while True:
                with self._state_lock:
                    if not self._recording:
                        break
                    fr = self._frame_read
                if fr is not None:
                    frame = fr.frame
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    out.write(frame)
                time.sleep(0.01)
```

替换为：

```python
            last_frame = None
            while True:
                with self._state_lock:
                    if not self._recording:
                        break
                    fr = self._frame_read
                if fr is not None:
                    frame = fr.frame
                    if frame is not last_frame:
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        out.write(frame_bgr)
                        last_frame = frame
                time.sleep(0.01)
```

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "fix: 录像去重写入，解决视频慢动作问题"
```

---

### Task 4: `land` 时清理录像状态

**Files:**
- Modify: `scripts/controller.py:192-194`

- [ ] **Step 1: 在 land 分支中添加录像清理**

找到 `scripts/controller.py` 第 192-194 行：

```python
        elif action == "land":
            self.tello.land()
            return "ok"
```

替换为：

```python
        elif action == "land":
            self.tello.land()
            with self._state_lock:
                self._recording = False
            rt = self._recorder_thread
            if rt:
                rt.join(timeout=3)
            with self._state_lock:
                self._recorder_thread = None
                self._recording_filename = None
            return "ok"
```

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "fix: land 时清理录像状态，防止残留阻塞下次录像"
```

---

### Task 5: `record_start` 双重保障（自愈 + 流检查）

**Files:**
- Modify: `scripts/controller.py:434-444`

- [ ] **Step 1: 添加自愈逻辑和流检查**

找到 `scripts/controller.py` 第 434-444 行：

```python
        elif action == "record_start":
            name = args[0] if args else ""
            with self._state_lock:
                if self._recording:
                    return "error: already recording"
                if not name:
                    name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                self._recording_filename = os.path.join("videos", name)
                self._recording = True
            self._recorder_thread = Thread(target=self._record_loop, daemon=True)
            self._recorder_thread.start()
```

替换为：

```python
        elif action == "record_start":
            name = args[0] if args else ""
            # 自愈：如果上次录像未正常关闭（land 清理未执行到），先清理
            with self._state_lock:
                if self._recording:
                    self._recording = False
            rt = self._recorder_thread
            if rt:
                rt.join(timeout=3)
            with self._state_lock:
                self._recorder_thread = None
                # 检查视频流
                if self._frame_read is None:
                    return "error: stream not started"
                # 自动补扩展名
                if name and "." not in name:
                    name += ".mp4"
                if not name:
                    name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                self._recording_filename = os.path.join("videos", name)
                self._recording = True
            self._recorder_thread = Thread(target=self._record_loop, daemon=True)
            self._recorder_thread.start()
```

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: record_start 自愈旧录像状态并检查视频流"
```

---

### Task 6: photo 自定义文件名自动补扩展名

**Files:**
- Modify: `scripts/controller.py:422-425`

- [ ] **Step 1: 添加扩展名自动补全**

找到 `scripts/controller.py` 第 422-425 行：

```python
        elif action == "photo":
            name = args[0] if args else ""
            if not name:
                name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
```

替换为：

```python
        elif action == "photo":
            name = args[0] if args else ""
            if name and "." not in name:
                name += ".jpg"
            if not name:
                name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
```

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: photo 自定义文件名自动补 .jpg 扩展名"
```

---

### Task 7: SKILL.md 文档更新

**Files:**
- Modify: `SKILL.md:138`
- Modify: `SKILL.md:124-136`
- Modify: `SKILL.md:246`

- [ ] **Step 1: 更新输出说明**

找到 `SKILL.md` 第 138 行：

```
> 输出：成功返回 `ok`。`photo` 无流时返回 `error: stream not started`，`record_start` 已在录像返回 `error: already recording`。照片保存至 `images/`，视频保存至 `videos/`。
```

替换为：

```
> 输出：成功返回 `ok`。`photo` 和 `record_start` 无流时返回 `error: stream not started`。`record_start` 启动时若上一次录像未正常结束会自动清理。照片保存至 `images/`，视频保存至 `videos/`。**文件名默认含时间戳（如 `video_20260527_173000.mp4`），自定义名称建议也带时间戳，避免重复覆盖。**
```

- [ ] **Step 2: 更新示例文件名**

找到 `SKILL.md` 第 246 行：

```
python scripts/vision.py record_start --name mission.avi
```

替换为：

```
python scripts/vision.py record_start --name mission.mp4
```

- [ ] **Step 3: 提交**

```bash
git add SKILL.md
git commit -m "docs: SKILL.md 更新录像/拍照说明，补充时间戳命名建议"
```

---

### Task 8: 验证

- [ ] **Step 1: 检查语法无错误**

```bash
uv run python -c "import py_compile; py_compile.compile('scripts/controller.py', doraise=True)"
```

- [ ] **Step 2: 查看最终 diff 确认所有改动**

```bash
git diff HEAD~7..HEAD --stat
git diff HEAD~7..HEAD
```

- [ ] **Step 3: 确认无遗漏**

对照 `docs/superpowers/specs/2026-05-27-vision-record-fix-design.md` 检查所有 7 项改动均已实施。
