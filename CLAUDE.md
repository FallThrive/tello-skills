# 记忆文件

This file provides guidance to AI agents (Claude Code, OpenClaw, OpenCode, et al.) when working with code in this repository.

## 项目概述

这是一个 Tello TT 无人机的 AI Agent 技能（skill），通过 CLI 脚本将自然语言指令转换为无人机控制命令。SKILL.md 是运行时加载的技能定义（供 AI Agent 使用），本文件描述代码库本身的开发维护。

## 核心架构

```
CLI 脚本 (scripts/*.py)       ← AI/用户直接调用
      ↓ TCP (127.0.0.1:9999)
Controller 进程 (scripts/controller.py)  ← 持久运行，多线程+Lock 串行化
      ↓ UDP (DJITelloPy)
Tello 无人机
```

- **[scripts/_client.py](scripts/_client.py)** — 所有 CLI 脚本共用的薄封装层，通过 TCP 向 controller 发送文本命令
- **[scripts/controller.py](scripts/controller.py)** — 持久 TCP 服务器进程，是 DJITelloPy 的唯一桥梁。需手动后台启动 `uv run scripts/controller.py &`，内置 5 秒间隔心跳守护线程，`land` 后仅降落无人机，controller 继续运行
- **`scripts/tasks/`** — 实时闭环脚本（`task_follow.py`, `task_search_pad.py`），持续运行至超时或任务完成，内部自己实现控制循环

## 环境与执行

本项目使用 uv 管理依赖和虚拟环境。所有脚本执行命令统一使用 `uv run` 前缀：

```
uv run scripts/<模块>.py <子命令> [--参数]
```

不要使用裸 `python` 命令，因为 `uv run` 会自动确保正确的虚拟环境。SKILL.md 中的命令使用 `python` 前缀（环境无关的通用格式），在本仓库中执行时必须替换为 `uv run`。

## 常用命令

```bash
# 安装依赖
uv sync

# 添加新依赖
uv add <package-name>

# 启动 controller（需手动后台启动）
uv run scripts/controller.py &

# 运行任意 CLI 脚本
uv run scripts/flight.py takeoff
uv run scripts/sensor.py battery

# 运行 task 脚本
uv run scripts/tasks/task_follow.py --duration 60
```

## 添加新模块

1. 在 [scripts/controller.py](scripts/controller.py) 的 `_dispatch()` 中添加新模块路由
2. 在 `TelloController` 中实现 `_handle_<module>()` 方法
3. 创建 `scripts/<module>.py` CLI 脚本，通过 `_client.send_command()` 发送命令
4. 在 [SKILL.md](SKILL.md) 中添加模块速查说明

所有 controller 方法在三层锁保护下执行（`_flight_lock`、`_model_lock`、`_state_lock`），详见 README.md 架构详解。

## 关键设计要点

- **controller 多线程+锁串行化**：ThreadPoolExecutor(4 workers) 处理并发连接，通过 `_flight_lock` 串行化所有 UDP 通信，避免 DJITelloPy 单 socket 协议冲突
- **心跳机制**：守护线程每 5 秒检查空闲时间，超过 5 秒发送 `rc_control(0,0,0,0)` 防止 Tello 15 秒超时自动降落。仅飞行命令重置心跳计时器，sensor/led/matrix/photo/record 不重置
- **录像通过 daemon 线程执行**：`_record_loop` 在独立线程中写入帧，`land` 时自动停止录像并清理线程
- **YOLO 模型懒加载**：首次调用 `yolo detect/count` 时才加载模型，seg 模式加载 `models/yolo26n-seg.pt`，pose 模式加载 `models/yolo26n-pose.pt`
- **BoT-SORT 追踪器**：使用 ReID 外观特征支持遮挡后重识别，优于 ByteTrack（遮挡后 track_id 会变）
- **task 两种闭环模式**：`task_follow.py` 委托 controller 服务端闭环（20Hz，无 TCP 延迟）；`task_search_pad.py` 客户端闭环（每次操作一次 TCP 往返，适合低频步进）

## 开发注意事项

- **RGB/BGR 帧格式**：DJITelloPy `BackgroundFrameRead.frame` 输出 RGB，OpenCV 需 BGR，拍照和录像必须 `cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)`
- **ESP32 点阵屏限制**：`mled s` 只支持单字符，多字符需用 `mled l` 滚动显示；高频发送会触发 `matrix error` → `TelloException`，task follow 中已降频到 2Hz 并加 try-except 保护
- **TOF 返回值格式**：DJITelloPy 返回 `'tof 52'` 而非纯数字，需 `resp.strip().split()[-1]` 解析；单位 mm；< 100 不可信，8192 表示未检测到
- **挑战卡检测独立于视频流**：SDK 3.0 的 `mon`/`mdirection` 与 `streamon` 是独立命令，切换下视摄像头会中断前视录像
- **daemon 线程需 try-except 保护**：ESP32 命令不稳定，未捕获异常会导致 daemon 线程静默崩溃

## Git 提交规范

- 提交信息以单行总结形式，格式：`feat: 简短描述`（也支持 fix、docs、refactor、test、chore、style 等类型）

## 安全约束

- 任何飞行操作前检查电量 ≥ 20%
- TOF 测距值 < 100 不可信，8192 表示未检测到
- 任务脚本支持 Ctrl+C 触发紧急降落
- 所有操作总时长不超过 5 分钟（电池限制）
