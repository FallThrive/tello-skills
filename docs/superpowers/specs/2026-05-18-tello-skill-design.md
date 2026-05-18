# 视觉跟踪跟随无人机 — Tello Skill 设计规范

## 概述

为 Tello TT 无人机课程设计创建一个 Claude Code Skill，实现自然语言控制无人机完成视觉跟踪跟随任务。用户通过自然语言下达多阶段指令，AI 拆解为无人机原子操作并执行。

### 应用场景

**人员视觉跟踪跟随**：无人机搜索指定衣着的人员，多模态模型辅助精确定位，YOLO + 比例控制实现实时跟随，挑战卡定位巡逻点，激光测距保障安全距离。

### 目标加分项（合计 +11）

| 加分项 | 分值 | 实现方式 |
|--------|------|----------|
| 激光测距 | +2 | 跟随安全距离、搜索挑战卡高度控制 |
| 挑战卡识别 | +1 | 任务点定位与导航 |
| LED 灯 | +1 | 状态指示（搜索/锁定/跟随/警告） |
| LED 点阵屏 | +1 | 显示挑战卡 ID、人数、目标距离 |
| 避障功能 | +2 | 多模态模型视觉判断 + 飞行绕行 |
| 基于视觉的飞行控制 | +2 | 多模态视觉定位 + YOLO 比例控制跟随 |
| 目标检测 | +2 | YOLO 人员检测 + 衣物颜色过滤 |

## 架构

### 交互链路

```
用户（手机 OpenClaw / 本地 Claude Code）
       │ 自然语言指令
       ▼
AI 模型（多模态 / 纯文本）
       │ 拆解任务 + 调用 shared 函数 + 调度 task 脚本
       ▼
┌──────────────────────────────┐
│        Tello Skill           │
│  ┌──────────┐ ┌───────────┐  │
│  │ CLI 脚本 │ │  tasks/   │  │
│  │ 薄封装层 │ │ 实时闭环 │  │
│  └──────────┘ └───────────┘  │
└──────────────────────────────┘
       │ DJITelloPy
       ▼
  Tello TT 无人机
```

### 核心划分原则

| 场景 | 控制方 | 封装方式 |
|------|--------|----------|
| 单次命令（起飞、移动、LED、拍照等） | DJITelloPy | CLI 脚本（参数格式固定，不易出错） |
| 避障判断、人物居中定位 | AI（多模态模型）拍照→看图→给动作→循环 | 不封装脚本，AI 直接调 CLI 脚本 |
| 方向搜索挑战卡（0.5s 级硬件交互循环） | 本地闭环 | `tasks/task_search_pad.py` |
| 实时人物跟随（50ms 级视觉闭环） | 本地闭环 | `tasks/task_follow.py` |

### 两种控制模式

**模式 A — AI + 多模态视觉定位**（避障绕行、人物居中）

```
拍照 → 发送给多模态模型 → 模型看图判断位置 →
返回动作指令（旋转/平移/通过）→ 无人机执行 → 循环
延迟：~1-2s/轮 | 精度：高 | 适用：非实时场景
```

**模式 B — YOLO + 比例控制跟随**（实时跟踪）

```
YOLO 检测人员边界框 → 计算中心偏移 →
比例控制器输出 rc_control → 激光测距限距 → 循环
延迟：~50-100ms/帧 | 精度：中 | 适用：实时跟踪
```

## 项目结构

```
tello_skills/
├── SKILL.md                         # Skill 提示词
├── scripts/
│   ├── controller.py                # 持久后台进程（连接、心跳守护线程、录像线程）
│   ├── flight.py                    # takeoff | land | move | rotate | rc
│   ├── led.py                       # solid | breathe | blink | off
│   ├── matrix.py                    # scroll | static | off
│   ├── sensor.py                    # battery | tof | attitude | acceleration | height | flight_time | barometer
│   ├── vision.py                    # stream_on | stream_off | photo | record_start | record_stop
│   ├── yolo.py                      # detect | count（卡尔曼滤波平滑）
│   ├── mission_pad.py               # enable | disable | id | xyz | fly
│   └── tasks/
│       ├── task_search_pad.py       # 方向搜索挑战卡
│       └── task_follow.py           # YOLO + 比例控制实时跟随
│
└── ref/
    └── tello_track/                 # 现有项目（跟随逻辑参考）
```

### CLI 脚本接口

所有脚本统一格式：`uv run scripts/<模块>.py <子命令> [--参数]`

脚本内部通过 controller 进程与 DJITelloPy 通信，controller 首次调用时自动启动，`land` 后自动停止。

- **flight.py**：`takeoff`, `land`, `move --direction <f/b/l/r/u/d> --dist <cm>`, `rotate --dir <cw/ccw> --deg <角度>`, `rc --lr ... --fb ... --ud ... --yaw ...`
- **led.py**：`solid --r 255 --g 0 --b 0`, `breathe --freq 0.5 --r 0 --g 255 --b 0`, `blink --freq 1 --r1 255 --g1 0 --b1 0 --r2 0 --g2 0 --b2 255`, `off`
- **matrix.py**：`scroll --direction <l/r/u/d> --color <r/b/p> --freq 1 --text "hello"`, `static --color <r/b/p> --text "OK"`, `off`
- **sensor.py**：`battery`, `tof`, `attitude` → (pitch, roll, yaw), `acceleration`, `height`, `flight_time`, `barometer`
- **vision.py**：`stream_on`, `stream_off`, `photo --name <文件名>`, `record_start --name <文件名>`, `record_stop`
- **yolo.py**：`detect` → 边界框列表, `count` → 人数（卡尔曼滤波平滑）
- **mission_pad.py**：`enable`, `disable`, `id` → 挑战卡 ID（-1 未识别）, `xyz` → (x,y,z) 相对坐标, `fly --id <pad_id>`

### tasks/ 任务脚本

**task_search_pad.py**：
```
--direction f/b/l/r  搜索方向
--step 30            每步距离(cm)
--max-attempts 10    最大尝试次数

逻辑：小步飞行 → 等待 0.5s 稳定 → 检测挑战卡 →
      有则 fly_to_pad_above + 蓝灯 + 屏显 ID → 退出
      无则继续飞行 → 超过 max_attempts 退出
```

**task_follow.py**：
```
--duration 120       跟随时长(秒)
--model seg          跟踪模型: seg(分割面积)/pose(躯干高度)

逻辑：YOLO 检测人员 → 卡尔曼滤波平滑 →
      计算中心偏移(yaw+ud) + 像素面积/躯干高度(fb) →
      比例控制器 → rc_control → LED 红灯 + 屏显距离 → 循环
      TOF 仅作紧急安全下限(50cm)，不参与正常距离控制
      控制逻辑参考 ref/tello_track/modules/tracking_controller.py
```

## SKILL.md 核心内容

### 示例：AI 拆解完整用户指令

```
用户输入：
  开始录像并起飞，向前小步飞找到任务点1，向左绕过柱子，
  继续向前到任务点2，检测前方人数，找到穿白衣服的人并使其居中，
  开启跟随模式，降落并停止录制

AI 拆解执行：

1.  [shared 调用]
    vision.record_start("mission.avi")
    flight.takeoff()

2.  [脚本闭环]
    uv run scripts/tasks/task_search_pad.py --direction f --step 30

3.  [AI + 多模态避障循环]
    a. vision.take_photo("check.png")
    b. 多模态模型看图 → "柱子还在画面右侧，向左移动约1m"
    c. flight.move_left(100)
    d. vision.take_photo("check.png")
    e. 多模态模型 → "柱子已不在画面中，可以继续前进"

4.  [脚本闭环]
    uv run scripts/tasks/task_search_pad.py --direction f --step 30

5.  [shared 调用]
    frame = vision.capture_frame()
    n = yolo_detector.count_persons(frame)
    matrix.static('blue', str(n))
    led.solid(0, 255, 0)

6.  [AI + 多模态居中循环]
    a. vision.take_photo("target.png")
    b. 多模态模型 → "穿白衣的人偏离中心右侧，右转20度"
    c. flight.rotate_cw(20)
    d. vision.take_photo("target.png")
    e. 多模态模型 → "目标人物已在画面中心附近，满足要求"

7.  [脚本闭环]
    uv run scripts/tasks/task_follow.py --duration 120 --distance 150

8.  [shared 调用]
    flight.land()
    vision.record_stop()
```

## 安全约束

1. 每次操作前检查电池电量 ≥ 20%
2. 任务脚本需支持 Ctrl+C 紧急降落
3. 激光测距阈值：跟随模式最小距离 50cm
4. 所有脚本超时上限 5 分钟（电池续航）

## 部署

- **开发调试**：本地 Claude Code 加载 SKILL.md
- **最终部署**：电脑运行 OpenClaw Gateway，手机远程交互
- 两种环境共用同一套 `scripts/` 代码和 `SKILL.md` 提示词
