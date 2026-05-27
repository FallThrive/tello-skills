# 预览功能清理与重构：设计文档

## 背景与动机

2026-05-22 的摄像头预览设计规范实现了自动弹窗预览功能：YOLO detect 自动弹出标注窗口、mission_pad detect 自动切换下视摄像头并弹出检测窗口。但实际运行环境中 AI 通过 CLI 执行命令，无法查看 GUI 窗口，自动弹窗是冗余设计。

同时，根据 Tello SDK 3.0 文档，挑战卡探测（`mon` + `mdirection`）与视频流（`streamon`）是两套独立命令，挑战卡检测不依赖视频流。当前 `mission_pad detect` 中切换下视摄像头方向并开启视频流的行为是多余的，且会导致前视录像中断（上一会话已验证此 bug）。

## 改动范围

### 1. 取消 YOLO 自动弹窗 + 新增手动 `preview_yolo_start`

- **`controller.py`**：`_handle_yolo` 删除自动启动 `preview-yolo` daemon 线程的逻辑；`_handle_vision` 新增 `preview_yolo_start` action（`model_type` 参数由当前 `_model_type` 状态决定）
- **`vision.py`**：新增 `preview_yolo_start` 子命令（可选 `--model pose|seg` 参数）
- **`SKILL.md`**：vision 模块补充 `preview_yolo_start` 命令说明

`yolo detect` 行为：加载模型 → 执行推理 → 返回 JSON → 退出。不弹窗。用户需可视化时手动执行 `vision preview_yolo_start`。

### 2. 删除挑战卡检测预览及下视视频流自动切换

- **`controller.py`**：
  - 删除 `_preview_pad_loop` 方法（约 50 行）
  - 删除实例属性：`_preview_pad_stop`、`_preview_pad_thread`、`_pad_shared`
  - `_handle_mission_pad`：删除 `detect` 和 `detect_stop` action 分支
- **`mission_pad.py`**：删除 `detect` 和 `detect_stop` 子命令
- **`tasks/task_search_pad.py`**：删除 `mission_pad detect` 和 `mission_pad detect_stop` 调用；删除 `preview_started` 变量及关联逻辑；`pad_id` 校验保持现有 `1 <= pad_id <= 8`
- **`SKILL.md`**：移除 mission_pad 模块中 `detect`/`detect_stop` 说明

保留手动 `preview_start downward`——下视纯净预览仍可手动开启。

### 3. SKILL.md 补充 controller 进程清理指引

在连接管理部分添加：

> 重新连接无人机后，先清理旧 controller 再启动：
> ```bash
> pgrep -f "scripts/controller.py" && pkill -f "scripts/controller.py" && sleep 1
> uv run scripts/controller.py &
> ```
> `pgrep` 检查残留进程，有则 `pkill` 终止并等待退出，最后启动新 controller。

### 变更文件清单

| 文件 | 改动描述 |
|------|----------|
| `scripts/controller.py` | 删除 `_preview_pad_loop` + pad 相关状态；`_handle_mission_pad` 删除 detect/detect_stop；`_handle_yolo` 删除自动弹窗；`_handle_vision` 新增 preview_yolo_start |
| `scripts/vision.py` | 新增 `preview_yolo_start` 子命令 |
| `scripts/mission_pad.py` | 删除 `detect`/`detect_stop` 子命令 |
| `scripts/tasks/task_search_pad.py` | 删除 detect/detect_stop 调用及 preview_started 变量 |
| `SKILL.md` | vision 补充 preview_yolo_start；mission_pad 删除 detect/detect_stop；连接管理补充 controller 清理指引 |

## 验证方案

1. **YOLO 手动预览**（需真实无人机）：
   ```bash
   uv run scripts/yolo.py detect          # 确认无窗口弹出，仅返回 JSON
   uv run scripts/vision.py preview_yolo_start  # 确认手动打开 YOLO 标注窗口
   uv run scripts/vision.py preview_yolo_stop    # 确认关闭
   ```

2. **挑战卡检测无视频流**（需真实无人机）：
   ```bash
   uv run scripts/vision.py record_start --name test.avi  # 前视录像
   uv run scripts/tasks/task_search_pad.py --direction f --step 30  # 执行搜索
   uv run scripts/vision.py record_stop  # 确认录像文件正常保存（未被中断）
   ```

3. **下视手动预览保留**：
   ```bash
   uv run scripts/vision.py preview_start downward  # 确认仍可手动开启
   uv run scripts/vision.py preview_stop downward
   ```

4. **Controller 清理**：
   ```bash
   pgrep -f "scripts/controller.py" && pkill -f "scripts/controller.py" && sleep 1
   uv run scripts/controller.py &
   # 确认 controller 正常启动且无残留进程
   ```
