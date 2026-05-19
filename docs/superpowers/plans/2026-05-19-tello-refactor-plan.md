# Tello 技能重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 5 个问题——`--model` 死代码、yolo11→yolo26、拍照录像路径和时间戳、移除平滑、IoU 跟踪锁定同一人

**Architecture:** controller.py 的 `_handle_yolo` 新增 model_type 参数加载 seg/pose 模型，新增 IoU 跟踪逻辑维护锁定目标；task_follow.py 拆分 Seg/Pose 控制器，`--model` 参数路由到对应控制器和模型

**Tech Stack:** Python 3.10+, DJITelloPy, Ultralytics YOLO26, NumPy, OpenCV

---

## 文件职责

| 文件 | 修改内容 |
|------|---------|
| `scripts/controller.py` | 删除 SlidingWindowTracker，`_handle_yolo` 支持 seg/pose 模型加载 + IoU 跟踪，`_handle_vision` 文件路径加子目录和时间戳 |
| `scripts/tasks/task_follow.py` | 拆分 SegFollowController / PoseFollowController，`--model` 路由，删除 target_history 平滑 |
| `scripts/yolo.py` | docstring 删除"含卡尔曼滤波" |
| `scripts/vision.py` | 默认文件名改为带时间戳 |
| `SKILL.md` | yolo11→yolo26，删除"卡尔曼滤波"，更新 task_follow 模型参数说明 |
| `README.md` | YOLO11→YOLO26 |
| `CLAUDE.md` | yolo11n.pt→yolo26n-pose.pt，删除滑动窗口和卡尔曼滤波描述 |

---

### Task 1: 删除 SlidingWindowTracker + 平滑逻辑（controller.py）

**Files:**
- Modify: `scripts/controller.py:29-40,57-58,318-379`

- [ ] **Step 1: 删除 SlidingWindowTracker 类定义**

删除第 29-39 行的 `SlidingWindowTracker` 类：

```python
# 删除 lines 29-39:
# class SlidingWindowTracker:
#     """滑动窗口平滑边界框中心点（基于历史均值的卡尔曼替代方案）"""
#     def __init__(self):
#         self._history: deque = deque(maxlen=5)
#     def update(self, cx: float, cy: float) -> np.ndarray:
#         self._history.append((cx, cy))
#         if len(self._history) >= 3:
#             return np.mean(self._history, axis=0)
#         return np.array([cx, cy])
```

- [ ] **Step 2: 删除 YOLO 相关初始化中的 tracker 实例**

将第 57-58 行：
```python
        # --- YOLO 相关 ---
        self._yolo_model = None
        self._yolo_tracker = SlidingWindowTracker()
```
替换为：
```python
        # --- YOLO 相关 ---
        self._yolo_model = None
        self._tracked_target = None  # IoU 跟踪锁定目标 {'bbox': [x1,y1,x2,y2], 'id': int}
```

- [ ] **Step 3: 替换 _handle_yolo 的 detect 分支——移除 tracker 调用**

将 `_handle_yolo` 中 detect 分支（第 330-374 行）的平滑逻辑改为直接返回离中心最近的人（无平滑），为 Task 2 的 IoU 跟踪打基础。完整替换见 Task 2。

- [ ] **Step 4: 删除 `from collections import deque` 导入中未使用的导入**

检查 `deque` 是否仍被其他地方使用（搜索全文）。如果仅被 SlidingWindowTracker 使用，则删除 `deque` 导入：
```python
# from collections import deque  ← 删除此行
```

- [ ] **Step 5: 验证**

```bash
uv run python -c "from scripts.controller import SlidingWindowTracker" 2>&1
```
预期：ImportError，确认类已删除。

- [ ] **Step 6: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: 删除 SlidingWindowTracker 滑动窗口平滑"
```

---

### Task 2: YOLO 模型切换 + IoU 跟踪（controller.py）

**Files:**
- Modify: `scripts/controller.py:318-381`

- [ ] **Step 1: 重写 _ensure_yolo_model，支持 model_type 参数**

将 `_ensure_yolo_model`（第 318-321 行）替换为：

```python
def _ensure_yolo_model(self, model_type="pose"):
    if self._yolo_model is not None:
        return
    from ultralytics import YOLO
    model_path = f"models/yolo26n-{model_type}.pt"
    logger.info(f"加载 YOLO 模型: {model_path}")
    self._yolo_model = YOLO(model_path)
```

- [ ] **Step 2: 重写 _handle_yolo，支持 model_type 参数 + IoU 跟踪 + seg/pose 检测**

将 `_handle_yolo`（第 323-381 行）完整替换为：

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

def _yolo_detect_seg(self, results, frame_cx, frame_cy):
    """分割模式检测：返回 area（掩码面积），IoU 跟踪锁定"""
    import cv2

    target_info = None
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
```

- [ ] **Step 3: 更新 _dispatch 路由，传递 model_type 给 _handle_yolo**

将 `_dispatch` 中第 120 行的 yolo 路由改为解析参数：

```python
elif module == "yolo":
    # 格式: yolo <action> [--model seg|pose]
    model_type = "pose"  # 默认
    remaining = parts[1:]
    for i, p in enumerate(remaining):
        if p == "--model" and i + 1 < len(remaining):
            model_type = remaining[i + 1]
            break
    return self._handle_yolo(action, model_type)
```

- [ ] **Step 4: 验证**

```bash
uv run python -c "from scripts.controller import TelloController; print('OK')"
```

- [ ] **Step 5: 提交**

```bash
git add scripts/controller.py
git commit -m "feat: YOLO 模型切换 + IoU 跟踪锁定 + seg/pose 双模式检测"
```

---

### Task 3: 拍照/录像文件路径 + 时间戳（controller.py + vision.py）

**Files:**
- Modify: `scripts/controller.py:266-293`
- Modify: `scripts/vision.py:16,19`

- [ ] **Step 1: controller.py 添加目录创建 + 文件路径逻辑**

在 `_handle_vision` 方法开头添加：

```python
def _handle_vision(self, action, args):
    import cv2
    import os
    from datetime import datetime

    # 确保 images/ 和 videos/ 目录存在
    os.makedirs("images", exist_ok=True)
    os.makedirs("videos", exist_ok=True)

    if action == "stream_on":
        ...
```

- [ ] **Step 2: 修改 photo 分支——加入路径和时间戳**

将第 275-277 行 `photo` 分支替换为：

```python
elif action == "photo":
    name = args[0] if args else ""
    if not name:
        name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = os.path.join("images", name)
    cv2.imwrite(path, self._frame_read.frame)
```

- [ ] **Step 3: 修改 record_start 分支——加入路径和时间戳**

将第 278-281 行替换为：

```python
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
```

- [ ] **Step 4: 更新 scripts/vision.py 默认文件名**

将 `scripts/vision.py:16` 和 `scripts/vision.py:19` 的默认值改为空字符串（让 controller 生成时间戳名）：

```python
p_photo.add_argument('--name', '-n', default='')
p_rec_start.add_argument('--name', '-n', default='')
```

- [ ] **Step 5: 验证**

```bash
# 语法检查
uv run python -c "from scripts.controller import TelloController; print('OK')"
# 验证目录自动创建
python -c "import os; os.makedirs('images', exist_ok=True); os.makedirs('videos', exist_ok=True); print('dirs created')"
```

- [ ] **Step 6: 提交**

```bash
git add scripts/controller.py scripts/vision.py
git commit -m "feat: 拍照存入 images/，录像存入 videos/，默认名带时间戳"
```

---

### Task 4: 拆分 FollowController + 激活 --model 参数（task_follow.py）

**Files:**
- Modify: `scripts/tasks/task_follow.py`

- [ ] **Step 1: 替换 FollowController 为 SegFollowController**

删除旧的 `FollowController` 类（第 15-57 行），替换为（无平滑版本）：

```python
class SegFollowController:
    """分割模式比例控制器——用像素面积控制前后距离"""

    def __init__(self, center_x=480, center_y=360):
        self.center_x = center_x
        self.center_y = center_y
        self.kp_yaw = 0.2
        self.kp_ud = 0.3
        self.fb_speed_val = 15
        self.area_min = 100000
        self.area_max = 150000

    def update(self, target_info):
        if target_info is None:
            return (0, 0, 0, 0)

        cx, cy = target_info['center']
        area = target_info.get('area', self.area_min + 1)

        error_x = cx - self.center_x
        yaw_speed = int(self.kp_yaw * error_x)
        yaw_speed = max(-50, min(50, yaw_speed))

        error_y = self.center_y - cy
        ud_speed = int(self.kp_ud * error_y)
        ud_speed = max(-50, min(50, ud_speed))

        if area < self.area_min:
            fb_speed = self.fb_speed_val
        elif area > self.area_max:
            fb_speed = -self.fb_speed_val
        else:
            fb_speed = 0

        return (0, fb_speed, ud_speed, yaw_speed)


class PoseFollowController:
    """姿态模式比例控制器——用躯干高度控制前后距离"""

    def __init__(self, center_x=480, center_y=360):
        self.center_x = center_x
        self.center_y = center_y
        self.kp_yaw = 0.2
        self.kp_ud = 0.3
        self.fb_speed_val = 15
        self.height_min = 200
        self.height_max = 250

    def update(self, target_info):
        if target_info is None:
            return (0, 0, 0, 0)

        cx, cy = target_info['center']
        torso_height = target_info.get('torso_height', 0)
        has_hips = target_info.get('has_hips', False)

        error_x = cx - self.center_x
        yaw_speed = int(self.kp_yaw * error_x)
        yaw_speed = max(-50, min(50, yaw_speed))

        error_y = self.center_y - cy
        ud_speed = int(self.kp_ud * error_y)
        ud_speed = max(-50, min(50, ud_speed))

        if has_hips:
            if torso_height < self.height_min:
                fb_speed = self.fb_speed_val
            elif torso_height > self.height_max:
                fb_speed = -self.fb_speed_val
            else:
                fb_speed = 0
        else:
            fb_speed = 0

        return (0, fb_speed, ud_speed, yaw_speed)
```

- [ ] **Step 2: 更新 main()——根据 --model 选择控制器 + 传递 model_type 给 yolo detect**

将 `main()` 中第 88 行：

```python
controller = FollowController(center_x=480, center_y=360)
```

替换为：

```python
if args.model == 'pose':
    controller = PoseFollowController(center_x=480, center_y=360)
else:
    controller = SegFollowController(center_x=480, center_y=360)
```

- [ ] **Step 3: 更新 yolo detect 调用——传递 --model 参数**

将第 100 行：

```python
result = send_command("yolo detect")
```

替换为：

```python
result = send_command(f"yolo detect --model {args.model}")
```

- [ ] **Step 4: 更新任务循环——处理新的 JSON 返回格式**

IoU 跟踪丢失时 `_handle_yolo` 返回 `{}`（空 JSON），更新第 107-116 行的处理逻辑：

```python
try:
    target = json.loads(result)
except json.JSONDecodeError:
    target = {}

if not target:
    send_command("flight rc 0 0 0 0")
    send_command("matrix static b ?")
    time.sleep(0.1)
    continue

lr, fb, ud, yaw = controller.update(target)
send_command(f"flight rc {lr} {fb} {ud} {yaw}")

# LED 屏显距离信息
if args.model == 'seg':
    area_k = int(target.get('area', 0) // 1000)
    send_command(f"matrix static r {area_k}k")
else:
    h = int(target.get('torso_height', 0))
    send_command(f"matrix static r {h}h")

time.sleep(0.05)
```

- [ ] **Step 5: 清理不再需要的导入**

移除 `from collections import deque` 和 `import numpy as np`（不再需要）：

```python
# 删除:
# from collections import deque
# import numpy as np
```

- [ ] **Step 6: 验证**

```bash
# 语法检查
uv run python -m py_compile scripts/tasks/task_follow.py
# 检查 argparse 参数
uv run python scripts/tasks/task_follow.py --help
```

- [ ] **Step 7: 提交**

```bash
git add scripts/tasks/task_follow.py
git commit -m "refactor: 拆分 Seg/Pose 控制器，激活 --model 参数，删除平滑"
```

---

### Task 5: 更新代码注释和文档

**Files:**
- Modify: `scripts/yolo.py:2`
- Modify: `SKILL.md`
- Modify: `README.md:55`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新 yolo.py docstring**

将 `scripts/yolo.py:2` 的 docstring 从 `"""YOLO 人员检测 CLI（含卡尔曼滤波）"""` 改为：

```python
"""YOLO 人员检测 CLI"""
```

- [ ] **Step 2: 更新 SKILL.md 中的 yolo 相关内容**

需要修改的 SKILL.md 位置：

**Line 103** — yolo.py 描述从 "YOLO 检测 + 滑动窗口平滑" 改为：
```
### yolo.py — YOLO 检测 + IoU 跟踪
```

**Lines 106-108** — 从：
```
python scripts/yolo.py detect          # 检测人员，输出滑动窗口平滑后的边界框 JSON
python scripts/yolo.py count           # 检测人员，输出人数
```
改为：
```
python scripts/yolo.py detect          # 检测人员（IoU 跟踪锁定），输出 JSON
python scripts/yolo.py count           # 检测人员，输出人数
```

**Line 110** — 删除 "内部使用滑动窗口（最近 5 帧取均值）平滑边界框中心，减少抖动。"

**task_follow.py 描述（Lines 139-151）** — 更新为：
```
### task_follow.py — 实时人员跟随

YOLO 检测人员，通过 IoU 跟踪锁定同一人，比例控制器驱动 rc_control 实时跟随。

```
python scripts/tasks/task_follow.py [--duration 120] [--model seg]
```

参数：
- `--duration`：跟随时长秒（默认 120）
- `--model`：跟踪模型，`pose`（躯干高度控制距离）或 `seg`（分割面积控制距离），默认 `pose`

行为：YOLO 检测 → IoU 锁定（丢失即悬停） → 根据模型类型计算距离 → 比例控制器 → rc_control → LED 红灯 + 屏显距离信息 → 循环。
TOF 仅作为紧急安全下限（最小 50cm），不参与正常距离控制。
```

- [ ] **Step 3: 更新 README.md**

将 `README.md:55` 的 `YOLO11` 改为 `YOLO26`。

- [ ] **Step 4: 更新 CLAUDE.md**

需要修改的位置：

**Line 68**: 从"YOLO 模型懒加载"行删除 "首次调用 `yolo detect/count` 时才加载 `yolo11n.pt`，节省内存"
改为 "首次调用 `yolo detect/count` 时才加载模型，seg 模式加载 `models/yolo26n-seg.pt`，pose 模式加载 `models/yolo26n-pose.pt`"

**Line 69**: 删除 "滑动窗口平滑：`SlidingWindowTracker`（5 帧均值）用于检测/跟踪中平滑边界框中心点"

- [ ] **Step 5: 验证**

```bash
grep -rn "yolo11\|YOLO11\|卡尔曼滤波\|滑动窗口平滑\|SlidingWindowTracker" --include="*.py" --include="*.md" .
```
预期：无输出（所有引用已更新）。

- [ ] **Step 6: 提交**

```bash
git add scripts/yolo.py SKILL.md README.md CLAUDE.md
git commit -m "docs: yolo11→yolo26，删除平滑/卡尔曼滤波相关描述"
```

---

## 执行顺序

```
Task 1 (删除平滑) → Task 2 (YOLO模型+IoU) → Task 3 (文件路径) → Task 4 (FollowController) → Task 5 (文档)
```

Tasks 1-3 都改 controller.py，按顺序执行避免冲突。Task 4 改 task_follow.py 独立。Task 5 是纯文档更新，放最后。

## 验证清单

1. `grep -rn "yolo11\|YOLO11\|卡尔曼滤波\|滑动窗口平滑\|SlidingWindowTracker" --include="*.py" --include="*.md" .` — 无残留引用
2. `grep -rn "yolo26" --include="*.py" --include="*.md" .` — 新版本号已应用
3. `uv run python -c "from scripts.controller import SlidingWindowTracker"` — ImportError（类已删除）
4. `uv run python -m py_compile scripts/controller.py scripts/tasks/task_follow.py scripts/yolo.py scripts/vision.py` — 语法无误
5. `uv run python scripts/tasks/task_follow.py --help` — 参数正常
