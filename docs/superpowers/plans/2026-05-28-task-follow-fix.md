# task_follow 提前结束修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `task_follow` 检测到目标后立刻结束的 bug，并修正 SKILL.md 中 controller 生命周期的文档错误。

**Architecture:** 在 `_task_follow_loop` 方法内做三处局部修改——TOF 阈值修正、矩阵命令异常保护、矩阵更新降频至 2Hz。SKILL.md 修正连接管理和 TOF 参数文档。

**Tech Stack:** Python 3.12, DJITelloPy

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `scripts/controller.py` | 修改 | `_task_follow_loop` 方法三处修复 |
| `SKILL.md` | 修改 | 连接管理、TOF 参数文档修正 |

---

### Task 1: TOF 解析修复 + 阈值修正

**Files:**
- Modify: `scripts/controller.py:988-999`

- [ ] **Step 1: 修复 TOF 解析逻辑并修正阈值**

`EXT tof?` 返回值格式为 `'tof 52'`（DJITelloPy 附加前缀 `'tof '`），当前 `int(resp.strip())` 对 `'tof 52'` 解析失败被 `except` 吞掉，TOF 紧急停止形同虚设。需按空格分割取最后一段再转整数。

同时修正阈值：`EXT tof?` 返回值单位为 mm，`100-500`（10cm~50cm）修正为 `100-5000`（10cm~500cm）。日志信息同步修正为 mm。

```python
# scripts/controller.py 第 988-999 行
# 改前
tof_dist = -1
with self._flight_lock:
    self._update_cmd_time()
    try:
        resp = self.tello.send_read_command("EXT tof?")
        tof_dist = int(resp.strip()) if resp.strip() else -1
    except Exception:
        pass

if 100 <= tof_dist < 500:
    logger.warning(f"TOF 紧急停止: 距离={tof_dist}cm")
    break

# 改后
tof_dist = -1
with self._flight_lock:
    self._update_cmd_time()
    try:
        resp = self.tello.send_read_command("EXT tof?")
        if resp.strip():
            tof_dist = int(resp.strip().split()[-1])
    except Exception:
        pass

if 100 <= tof_dist < 5000:
    logger.warning(f"TOF 紧急停止: 距离={tof_dist}mm")
    break
```

- [ ] **Step 2: 提交**

```bash
git add scripts/controller.py
git commit -m "fix: 修复 TOF 解析（适配 'tof N' 格式）并修正紧急停止阈值为 100-5000mm"
```

---

### Task 2: 矩阵更新降频 + 异常保护

**Files:**
- Modify: `scripts/controller.py:963-964`（新增变量）
- Modify: `scripts/controller.py:1050-1057`（重构矩阵显示逻辑）

- [ ] **Step 1: 在循环变量初始化区新增 `last_matrix_time`**

在 `frame_cx, frame_cy = 480, 360` 后添加：

```python
# scripts/controller.py 第 964 行后新增
last_matrix_time = 0
```

- [ ] **Step 2: 重构矩阵显示块，添加 2Hz 节流 + try-except**

将第 1050-1057 行替换为：

```python
                # ---- LED 矩阵显示（2Hz + 容错） ----
                now = time.time()
                if now - last_matrix_time >= 0.5:
                    with self._flight_lock:
                        if model_type == "seg":
                            area_k = int(target.get('area', 0) // 1000) if target else 0
                            try:
                                self.tello.send_expansion_command(f"mled s r {area_k}k")
                            except Exception:
                                pass
                        else:
                            h = int(target.get('torso_height', 0)) if target else 0
                            try:
                                self.tello.send_expansion_command(f"mled s r {h}h")
                            except Exception:
                                pass
                    last_matrix_time = now
```

- [ ] **Step 3: 提交**

```bash
git add scripts/controller.py
git commit -m "fix: 矩阵更新降频至 2Hz 并添加异常保护，防止 daemon 线程崩溃"
```

---

### Task 3: SKILL.md 文档修正

**Files:**
- Modify: `SKILL.md:46`
- Modify: `SKILL.md:55-59`
- Modify: `SKILL.md:231`
- Modify: `SKILL.md:288`

- [ ] **Step 1: 修正连接管理描述（第 46 行）**

```markdown
# 改前
首次调用任意脚本时自动连接无人机，`land` 后自动断开。

# 改后
使用脚本前需先手动启动 controller，controller 启动时自动连接无人机。`land` 后 controller 保持运行。
```

- [ ] **Step 2: 修正 controller 启动说明（第 55-59 行）**

```markdown
# 改前
> `scripts/controller.py` 是持久 TCP 服务器进程，首次调用任意 CLI 脚本时自动启动。如需手动启动，后台运行即可：
> ```
> uv run scripts/controller.py &
> ```
> 看到 `[controller] TCP 服务器监听 127.0.0.1:9999` 日志后即可继续执行后续脚本，controller 在后台持续运行。`land` 后自动断开并退出。

# 改后
> `scripts/controller.py` 是持久 TCP 服务器进程，需手动后台启动：
> ```
> uv run scripts/controller.py &
> ```
> 看到 `[controller] TCP 服务器监听 127.0.0.1:9999` 日志后即可执行后续脚本，controller 在后台持续运行。`land` 后仅降落无人机，controller 进程继续运行，可继续执行 `record_stop` 等命令。
>
> 全部任务完成后手动终止 controller：
> ```
> pkill -f "scripts/controller.py"
> ```
```

- [ ] **Step 3: 修正安全约束 TOF 说明（第 231 行）**

```markdown
# 改前
2. TOF 测距 < 100 不可信，8192 表示未检测到

# 改后
2. TOF 测距单位 mm，< 100（10cm 以内）不可信，8192 表示未检测到
```

- [ ] **Step 4: 修正默认参数表 TOF 行（第 288 行）**

```markdown
# 改前
| TOF 紧急停止 | `100-500 cm` | 检测距离范围（1-5 米） |

# 改后
| TOF 紧急停止 | `100-5000 mm` | 检测距离范围（0.1-5 米），< 100mm 不可信 |
```

- [ ] **Step 5: 提交**

```bash
git add SKILL.md
git commit -m "docs: 修正 controller 生命周期描述及 TOF 参数单位"
```

---

### Task 4: 验证

- [ ] **Step 1: 确认 controller.py 语法正确**

```bash
uv run python -c "import py_compile; py_compile.compile('scripts/controller.py', doraise=True)"
```

- [ ] **Step 2: 提交验证 commit（如有残留修改）**

```bash
git status
```
