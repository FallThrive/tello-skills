# task_follow 提前结束修复设计

**日期**：2026-05-28  
**状态**：已批准

## 背景

用户执行 `uv run scripts/tasks/task_follow.py --duration 60 --model pose` 时，跟踪到目标后立刻结束，未等到 60 秒超时。经日志分析定位到三个问题：

1. LED 矩阵 `send_expansion_command` 抛 `TelloException` 导致跟随 daemon 线程静默崩溃
2. TOF 紧急停止阈值单位错误（代码判断 mm 值，但阈值范围错误）
3. SKILL.md 中 controller 生命周期描述与实际行为不符

## 根因分析

### 问题 1：矩阵命令异常导致线程崩溃

`_task_follow_loop` 每 0.05s（20Hz）发送一次矩阵更新命令（如 `EXT mled s r 260h`），ESP32 返回 `matrix error` 并重试 4 次后，DJITelloPy 抛出 `TelloException`。daemon 线程内无 try-except 保护，异常导致线程静默死亡，`ft.join()` 立即返回，`_start_task_follow` 返回 `"ok"`。

### 问题 2：TOF 阈值单位错误

`EXT tof?` 返回值单位为 mm（实践指导书及 SDK 文档明确说明）。当前代码：

```python
if 100 <= tof_dist < 500:  # 100mm~500mm = 10cm~50cm
```

应修正为 100mm~5000mm（10cm~500cm）。同时 `EXT tof?` 返回值格式为 `'tof 52'` 而非纯数字 `'52'`，`int(resp.strip())` 解析失败被 `except` 吞掉，导致 TOF 紧急停止形同虚设。需修复解析逻辑。

### 问题 3：SKILL.md 文档不准确

- "首次调用任意脚本时自动连接无人机" — 实际需手动启动 controller
- "`land` 后自动断开并退出" — 实际 controller 继续运行
- 缺少手动终止 controller 的命令

## 修复方案

方案 A（最小修补）：仅修改 `_task_follow_loop` 方法 + SKILL.md。

## 详细设计

### controller.py 修改

文件：[scripts/controller.py](scripts/controller.py)，`_task_follow_loop` 方法内三处修改：

#### 1. TOF 阈值修正 + 解析修复（第 988-999 行）

`EXT tof?` 返回值格式为 `'tof 52'`（DJITelloPy 会附加前缀），需按空格分割取最后一段再转整数。日志为 mm 单位。

```python
# 改前
try:
    resp = self.tello.send_read_command("EXT tof?")
    tof_dist = int(resp.strip()) if resp.strip() else -1
except Exception:
    pass

if 100 <= tof_dist < 500:
    logger.warning(f"TOF 紧急停止: 距离={tof_dist}cm")

# 改后
try:
    resp = self.tello.send_read_command("EXT tof?")
    if resp.strip():
        tof_dist = int(resp.strip().split()[-1])
except Exception:
    pass

if 100 <= tof_dist < 5000:
    logger.warning(f"TOF 紧急停止: 距离={tof_dist}mm")
```

#### 2. 矩阵命令加 try-except 保护（第 1050-1057 行）

矩阵显示是辅助功能，异常不应中断跟踪。用 `pass` 静默吞异常，不做 warning 日志以免高频刷屏。

```python
# 改前
with self._flight_lock:
    if model_type == "seg":
        ...
        self.tello.send_expansion_command(f"mled s r {area_k}k")
    else:
        ...
        self.tello.send_expansion_command(f"mled s r {h}h")

# 改后
with self._flight_lock:
    if model_type == "seg":
        ...
        try:
            self.tello.send_expansion_command(f"mled s r {area_k}k")
        except Exception:
            pass
    else:
        ...
        try:
            self.tello.send_expansion_command(f"mled s r {h}h")
        except Exception:
            pass
```

#### 3. 矩阵更新降频到 2Hz

循环外新增 `last_matrix_time = 0`，矩阵命令前加节流判断 `if now - last_matrix_time >= 0.5`。

### SKILL.md 修改

文件：[SKILL.md](SKILL.md)，三处修改：

#### 1. 连接管理描述（第 45-47 行）

```markdown
# 改前
首次调用任意脚本时自动连接无人机，`land` 后自动断开。

# 改后
使用脚本前需先手动启动 controller，controller 启动时自动连接无人机。`land` 后 controller 保持运行。
```

#### 2. controller 启动说明（第 53-59 行）

删除"首次调用自动启动"和"land 后自动断开并退出"的错误描述，补充手动终止命令：

```bash
pkill -f "scripts/controller.py"
```

#### 3. TOF 相关参数

- 默认参数汇总表 TOF 行：`100-500 cm` → `100-5000 mm`
- 安全约束第 2 条：补充单位说明（mm）

## 影响范围

- `scripts/controller.py`：`_task_follow_loop` 方法，三处局部修改
- `SKILL.md`：文档修正，无代码影响
- 不涉及 CLI 接口变更，不涉及 `_client.py` 修改
