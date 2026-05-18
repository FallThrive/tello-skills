# Tello 无人机控制技能

Claude Code 技能，通过自然语言控制 Tello TT 无人机，支持飞行、拍摄、LED、挑战卡、YOLO 检测、视觉跟踪等功能。

## 快速开始

```bash
# 安装依赖
uv sync

# 起飞
uv run scripts/flight.py takeoff

# 拍照
uv run scripts/vision.py photo --name test.jpg

# 降落
uv run scripts/flight.py land
```

首次调用任意脚本时 controller 自动启动，降落时自动断开。所有命令格式为 `uv run scripts/<模块>.py <子命令> [--参数]`，详见 [SKILL.md](SKILL.md)。

## 依赖

- Python >= 3.10
- [DJITelloPy](https://github.com/damiafuentes/DJITelloPy) — Tello 无人机 SDK
- [Ultralytics YOLO11](https://github.com/ultralytics/ultralytics) — 实时人员检测
- PyTorch + torchvision

## 项目结构

```
scripts/
  controller.py       # 持久 TCP 服务器，通过 DJITelloPy 与无人机通信
  _client.py          # CLI 脚本共用的 TCP 客户端封装
  flight.py           # 飞行控制（起飞、降落、移动、旋转、速度控制）
  led.py              # LED 彩灯（常亮、呼吸、闪烁）
  matrix.py           # LED 点阵屏（滚动、静态显示）
  sensor.py           # 传感器（电量、TOF、姿态、加速度、高度等）
  vision.py           # 视觉（视频流、拍照、录像）
  yolo.py             # YOLO 人员检测
  mission_pad.py      # 挑战卡识别
  tasks/
    task_search_pad.py  # 方向搜索挑战卡（闭环脚本）
    task_follow.py      # 实时人员跟随（闭环脚本）
SKILL.md              # 技能定义（Claude Code 运行时加载）
evals/
  evals.json          # 技能评估用例
```

## 架构

```
CLI 脚本 → TCP (127.0.0.1:9999) → Controller 进程 → DJITelloPy (UDP) → Tello 无人机
```

Controller 是单点串行通道，单线程 + Lock 确保命令不冲突，内置 10 秒心跳守护线程。

## 开发

```bash
uv sync          # 安装依赖
uv add <pkg>     # 添加新依赖
```

添加新功能模块：在 `scripts/controller.py` 中注册路由并实现 handler → 创建 CLI 脚本 → 更新 [SKILL.md](SKILL.md)。

项目 PIN 到 PyTorch CUDA 12.8 版本，国内环境使用清华 PyPI 镜像。

## 使用其他环境管理工具

本项目默认使用 uv，SKILL.md 中的命令使用 `python` 前缀（环境无关的通用格式）。如果你更熟悉 conda 或 venv，按以下步骤配置后可直接使用 `python` 代替 `uv run`。

### conda

```bash
conda create -n tello python=3.12
conda activate tello
pip install djitellopy ultralytics
# PyTorch 安装请参考 https://pytorch.org/get-started/locally/ 选择对应 CUDA 版本
# 激活环境后直接使用 python 代替 uv run：
python scripts/flight.py takeoff
```

### venv

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python scripts/flight.py takeoff
```

> **注意**：切换环境后，CLAUDE.md / AGENTS.md 中的环境适配规则也需相应调整（将 `uv run` 替换为你实际使用的执行方式），以确保 AI Agent 生成正确的命令。
