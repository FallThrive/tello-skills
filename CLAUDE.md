# 记忆文件

This file provides guidance to AI agents (Claude Code, OpenClaw, OpenCode, et al.) when working with code in this repository.

## 项目概述

这是一个 Tello TT 无人机的 AI Agent 技能（skill），通过 CLI 脚本将自然语言指令转换为无人机控制命令。SKILL.md 是运行时加载的技能定义（供 AI Agent 使用），本文件描述代码库本身的开发维护。

## 核心架构

```
CLI 脚本 (scripts/*.py)       ← AI/用户直接调用
      ↓ TCP (127.0.0.1:9999)
Controller 进程 (scripts/controller.py)  ← 持久运行，单线程+Lock 串行执行
      ↓ UDP (DJITelloPy)
Tello 无人机
```

- **[scripts/_client.py](scripts/_client.py)** — 所有 CLI 脚本共用的薄封装层，通过 TCP 向 controller 发送文本命令
- **[scripts/controller.py](scripts/controller.py)** — 持久 TCP 服务器进程，是 DJITelloPy 的唯一桥梁。首次调用脚本时自动启动，内置 10 秒间隔心跳守护线程，`land` 后自动断开
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

# 启动 controller（通常由脚本自动启动，无需手动操作）
uv run scripts/controller.py

# 运行任意 CLI 脚本
uv run scripts/flight.py takeoff
uv run scripts/sensor.py battery
uv run scripts/tasks/task_follow.py --duration 60

# 运行 eval 测试技能行为
# （eval 文件在 evals/evals.json，通过 skill-creator 技能执行）
```

## 添加新模块

1. 在 [scripts/controller.py](scripts/controller.py) 的 `_dispatch()` 中添加新模块路由
2. 在 `TelloController` 中实现 `_handle_<module>()` 方法
3. 创建 `scripts/<module>.py` CLI 脚本，通过 `_client.send_command()` 发送命令
4. 在 [SKILL.md](SKILL.md) 中添加模块速查说明

所有 controller 方法在 `self._lock` 保护下执行，无需额外加锁。

## 关键设计要点

- **controller 是单点串行通道**：所有无人机命令通过它串行执行，一个命令完成后才处理下一个，避免 UDP 命令冲突
- **心跳机制**：连接后守护线程每 10 秒发送 `rc_control(0,0,0,0)`，无需 AI 手动管理
- **录像通过 daemon 线程执行**：`_record_loop` 在独立线程中写入帧，主线程响应命令
- **YOLO 模型懒加载**：首次调用 `yolo detect/count` 时才加载模型，seg 模式加载 `models/yolo26n-seg.pt`，pose 模式加载 `models/yolo26n-pose.pt`

- **任务脚本自己实现控制循环**：`task_follow.py` 和 `task_search_pad.py` 内部闭环，通过 `_client.send_command()` 调用 controller，无需 controller 支持长任务

## Git 提交规范

- 提交信息以单行总结形式，格式：`feat: 简短描述`（也支持 fix、docs、refactor、test、chore、style 等类型）

## 安全约束

- 任何飞行操作前检查电量 ≥ 20%
- TOF 测距值 < 100 不可信，8192 表示未检测到
- 任务脚本支持 Ctrl+C 触发紧急降落
- 所有操作总时长不超过 5 分钟（电池限制）
