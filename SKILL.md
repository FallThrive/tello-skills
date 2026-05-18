---
name: tello
description: 控制 Tello TT 无人机，支持飞行、拍摄、LED、挑战卡、YOLO 检测、视觉跟踪等功能。Make sure to use this skill whenever the user mentions drone, UAV, Tello, 无人机, 航拍, 跟踪, 避障, 飞行控制, or needs to control a real drone — even if they don't explicitly say "Tello".
---

# Tello 无人机控制技能

## 环境与执行

所有脚本调用格式：

```
python scripts/<模块>.py <子命令> [--参数]
```

本项目默认使用 uv 管理依赖和虚拟环境。实际执行时需将 `python` 替换为 `uv run`，以确保自动使用正确的虚拟环境：

```
uv run scripts/<模块>.py <子命令> [--参数]
```

如果你使用 conda、venv 等其他环境管理工具，激活对应环境后可直接使用 `python` 前缀。

## 核心架构

所有无人机操作通过 CLI 脚本执行，脚本内部通过 controller 进程与 DJITelloPy 通信。controller 首次连接时自动启动，持久运行，内置心跳守护线程。

- **`scripts/flight.py` / `led.py` / `vision.py` 等** — 单次命令脚本，执行完即退出
- **`scripts/tasks/`** — 实时闭环脚本，持续运行至超时或任务完成

## 脚本调用方式

所有脚本使用 Bash 工具执行，工作目录为项目根目录：

```
python scripts/<模块>.py <子命令> [--参数]
```

具体命令格式参见下方模块速查。

## 模块速查

### 连接管理
首次调用任意脚本时自动连接无人机，`land` 后自动断开。

```
python scripts/flight.py takeoff
python scripts/flight.py land
```

连接后自动启动守护线程每 10 秒发送心跳，AI 无需手动管理。

### flight.py — 飞行控制

```
python scripts/flight.py takeoff
python scripts/flight.py land
python scripts/flight.py move --direction <f/b/l/r/u/d> --dist <cm>
python scripts/flight.py rotate --direction <cw/ccw> --deg <角度>
python scripts/flight.py rc --lr <左+右-> --fb <前+后-> --ud <上+下-> --yaw <顺时针+逆时针->
```

### led.py — LED 彩灯

```
python scripts/led.py solid --r 255 --g 0 --b 0
python scripts/led.py breathe --freq 0.5 --r 0 --g 255 --b 0
python scripts/led.py blink --freq 1 --r1 255 --g1 0 --b1 0 --r2 0 --g2 0 --b2 255
python scripts/led.py off
```

### matrix.py — LED 点阵屏

```
python scripts/matrix.py scroll --direction <l/r/u/d> --color <r/b/p> --freq 1 --text "hello"
python scripts/matrix.py static --color <r/b/p> --text "OK"
python scripts/matrix.py off
```

### sensor.py — 传感器

```
python scripts/sensor.py battery       # 电量百分比
python scripts/sensor.py tof            # 激光测距（mm），<100 不可信，8192 表示未检测到
python scripts/sensor.py attitude       # (pitch, roll, yaw) 姿态角（度）
python scripts/sensor.py acceleration   # (ax, ay, az) 加速度（cm/s²）
python scripts/sensor.py height         # 相对起飞高度（cm）
python scripts/sensor.py flight_time    # 累计飞行时长（秒）
python scripts/sensor.py barometer      # 气压计高度（m）
```

### vision.py — 视觉

```
python scripts/vision.py stream_on
python scripts/vision.py stream_off
python scripts/vision.py photo --name <文件名>
python scripts/vision.py record_start --name <文件名>
python scripts/vision.py record_stop
```

### yolo.py — YOLO 检测 + 滑动窗口平滑

```
python scripts/yolo.py detect          # 检测人员，输出滑动窗口平滑后的边界框 JSON
python scripts/yolo.py count           # 检测人员，输出人数
```

内部使用滑动窗口（最近 5 帧取均值）平滑边界框中心，减少抖动。

### mission_pad.py — 挑战卡

```
python scripts/mission_pad.py enable
python scripts/mission_pad.py disable
python scripts/mission_pad.py id                      # 挑战卡 ID（-1 未识别）
python scripts/mission_pad.py xyz                     # (x, y, z) 相对坐标（cm）
python scripts/mission_pad.py fly --id <pad_id>       # 飞至挑战卡正上方
```

## tasks/ 脚本

### task_search_pad.py — 方向搜索挑战卡

面向指定方向小步飞行，每步后检测挑战卡。适用于"向前飞找到任务点"这类指令。

```
python scripts/tasks/task_search_pad.py --direction <f/b/l/r> [--step 30] [--max-attempts 10]
```

参数：
- `--direction`：搜索方向，f=前、b=后、l=左、r=右（必填）
- `--step`：每步距离 cm（默认 30）
- `--max-attempts`：最大尝试次数（默认 10）

行为：小步飞行 → 等待 0.5s → 检测挑战卡 → 发现则飞到正上方、蓝灯亮、屏显 ID → 未发现则继续，超 max_attempts 退出。

### task_follow.py — 实时人员跟随

YOLO 检测人员，滑动窗口平滑中心点，比例控制器驱动 rc_control 实时跟随。前后距离通过人物像素大小（分割面积/躯干高度）控制。

```
python scripts/tasks/task_follow.py [--duration 120] [--model seg]
```

参数：
- `--duration`：跟随时长秒（默认 120）
- `--model`：跟踪模型，`seg`（分割面积控制距离）或 `pose`（躯干高度控制距离），默认 `seg`

行为：YOLO 检测 → 滑动窗口平滑边界框中心 → 计算中心偏移(yaw+ud) + 像素面积/躯干高度(fb) → 比例控制器 → rc_control → LED 红灯 + 屏显目标距离 → 循环。
TOF 仅作为紧急安全下限（最小 50cm），不参与正常距离控制。

## 两种控制模式

### 模式 A：AI + 多模态视觉定位

用于避障绕行、人物居中定位等需要视觉判断的场景。AI 自己控制循环：

1. `python scripts/vision.py photo --name check.png` 拍照（守护线程自动维持连接）
2. 用 Read 工具读取图片，发送给多模态模型
3. 多模态模型看图判断位置并给出动作建议
4. `python scripts/flight.py rotate/move` 执行动作
5. 重复，直到多模态模型判断目标达成

### 模式 B：YOLO + 比例控制跟随

用于实时跟踪，无需 AI 参与循环：

```
python scripts/tasks/task_follow.py --duration 120 --model seg
```

## 安全约束

1. 任何飞行操作前检查电量 ≥ 20%：`python scripts/sensor.py battery`
2. TOF 测距 < 100 不可信，8192 表示未检测到
3. 防断连心跳由 controller 守护线程自动处理，AI 无需关心
4. 所有操作总时长不超过 5 分钟（电池续航）
5. task 脚本支持 Ctrl+C 触发紧急降落

## 示例：完整任务拆解

用户输入：
> 开始录像并起飞，向前小步飞找到任务点1，向左绕过柱子，继续向前到任务点2，
> 检测前方人数，找到穿白衣服的人并使其居中，开启跟随模式，降落并停止录制

AI 按以下顺序执行：

```
# 1. 录像 + 起飞
python scripts/vision.py record_start --name mission.avi
python scripts/flight.py takeoff

# 2. 方向搜索挑战卡（脚本闭环）
python scripts/tasks/task_search_pad.py --direction f --step 30

# 3. AI + 多模态避障循环
# a. python scripts/vision.py photo --name check.png
# b. Read check.png → 多模态模型判断柱子位置 → 返回动作
# c. python scripts/flight.py move --direction l --dist 100
# d. 重复 a-c 直到确认通过

# 4. 继续搜索下个任务点
python scripts/tasks/task_search_pad.py --direction f --step 30

# 5. 检测人数 + LED 反馈
python scripts/yolo.py count
# 读输出获取人数 n，然后：
python scripts/matrix.py static --color b --text "<n>"
python scripts/led.py solid --r 0 --g 255 --b 0

# 6. AI + 多模态人物居中循环
# a. python scripts/vision.py photo --name target.png
# b. Read target.png → 多模态模型判断人物位置 → 返回旋转/平移指令
# c. python scripts/flight.py rotate --direction cw --deg 20
# d. 重复 a-c 直到模型判断人物已居中

# 7. 开启实时跟随（脚本闭环）
python scripts/tasks/task_follow.py --duration 120 --model seg

# 8. 降落 + 停止录像
python scripts/flight.py land
python scripts/vision.py record_stop
```

## 执行注意事项

- controller 进程首次调用脚本时自动启动，AI 无需手动管理
- task 脚本运行时 AI 无法干预，需等待脚本正常结束或超时
- 多模态视觉循环中 AI 只需拍照→读取→判断→执行动作循环，controller 守护线程自动维护连接
- 如果用户只说了大方向（如"绕过柱子"），AI 应主动用视觉循环判断，而不是假设固定距离
- 用户指令中若未明确参数，使用合理默认值并在执行前确认
- 起飞后、每次移动后可调用 `python scripts/sensor.py attitude` 检查 pitch/roll 是否接近 0，姿态稳定后再拍照
- 旋转对位后检查 yaw 值验证角度是否达标
