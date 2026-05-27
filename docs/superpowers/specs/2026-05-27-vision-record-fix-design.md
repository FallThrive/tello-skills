# 视觉录像/拍照修复设计

## 背景

当前 `controller.py` 的录像和拍照功能存在四个缺陷，导致视频慢动作、录像状态残留、静默失败、文件名无扩展名等问题。

## 改动范围

- `scripts/controller.py` — 核心修复
- `SKILL.md` — 补充文件命名建议

## 详细设计

### 1. 摄像头 FPS 设置

**位置：** `_handle_vision` → `stream_on` 分支（约 controller.py:412）

**改动：** `streamon()` 后追加 `self.tello.set_video_fps(self.tello.FPS_30)`，将摄像头从默认 5 FPS 提升到 30 FPS。

### 2. 默认视频格式改为 MP4

默认文件名 `video_YYYYMMDD_HHMMSS.avi` → `video_YYYYMMDD_HHMMSS.mp4`，自动补扩展名 `.avi` → `.mp4`，编码器 `XVID` → `mp4v`。SKILL.md 示例文件名同步更新。

### 3. 录像去重写入

**位置：** `_record_loop` 方法（约 controller.py:469-479）

**改动：** 用 `is` 比较对象身份追踪上一帧，只有新帧（`BackgroundFrameRead` 收到新数据后创建的新 numpy 数组）才写入 VideoWriter。

**原理：** `BackgroundFrameRead.update_frame()` 每次收到新帧执行 `self.frame = np.array(frame.to_image())` 创建新对象。未收到新帧时，`.frame` 属性返回同一对象，`is` 比较可可靠区分。

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

**效果：** 30 FPS 摄像头 + 30 FPS VideoWriter + 去重 = 视频播放速度与实景一致。

### 4. land 时清理录像状态

**位置：** `_handle_flight` → `land` 分支（约 controller.py:192）

**改动：** 在 `self.tello.land()` 后重置 `_recording`，等待录制线程结束，清理线程引用和文件名。

### 5. record_start 双重保障

**位置：** `_handle_vision` → `record_start` 分支（约 controller.py:434）

**改动：**
- 先自愈：若 `_recording` 残留 True（异常中断未清理），自动重置，等待旧录制线程结束
- 再检查 `_frame_read` 是否为 None，若未开流返回 `"error: stream not started"`
- 最后正常开始录像

### 6. 自定义文件名自动补扩展名

**位置：** `_handle_vision` → `photo` 和 `record_start` 分支

**改动：** 用户提供的 name 不含 `.` 时，photo 自动补 `.jpg`，record_start 自动补 `.mp4`。

### 7. SKILL.md 补充

在 vision.py 模块说明中添加：文件名默认包含时间戳，自定义名称建议带时间戳避免覆盖。

## 影响范围

- **无破坏性变更**：现有 CLI 接口不变，仅修复行为和返回值
- `record_start` 新增返回 `"error: stream not started"` 的场景（以前静默失败）
- `land` 行为变更：降落后自动停止录像（以前需手动 `record_stop`）
