# Tello 技能重构设计

**日期：** 2026-05-19
**状态：** 已批准

## 背景

本轮重构解决 `scripts/` 下 5 个问题：`--model` 参数死代码、yolo11→yolo26 版本更新、拍照录像文件路径和时间戳、文档与代码平滑策略不一致、YOLO 检测无法锁定同一人。

## 设计

### 1. YOLO 模型切换（yolo11 → yolo26 + seg/pose）

**controller.py `_handle_yolo`：**
- 新增 `model_type` 参数（`seg` / `pose`），控制加载哪个模型和返回哪种检测结果
- `seg`：加载 `models/yolo26n-seg.pt`，返回 `area`（分割掩码面积）
- `pose`：加载 `models/yolo26n-pose.pt`，返回 `torso_height` + `has_hips`（通过 COCO 关键点 5/6/11/12 计算）
- 默认使用 `pose` 模式

**task_follow.py `FollowController`：**
- 拆分为 `SegFollowController` 和 `PoseFollowController`，与 `ref/tello_track/modules/tracking_controller.py` 参考实现一致
- `SegFollowController`：用像素面积控制前后距离（area_min=100000, area_max=150000）
- `PoseFollowController`：用躯干高度控制前后距离（height_min=200, height_max=250），无髋部时 fb_speed=0
- `--model` 参数路由到对应的控制器

### 2. IoU 跟踪 + 首次锁定 + 丢失即停

**controller.py `_handle_yolo` 新增 IoU 跟踪逻辑：**
- 维护 `_tracked_target`（上一帧锁定目标的 bbox）
- 首次检测：选离画面中心最近的人，记录其 bbox
- 后续帧：计算所有检测框与上一帧锁定框的 IoU，选最大且 ≥ 0.3 的匹配
- 无一匹配（丢失）：清除 `_tracked_target`，返回空结果，无人机悬停，等待用户指令
- 不自动重锁

### 3. 文件路径 + 时间戳

**controller.py `_handle_vision`：**
- `photo`：保存到 `images/` 目录
- `record_start`：保存到 `videos/` 目录
- 用户指定 `--name` 时用原名 + 目录前缀
- 未指定时默认名带时间戳：`images/photo_YYYYMMDD_HHMMSS.jpg`、`videos/video_YYYYMMDD_HHMMSS.avi`

### 4. 移除平滑

- controller.py：删除 `SlidingWindowTracker` 类及 `_yolo_tracker` 实例
- task_follow.py：删除 `FollowController` 中的 `target_history` 滑动窗口平滑
- 直接返回原始检测坐标，与 `ref/tello_track/modules/vision_processor.py` 参考实现一致

### 5. 文档和代码注释更新

| 文件 | 修改内容 |
|------|---------|
| `SKILL.md` | yolo11 → yolo26，删除"卡尔曼滤波"表述，更新模型参数说明 |
| `README.md` | YOLO11 → YOLO26 |
| `CLAUDE.md` | yolo11n.pt → yolo26n-pose.pt，删除滑动窗口描述 |
| `scripts/yolo.py` | 删除 docstring 中"含卡尔曼滤波" |
| `scripts/controller.py` | 删除 SlidingWindowTracker docstring 中相关注释 |

### 涉及修改的文件

- `scripts/controller.py` — YOLO 模型加载 + IoU 跟踪 + 文件路径 + 删除平滑
- `scripts/tasks/task_follow.py` — 拆分控制器 + `--model` 生效 + 删除平滑
- `scripts/yolo.py` — docstring 更新
- `SKILL.md` / `README.md` / `CLAUDE.md` — 文档更新

## 验证

1. 无无人机环境下运行 `uv run scripts/yolo.py detect` 验证 YOLO 检测 JSON 输出格式
2. `uv run scripts/yolo.py count` 验证人数输出
3. `uv run scripts/vision.py photo --name test.jpg` 验证照片保存到 `images/` 且文件名正确
4. `uv run python -c "from scripts.controller import SlidingWindowTracker"` 预期失败（类已删除）
5. grep 确认项目中无残留 `yolo11`、`卡尔曼滤波` 引用
