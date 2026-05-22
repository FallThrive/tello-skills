# Tello 摄像头预览 + 标注画面：设计文档

## 背景与动机

当前系统支持 `stream_on` / `stream_off`、`photo`、`record_start` / `record_stop`，但无法在屏幕上实时查看摄像头画面。YOLO 检测和挑战卡搜索的结果只能通过 JSON 文本或保存的图片查看，缺少可视化反馈。

需求：
1. 可手动开启前视/下视纯净预览窗口（画面 + 电池 + 方向标识）
2. `yolo detect` 时自动弹出标注预览窗口（检测框、标签、置信度、关键点骨架/分割轮廓、track ID）
3. `mission_pad detect` 和 `task_search_pad` 时弹出下视标注预览窗口（挑战卡编号 + 坐标）

## 关键决策

- **Controller 内部 daemon 线程**：预览窗口作为 daemon 线程在 controller 进程中运行，模式与 `recorder-daemon`、`task-follow` 一致。DJITelloPy 仅支持单路视频流，独立进程方案不可行。
- **手动 + 自动混合生命周期**：纯净预览手动控制，YOLO/挑战卡标注预览由对应检测命令自动触发，各自独立启停。
- **PyAV 帧为 RGB 格式**：DJITelloPy 的 `BackgroundFrameRead.frame` 来自 `av.open().decode()` → `frame.to_image()` → `np.array()`，输出 RGB 通道顺序。OpenCV 的 `imshow` / `imwrite` 期望 BGR，前视需 `cvtColor`。转换行以可注释形式编写，方便实际测试后调整。

## 新增命令总览

| 命令 | 功能 |
|------|------|
| `vision preview_start forward\|downward` | 开启纯净预览（画面 + Bat + 方向） |
| `vision preview_stop forward\|downward` | 关闭指定纯净预览 |
| `vision preview_yolo_stop` | 关闭 YOLO 标注预览 |
| `mission_pad detect` | 开启下视画面 + 持续检测挑战卡，返回 JSON，不亮灯不显矩阵 |

YOLO 标注预览**没有 start 命令**——由 `yolo detect` 自动触发。

## 线程架构

### 新增 daemon 线程

| 线程名 | 数量 | 触发方式 | 关闭方式 |
|--------|------|----------|----------|
| `preview-forward` | 最多 1 | `preview_start forward` | `preview_stop forward` 或关闭窗口 |
| `preview-downward` | 最多 1 | `preview_start downward` | `preview_stop downward` 或关闭窗口 |
| `preview-yolo` | 最多 1 | `yolo detect` 自动创建 | `preview_yolo_stop` 或关闭窗口 |
| `preview-pad` | 最多 1 | `mission_pad detect` 或 `task_search_pad` 触发 | 任务结束或关闭窗口 |

### 线程循环结构

所有预览线程遵循统一模式：

```python
while not stopped:
    _state_lock → 快照 fr 引用           # 微秒级持锁
    锁外 → 画面处理（裁切/旋转/绘图）     # 重活，无锁
    锁外 → imshow + waitKey(1)          # OpenCV GUI
    每 ~30 帧 → _flight_lock → get_battery()  # 毫秒级持锁
```

### 锁安全

遵循现有三锁嵌套规则（`_state_lock` 始终最内层，`_flight_lock` 与 `_model_lock` 不同分支互斥）。预览线程仅在快照帧引用和读共享状态时短暂持 `_state_lock`。

`preview-yolo` 例外：需持 `_model_lock` 做推理，但 `_model_lock` 与 `_flight_lock` 互不嵌套。当 `preview-yolo` 运行时，CLI `yolo detect` 读共享状态（`_state_lock` 下）无需获取 `_model_lock`。

### 与既有线程的关系

| 线程 | 帧来源 | 推理 |
|------|--------|------|
| `preview-forward/downward` | 直接读 `_frame_read` | 无 |
| `preview-pad` | 直接读 `_frame_read` + 共享状态的挑战卡结果 | 无（由命令线程更新共享状态） |
| `preview-yolo` | 直接读 `_frame_read` | **自己跑推理**（`_model_lock` 下 `model.track()`） |

`preview-yolo` 是唯一自己做推理的预览线程——持续运行 `model.track(persist=True)` 以维持 BoT-SORT 跨帧跟踪，推理结果（detections、keypoints/masks）写入共享状态后自己绘制。其余预览线程只显示不推理。

## 帧处理

### 前视摄像头

```
原生帧 (960x720, RGB)
  → frame = fr.frame
  → frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)  # 如不需要可注释
  → 直接使用
```

### 下视摄像头

下视输出分辨率可能是 320x240 或 320x720。仅顶部 320x240 为有效画面，下方（若存在）为绿色无效填充。

```
原生帧 (320x720 或 320x240, 灰度 IR)
  → 裁切：frame[:240, :]（取顶部 320x240）
  → 旋转：cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
  → 240x320 竖屏
  → 不做 RGB→BGR 转换（灰度图无意义）
```

## 各窗口画面内容

通用：所有窗口底部叠加状态栏（半透明黑底白字），不含 emoji。

### 纯净预览窗口

```
┌──────────────────────┐
│                      │
│      摄像头画面       │
│                      │
│                      │
├──────────────────────┤
│  FWD  Bat: 85%       │
└──────────────────────┘
```

- 方向标识：`FWD`（前视）或 `DOWN`（下视，裁切旋转后）
- 状态栏格式：`{FWD|DOWN}  Bat: {battery}%`

### YOLO 标注预览窗口

每个检测到的人在框旁标注 `person {conf} [{track_id}]`。

- **锁定目标**（离中心最近或已锁定的 track ID）：绿色检测框 + 完整 COCO 17 点骨架 / seg mask 轮廓
- **未锁定目标**：红色检测框 + 完整 COCO 17 点骨架 / seg mask 轮廓

底部状态栏：

```
Locked: {track_id}  torso:{N}px  Bat: {battery}%    # pose 模式
Locked: {track_id}  area:{N}k  Bat: {battery}%      # seg 模式
```

目标丢失时：

```
Locked: ?  Bat: {battery}%
```

跟踪逻辑：首次选离画面中心最近的人并锁定 track ID，后续帧按 track ID 精确匹配。目标丢失后持续等待同一 track ID 重现（BoT-SORT ReID 支持），不主动切换目标。仅在收到 `preview_yolo_stop` 或关窗时结束。

### 挑战卡标注预览窗口

```
┌──────────────────────┐
│                      │
│   下视画面 240x320    │
│                      │
│                      │
├──────────────────────┤
│  Pad:#3  (12,34,60)  Bat: 85%  │
└──────────────────────┘
```

- 画面中心不叠加编号，只在状态栏显示
- 检测到时：`Pad:#{id}  ({x},{y},{z})  Bat: {battery}%`
- 未检测到时：`Pad:--  Bat: {battery}%`

## 命令详细行为

### vision preview_start forward|downward

1. `set_video_direction(CAMERA_FORWARD/CAMERA_DOWNWARD)`
2. 若流未开则 `streamon()` + `get_frame_read()`
3. 若指定方向已有预览线程则返回 `ok`（幂等）
4. 启动 `preview-{dir}` daemon 线程

### vision preview_stop forward|downward

1. 设置停止标志
2. `cv2.destroyWindow({window_name})`
3. 等待线程退出
4. 注意：不关闭视频流（其他消费者可能仍在使用）

### vision preview_yolo_stop

1. 设置停止标志
2. `cv2.destroyWindow("YOLO")`
3. 等待 `preview-yolo` 线程退出
4. 同时通知 `_handle_yolo` 不再写入共享状态

### yolo detect（改动）

调用时：
1. 若 `preview-yolo` 线程未运行则自动启动 daemon 线程
2. `preview-yolo` 内部持续 `model.track(persist=True)`（`_model_lock` 下），写入共享状态后绘制显示
3. `_handle_yolo` 等待 `preview-yolo` 完成首帧推理后，读取共享状态中的 detections 返回 JSON
4. 后续 `yolo detect` 调用（preview 已运行时）直接读共享状态返回 JSON，无需等待推理

### mission_pad detect（新增）

```
python scripts/mission_pad.py detect [--duration 10]
```

1. 切换下视摄像头 + 开启视频流
2. 启动 `preview-pad` daemon 线程
3. 循环读取挑战卡 ID + 坐标，更新共享状态
4. 超时后（默认 10 秒）关闭预览，返回最后一次检测结果
5. 返回 JSON：`{"id": N, "x": val, "y": val, "z": val}`，未检测到时 id = -1

### task_search_pad.py（改动）

1. 首次检测挑战卡时调用 `mission_pad detect` 开启预览（而非 `mission_pad id`）
2. 到达任务点后：`mission_pad disable` 前自动关闭预览
3. 未找到也关闭预览

## 挑战卡编号校验修正

当前 `task_search_pad.py` 和 `_handle_mission_pad fly` 仅检查 `pad_id > 0`，会接受 9+ 等非法编号。

修正：
- `task_search_pad.py`：`pad_id > 0` → `1 <= pad_id <= 8`
- `_handle_mission_pad fly`：增加 `if not 1 <= pad_id <= 8: return "error: invalid pad id, must be 1-8"`
- `mission_pad detect`：输出中也仅当 `1 <= id <= 8` 时视为有效检测

## RGB/BGR 既有 bug 同步修复

现存 `_handle_vision` photo 的 `cv2.imwrite(path, fr.frame)` 和 `_record_loop` 的 `out.write(fr.frame)` 直接使用 RGB 帧数据（PyAV 解码输出为 RGB，而 OpenCV imwrite/VideoWriter 期望 BGR）。本次一并修复为：

```python
frame = fr.frame
frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
cv2.imwrite(path, frame)
```

录像同理。

## 改动文件清单

| 文件 | 改动描述 |
|------|----------|
| `scripts/controller.py` | 新增 4 种预览线程 + 共享状态字段 + `_handle_mission_pad` detect + pad_id 校验 + RGB→BGR 修复 |
| `scripts/vision.py` | 新增 `preview_start` / `preview_stop` / `preview_yolo_stop` 子命令 |
| `scripts/yolo.py` | 无需改动（`yolo detect` 自动弹窗由 controller `_handle_yolo` 处理） |
| `scripts/mission_pad.py` | 新增 `detect` 子命令 |
| `scripts/tasks/task_search_pad.py` | 改用 `mission_pad detect` + 自动关预览 + pad_id 1-8 校验 |

## 验证方案

1. **纯净预览**（需真实无人机）：
   ```bash
   uv run scripts/vision.py preview_start forward
   # 确认窗口显示前视画面 + FWD Bat: N%
   uv run scripts/vision.py preview_stop forward
   # 确认窗口关闭
   ```

2. **下视预览**：
   ```bash
   uv run scripts/vision.py preview_start downward
   # 确认为裁切旋转后的 240x320 竖屏 + DOWN Bat: N%
   uv run scripts/vision.py preview_stop downward
   ```

3. **纯净预览共存**：
   ```bash
   uv run scripts/vision.py preview_start forward
   uv run scripts/vision.py preview_start downward
   # 确认两个窗口同时运行
   ```

4. **YOLO 标注预览**：
   ```bash
   uv run scripts/vision.py preview_start forward  # 先开纯净前视
   uv run scripts/yolo.py detect                    # 自动弹出标注窗口
   uv run scripts/vision.py preview_yolo_stop      # 关闭标注窗口
   # 确认纯净前视窗口仍运行
   ```

5. **挑战卡检测**：
   ```bash
   uv run scripts/mission_pad.py detect --duration 10
   # 确认窗口显示下视画面 + Pad:#N 坐标
   ```

6. **挑战卡编号校验**：验证 pad_id 9+ 被正确拒绝。
