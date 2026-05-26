# 心跳保活修复 + SKILL.md 文档完善 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 controller 心跳计时器被非飞行控制命令重置的 bug，并在 SKILL.md 中补充 CLI 输出说明、禁止直接调用 _client.py、controller 后台运行指引。

**Architecture:** 两个文件改动。controller.py 在 `_dispatch()` 中移除 led/matrix/sensor/mission_pad(非fly) 分支的 `_update_cmd_time()` 调用。SKILL.md 在每个模块代码块后添加输出说明行，在核心架构节补充 _client.py 禁令，在连接管理节补充 controller 后台说明。

**Tech Stack:** Python 3.x, Markdown

---

### Task 1: controller.py — 修复心跳计时器

**文件：**
- Modify: `scripts/controller.py:144-162`

- [ ] **Step 1: 移除 led 分支的 `_update_cmd_time()`**

定位到 `scripts/controller.py` 第 144-147 行：

```python
        elif module == "led":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_led(action, remaining)
```

将 `_update_cmd_time()` 行删除，变为：

```python
        elif module == "led":
            with self._flight_lock:
                return self._handle_led(action, remaining)
```

- [ ] **Step 2: 移除 matrix 分支的 `_update_cmd_time()`**

定位到第 148-151 行：

```python
        elif module == "matrix":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_matrix(action, remaining)
```

变为：

```python
        elif module == "matrix":
            with self._flight_lock:
                return self._handle_matrix(action, remaining)
```

- [ ] **Step 3: 移除 sensor 分支的 `_update_cmd_time()`**

定位到第 152-155 行：

```python
        elif module == "sensor":
            with self._flight_lock:
                self._update_cmd_time()
                return self._handle_sensor(action)
```

变为：

```python
        elif module == "sensor":
            with self._flight_lock:
                return self._handle_sensor(action)
```

- [ ] **Step 4: 重构 mission_pad 分支，仅 fly 保留 `_update_cmd_time()`**

定位到第 156-162 行：

```python
        elif module == "mission_pad":
            if action in ("detect", "detect_stop"):
                return self._handle_mission_pad(action, remaining)
            else:
                with self._flight_lock:
                    self._update_cmd_time()
                    return self._handle_mission_pad(action, remaining)
```

替换为：

```python
        elif module == "mission_pad":
            if action == "fly":
                with self._flight_lock:
                    self._update_cmd_time()
                    return self._handle_mission_pad(action, remaining)
            elif action in ("detect", "detect_stop"):
                return self._handle_mission_pad(action, remaining)
            else:
                with self._flight_lock:
                    return self._handle_mission_pad(action, remaining)
```

- [ ] **Step 5: 验证改动正确性**

用 grep 确认 `_update_cmd_time()` 现在只在预期位置出现：

```bash
grep -n "_update_cmd_time" scripts/controller.py
```

预期输出应只有以下位置：
- `_dispatch()` 中 `flight` 分支（第 142 行）
- `_dispatch()` 中 `mission_pad` → `fly` 分支
- `_dispatch()` 中 `vision` → stream/preview 分支（第 167 行）
- `_task_follow_loop()` 中两处（第 1024 行和第 1081 行附近）
- 方法定义处（第 94 行）

- [ ] **Step 6: 提交**

```bash
git add scripts/controller.py
git commit -m "fix: 心跳计时器不再被非飞行控制命令重置

led/matrix/sensor 模块的 _update_cmd_time() 调用已移除，
mission_pad 仅 fly 保留。这些命令通过 ESP32 串口或 UDP 读取
通道，不会重置 Tello 固件的 15 秒自动降落计时器。"
```

---

### Task 2: SKILL.md — 补充 CLI 命令预期输出

**文件：**
- Modify: `SKILL.md`

- [ ] **Step 1: 在 flight.py 代码块后添加输出说明**

定位到第 61 行（`` ``` `` 结束后），添加：

```markdown
> 输出：成功返回 `ok`，失败返回 `error: ...`。`rc` 无返回值（Tello SDK 特性）。
```

- [ ] **Step 2: 在 led.py 代码块后添加输出说明**

定位到第 70 行（`` ``` `` 结束后），添加：

```markdown
> 输出：成功返回 `ok`，失败返回 `error: ...`
```

- [ ] **Step 3: 在 matrix.py 代码块后添加输出说明**

定位到第 78 行（`` ``` `` 结束后），添加：

```markdown
> 输出：成功返回 `ok`，失败返回 `error: ...`
```

- [ ] **Step 4: 在 sensor.py 代码块后添加输出说明**

定位到第 90 行（`` ``` `` 结束后），添加：

```markdown
> 输出：
> - `battery` → 百分比数值（如 `85`）
> - `tof` → 毫米数值（如 `1200`），8192 表示未检测到
> - `attitude` → `"pitch roll yaw"`（空格分隔，度）
> - `acceleration` → `"ax ay az"`（空格分隔，cm/s²）
> - `height` → 相对起飞高度 cm
> - `flight_time` → 累计飞行秒数
> - `barometer` → 气压计高度 m
```

- [ ] **Step 5: 在 vision.py 代码块后添加输出说明**

定位到第 100 行（`` ``` `` 结束后），添加：

```markdown
> 输出：成功返回 `ok`。`photo` 无流时返回 `error: stream not started`，`record_start` 已在录像返回 `error: already recording`。照片保存至 `images/`，视频保存至 `videos/`。
```

- [ ] **Step 6: 在 yolo.py 代码块后添加输出说明**

定位到第 108 行（`` ``` `` 结束后），添加：

```markdown
> 输出：`detect` 返回单人检测结果 JSON（含 bbox、center、track_id 等字段），无检测返回 `{}`；`count` 返回人数数字。
```

- [ ] **Step 7: 在 mission_pad.py 代码块后添加输出说明**

定位到第 118 行（`` ``` `` 结束后），添加：

```markdown
> 输出：
> - `enable`/`disable` → `ok`
> - `id` → 挑战卡 ID 数值（`1`-`8`），未识别返回 `-1`
> - `xyz` → `"x y z"`（空格分隔，cm）
> - `fly` → 成功返回 `ok`，失败返回 `error: ...`
> - `detect` → `ok`（后台开启预览窗口）
> - `detect_stop` → JSON `{"id": 1, "x": 0, "y": 0, "z": 0}`
```

- [ ] **Step 8: 在 task_search_pad.py 和 task_follow.py 代码块后添加输出说明**

在 task_search_pad.py 代码块后（约第 135 行）添加：

```markdown
> 输出：控制台打印搜索进度和结果，脚本返回退出码 0（成功）/ 1（失败）。
```

在 task_follow.py 代码块后（约第 157 行）添加：

```markdown
> 输出：控制器内部闭环运行。控制台打印 `跟随模式开始...` 和 `跟随结果: ok`。通过 `task status` 可查询运行中状态（JSON，含 elapsed、track_id、rc_speed、tof_distance 等字段）。
```

- [ ] **Step 9: 提交**

```bash
git add SKILL.md
git commit -m "docs: SKILL.md 补充 CLI 命令预期输出说明"
```

---

### Task 3: SKILL.md — 添加 _client.py 禁令 + controller 后台指引

**文件：**
- Modify: `SKILL.md`

- [ ] **Step 1: 在核心架构节末尾添加 _client.py 禁止说明**

定位到第 28-29 行（核心架构节的两个要点之后）：

```
- **`scripts/flight.py` / `led.py` / `vision.py` 等** — 单次命令脚本，执行完即退出
- **`scripts/tasks/`** — 实时闭环脚本，持续运行至超时或任务完成
```

在其后添加：

```markdown

> `scripts/_client.py` 是内部 TCP 通信模块，**禁止直接执行** `uv run scripts/_client.py`。所有无人机操作必须通过各模块 CLI 脚本（`flight.py`、`sensor.py` 等）完成。
```

- [ ] **Step 2: 在连接管理节补充 controller 后台运行指引**

定位到第 46-52 行（连接管理节）：

```
### 连接管理
首次调用任意脚本时自动连接无人机，`land` 后自动断开。

```
python scripts/flight.py takeoff
python scripts/flight.py land
```

连接后自动启动守护线程每 10 秒发送心跳，AI 无需手动管理。
```

在"连接后自动启动守护线程每 10 秒发送心跳，AI 无需手动管理。"之后添加：

```markdown

> `scripts/controller.py` 是持久 TCP 服务器进程，首次调用任意 CLI 脚本时自动启动。如需手动启动（如调试），后台运行即可：
> ```
> uv run scripts/controller.py &
> ```
> 看到 `TCP 服务器监听 127.0.0.1:9999` 等启动日志后即可继续执行后续脚本，controller 在后台持续运行。`land` 后自动断开并退出。
```

- [ ] **Step 3: 提交**

```bash
git add SKILL.md
git commit -m "docs: SKILL.md 补充 _client.py 禁令 + controller 后台运行指引"
```

---

### 验证检查清单

- [ ] controller.py `grep -n "_update_cmd_time"` 仅出现在预期位置
- [ ] SKILL.md 每个模块代码块后有输出说明
- [ ] SKILL.md 核心架构节包含 `_client.py` 禁止说明
- [ ] SKILL.md 连接管理节包含 controller 后台指引
- [ ] `uv run python -c "import ast; ast.parse(open('scripts/controller.py').read())"` 语法正确
