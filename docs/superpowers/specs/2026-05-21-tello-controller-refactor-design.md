# Tello Controller 三合一改造：设计文档

## 背景与动机

当前 Tello 无人机控制器 `scripts/controller.py` 存在三个架构层面问题：

1. **手动 IoU 跟踪不够鲁棒**：`_iou_match()` 仅靠 IoU 阈值 0.3 匹配检测框，无卡尔曼滤波和运动预测，目标快速移动或短暂遮挡时容易丢失。
2. **所有命令共享单锁**：YOLO 推理（50-200ms）和拍照在 `self._lock` 下阻塞飞行命令（RC 速度更新、紧急降落）。
3. **跟踪循环存在 TCP 往返延迟**：`task_follow.py` 每次迭代两次 TCP 串行往返（`yolo detect` → 等响应 → `flight rc` → 等响应），RC 更新频率受限于 ~10Hz。

## 改造目标

一次性完成三个方向（方向 2 是方向 3 的前置条件）：

| 方向 | 描述 | 核心收益 |
|------|------|----------|
| 1. model.track() | ultralytics 内置 BoT-SORT/ByteTrack 替换手动 IoU | 卡尔曼滤波 + 运动预测，遮挡后重识别 |
| 2. 读写分离 + 多线程 | 三锁替换单锁，ThreadPoolExecutor 处理命令 | YOLO/拍照不阻塞飞行，支持并发命令 |
| 3. task follow 闭环 | 跟踪循环移入 controller，消除 TCP 往返 | RC 更新频率从 ~10Hz 提升到 ~20Hz |

## 关键决策

- **不引入 WebSocket**：保持 TCP 短连接文本协议不变，加入 `task status` 轮询命令替代实时推送
- **不改造 task_search_pad**：其步进周期约 1 秒，TCP 延迟可忽略，闭环收益近乎为零
- **P 控制器参数可配置**：各系数提供合理默认值，CLI 可通过参数覆盖
- **全部 CLI 脚本零改动**（`task_follow.py` 除外）：命令字符串格式和返回值格式完全兼容

## 锁定与线程架构

三把锁替换当前的 `self._lock`，按职责划分：

| 锁 | 保护内容 | 持有者 |
|---|---|---|
| `_flight_lock` | 所有 `self.tello.*` UDP 通信（DJITelloPy 非线程安全） | flight, sensor, led, matrix, mission_pad, vision stream_on/off, 心跳 RC, task follow RC |
| `_model_lock` | `self._yolo_model()` / `.track()` 推理调用 | yolo detect/count, task follow 推理 |
| `_state_lock` | `_running`, `_last_cmd_time`, `_recording`, `_frame_read`, `_follow_stop`, `_follow_thread`, `_follow_target_id` | 所有线程（最内层锁，短暂持有） |

**锁嵌套规则（防死锁）**：
- `_state_lock` 始终是最内层锁
- `_flight_lock` 和 `_model_lock` 互不嵌套——不同命令分支互斥
- 嵌套时顺序：外层锁 → `_state_lock`

### 线程清单

| 线程 | 用途 | 关键锁 |
|------|------|--------|
| 主线程 | TCP accept 循环 + 提交 ThreadPoolExecutor(max_workers=4) | 无 |
| heartbeat-daemon | 每 10s 空闲检测 + 发送 `rc(0,0,0,0)` 保活 | `_state_lock`（读时间）, `_flight_lock`（发 RC） |
| recorder-daemon | 录像帧写入磁盘 | `_state_lock`（读标志和帧引用），锁外写帧 |
| pool-worker (≤4) | 执行单次命令，task follow 阻塞一个 | 按命令类型分派 |
| task-follow thread | YOLO + RC 控制闭环（daemon） | `_model_lock`（推理）, `_flight_lock`（RC + LED）, `_state_lock`（帧引用 + 停止事件） |

**关键安全保证**：`task follow` 阻塞一个 pool worker，但主 accept 循环和其他 3 个 worker 仍可接收紧急命令（`flight land`, `task stop`）。

## `_dispatch` 命令路由

按模块类型在分派时获取对应锁：

```
dispatch(parts)
  │
  ├─ flight / sensor / led / matrix / mission_pad
  │     └─ with _flight_lock: handler()         # 调 _update_cmd_time()
  │
  ├─ vision stream_on / stream_off
  │     └─ with _flight_lock: handler()         # 调 _update_cmd_time()
  │
  ├─ vision photo
  │     └─ handler()                             # _state_lock 快照帧引用，锁外 imwrite
  │
  ├─ vision record_start / record_stop
  │     └─ handler()                             # _state_lock 保护标志和线程引用
  │
  ├─ yolo detect / count
  │     └─ with _model_lock: handler()           # _state_lock 快照帧引用
  │
  └─ task follow / stop / status
        └─ _handle_task()                        # 内部管理锁
```

**心跳计时规则**：仅与无人机 UDP 通信的命令调用 `_update_cmd_time()`。YOLO 推理和拍照不重置心跳计时器——心跳只关心无人机连接是否还活跃。

| 命令 | 锁 | 更新心跳计时 |
|------|-----|:--:|
| flight.*, sensor.*, led.*, matrix.*, mission_pad.* | `_flight_lock` | 是 |
| vision stream_on/off | `_flight_lock` | 是 |
| vision photo | `_state_lock` | 否 |
| vision record_start/stop | `_state_lock` | 否 |
| yolo detect/count | `_model_lock` + `_state_lock` | 否 |
| task follow/stop/status | 内部管理 | 是（RC 时） |

## 方向 1 详细：model.track() 替换手动 IoU

### API 变更

```python
# 旧（detect 模式，不追踪）
results = self._yolo_model(frame, classes=[0], verbose=False)
# results[0].boxes.id → 不存在

# 新（track 模式，开启内置追踪）
results = self._yolo_model.track(frame, classes=[0], persist=True, verbose=False)
# results[0].boxes.id → tensor(N,) 或 None，float 类型需转 int
```

`yolo count` 保持使用 `model()` 无需追踪。`_ensure_yolo_model()` 无需修改（同个 `.pt` 文件同时支持 detect 和 track）。

### 新增 `_track_match()` 替换 `_iou_match()`

```
首帧：选离画面中心最近的人，记录 track_id
后续帧：按 track_id 精确匹配（O(n) 遍历筛选）
丢失：track_id 不再出现则返回 None
```

不再需要 IoU 阈值、面积计算、bbox 重叠判断——ultralytics 内部已完成跨帧身份关联。

### 新增 `_parse_track_detections(result, model_type)`

从 `model.track()` 结果中统一提取检测列表，包含 bbox、center、track_id 及模式特有字段（area / torso_height + has_hips）。

当前 `_yolo_detect_pose`（约 55 行）和 `_yolo_detect_seg`（约 35 行）中约 80% 代码为重复的 bbox/关键点/掩码提取逻辑。`_parse_track_detections` 统一此逻辑，供：
- `_yolo_detect_pose` / `_yolo_detect_seg`（CLI `yolo detect` 路径）
- `_task_follow_loop`（闭环跟踪路径）

三处复用，消除重复。

### 删除

- `_iou_match()` 方法（约 40 行，`controller.py:445-483`）
- `self._tracked_target` 实例变量（`controller.py:44`）

### yolo detect CLI 返回格式（向下兼容）

seg 模式新增 `track_id`：
```json
{"bbox": [x1,y1,x2,y2], "center": [cx,cy], "area": 12345.0, "track_id": 1}
```

pose 模式新增 `track_id`：
```json
{"bbox": [x1,y1,x2,y2], "center": [cx,cy], "torso_height": 180.5, "has_hips": true, "track_id": 1}
```

未检测到目标时返回空 JSON `{}`（不变）。

## 方向 3 详细：task follow 闭环 + task status

### 新增命令

| 命令 | 功能 | 返回 |
|------|------|------|
| `task follow --model <pose\|seg> --duration <秒> [P参数...]` | 启动闭环跟踪 | 跟踪完成后返回 `ok` |
| `task stop` | 停止正在运行的跟踪 | 立即返回 `ok` |
| `task status` | 查询跟踪状态 | 立即返回 JSON |

### task status 返回格式

```json
{
  "running": true,
  "model": "pose",
  "elapsed": 12.5,
  "duration": 60,
  "track_id": 1,
  "rc_speed": {"lr": 0, "fb": 15, "ud": -10, "yaw": 5},
  "tof_distance": 250,
  "target": {
    "center": [450, 320],
    "torso_height": 210.5,
    "has_hips": true
  }
}
```

跟踪未启动或已结束：`{"running": false}`

### P 控制器默认参数

所有参数有出厂默认值，CLI 可选覆盖：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--kp-yaw` | 0.2 | 水平偏转比例系数（pixels → RC speed） |
| `--kp-ud` | 0.3 | 垂直方向比例系数 |
| `--fb-speed` | 15 | 前后移动速度绝对值（cm/s） |
| `--dist-low` | pose: 200 px, seg: 100000 px | 目标距离下限（低于此前进） |
| `--dist-high` | pose: 250 px, seg: 150000 px | 目标距离上限（高于此后退） |

固定参数（非 CLI 可配）：`frame-cx=480`, `frame-cy=360`（720p 画面中心）, RC 限幅 ±50。

### `_task_follow_loop` 闭环循环

```
初始化：
  kp_yaw, kp_ud, fb_speed_val, dist_low, dist_high（从参数或默认值）
  local_track_id = None
  _ensure_yolo_model(model_type)（_model_lock）

while 未超时 且 _follow_stop 未设置:
  ├─ TOF 检测（_flight_lock）
  │   └─ 100 <= dist < 500 → 紧急停止 break
  ├─ 读帧（_state_lock 快照 fr = self._frame_read）
  │   └─ fr is None → sleep(0.05), continue
  ├─ YOLO track 推理（_model_lock）
  │   └─ results = self._yolo_model.track(frame, classes=[0], persist=True, verbose=False)
  ├─ 解析检测（_parse_track_detections）
  ├─ track ID 匹配（局部变量 local_track_id）
  │   ├─ 有锁定 → 过滤相同 track_id
  │   └─ 无锁定 → 选离中心最近目标
  ├─ P 控制计算（_compute_p_controls）
  │   └─ 返回 (lr, fb, ud, yaw)，限幅 ±50
  ├─ send_rc_control（_flight_lock + _update_cmd_time）
  ├─ LED 矩阵显示距离（_flight_lock）
  └─ sleep(0.05)

finally:
  send_rc_control(0, 0, 0, 0)  # 悬停
```

**关键设计决策**：
- `local_track_id` 为局部变量，不污染 `self._follow_target_id`（后者供 CLI `yolo detect` 独立使用的 `_track_match()` 用）
- TOF 紧急停止阈值 `100 <= dist < 500`（1-5 米）与 `task_follow.py` 原 `emergency_check()` 一致
- LED 矩阵显示需持 `_flight_lock`（走扩展端口 UDP）

### `_compute_p_controls(target, model_type, ...)`

统一的 P 控制器，pose 模式用 `torso_height` 控制前后距离，seg 模式用 `area`。

```python
def _compute_p_controls(self, target, model_type, frame_cx, frame_cy,
                         kp_yaw, kp_ud, fb_speed_val, dist_low, dist_high):
    cx, cy = target['center']
    yaw = max(-50, min(50, int(kp_yaw * (cx - frame_cx))))
    ud = max(-50, min(50, int(kp_ud * (frame_cy - cy))))
    
    if model_type == 'pose':
        if target.get('has_hips'):
            fb = fb_speed_val if target['torso_height'] < dist_low else \
                 -fb_speed_val if target['torso_height'] > dist_high else 0
        else:
            fb = 0
    else:
        area = target.get('area', dist_low + 1)
        fb = fb_speed_val if area < dist_low else \
             -fb_speed_val if area > dist_high else 0
    
    return (0, fb, ud, yaw)
```

### task_follow.py 简化

从 174 行缩减到约 65 行：

- **删除**：`SegFollowController`（第 13-47 行）、`PoseFollowController`（第 50-88 行）、`emergency_check()`（第 91-97 行）
- **保留**：参数解析（argparse），信号处理骨架
- **核心改动**：主循环替换为单次 `send_command(f"task follow --model {args.model} --duration {args.duration}", timeout=args.duration + 15)`

SIGINT 中断路径：
1. 信号处理器在**新** TCP 连接上发送 `task stop`
2. Controller 中 `_follow_stop` 被设置
3. 跟踪线程退出循环，`finally` 发送悬停
4. 原始 pool worker 返回 `"ok"`，`send_command` 的 `recv` 获得响应
5. 主流程继续清理（led off, matrix off, stream_off）

## 改动文件清单

| 文件 | 改动描述 | 行数变化 |
|------|----------|:--:|
| `scripts/controller.py` | 三锁 + ThreadPoolExecutor + model.track() + task follow/stop/status + _parse_track_detections + _compute_p_controls + _track_match + _handle_client | 565 → ~650 |
| `scripts/_client.py` | `send_command` 新增可选 `timeout` 参数（默认 5.0） | 28 → 30 |
| `scripts/tasks/task_follow.py` | 删除 P 控制器类和 emergency_check，简化为发送 task follow | 174 → ~65 |
| `scripts/yolo.py` | **无改动** | — |
| `scripts/vision.py` | **无改动** | — |
| `scripts/flight.py` | **无改动** | — |
| `scripts/sensor.py` | **无改动** | — |
| `scripts/led.py` | **无改动** | — |
| `scripts/matrix.py` | **无改动** | — |
| `scripts/mission_pad.py` | **无改动** | — |

## 实现顺序

1. **方向 2 基础**：锁分离 + 多线程 accept（_flight_lock, _model_lock, _state_lock, ThreadPoolExecutor）
2. **方向 1**：model.track() + _track_match() + _parse_track_detections()，删除 _iou_match()
3. **方向 3**：task follow/stop/status + _task_follow_loop + _compute_p_controls
4. **简化**：task_follow.py + _client.py timeout

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 三锁死锁 | `_state_lock` 始终最内层；`_flight_lock` 和 `_model_lock` 在不同 dispatch 分支互斥，永不同时持有 |
| `model.track(persist=True)` 内部状态泄漏 | ultralytics 内部约 30 帧后自动清理过期 track，无需手动管理 |
| task follow 长期占用 pool worker | max_workers=4，剩余 3 个可处理 flight land / task stop |
| SIGINT 时 send_command 嵌套 | task stop 走独立 TCP 连接；Python PEP 475 自动重试 EINTR 的 recv |
| stream_off 与帧读取竞争 | `_state_lock` 保证读者拿到有效引用或 None 的快照；DJITelloPy BackgroundFrameRead 在 streamoff 后仍可返回最后一帧 numpy 数组 |
| YOLO 非线程安全 | `_model_lock` 严格串行化所有推理调用；task follow 推理期间 CLI yolo detect 短暂阻塞（~50ms），可接受 |

## 验证方案

1. **基础命令回归**：依次运行所有 CLI 脚本的核心命令
   ```bash
   uv run scripts/flight.py takeoff
   uv run scripts/sensor.py battery
   uv run scripts/sensor.py tof
   uv run scripts/led.py solid 255 0 0
   uv run scripts/led.py off
   uv run scripts/vision.py stream_on
   uv run scripts/vision.py photo
   uv run scripts/vision.py record_start && sleep 2 && uv run scripts/vision.py record_stop
   uv run scripts/vision.py stream_off
   uv run scripts/flight.py land
   ```
   确认与改造前行为一致。

2. **YOLO 检测兼容性**：
   ```bash
   uv run scripts/yolo.py detect
   uv run scripts/yolo.py count
   ```
   确认返回格式不变（JSON 增加 track_id 字段），空检测返回 `{}`。

3. **跟踪闭环**（需真实无人机）：
   ```bash
   uv run scripts/tasks/task_follow.py --duration 10 --model pose
   # 另开终端：
   uv run scripts/flight.py rc 0 0 0 0  # 检查 task status
   ```
   确认：跟踪循环运行、Ctrl+C 中断后悬停、TOF 紧急停止。

4. **并发安全**（需真实无人机）：
   ```bash
   # 终端 1
   uv run scripts/tasks/task_follow.py --duration 60 --model pose
   # 终端 2（跟踪期间）
   uv run scripts/flight.py land
   ```
   确认能正常降落，无死锁。
