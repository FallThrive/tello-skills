# TOF 距离显示实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修改 task follow 模式下的点阵屏显示逻辑，将目标特征值替换为 TOF 距离米数显示

**Architecture:** 直接修改 `controller.py` 中 `_task_follow_loop` 方法的 LED 矩阵显示代码块，替换为目标丢失显示 "?"、有效距离显示米数整数位、无效数据显示 "-"

**Tech Stack:** Python, DJITelloPy, Tello SDK

---

### Task 1: 修改点阵屏显示逻辑

**Files:**
- Modify: `scripts/controller.py:1052-1068`

- [ ] **Step 1: 替换显示逻辑代码块**

将第 1052-1068 行的现有代码：

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

替换为：

```python
                # ---- LED 矩阵显示（2Hz + 容错） ----
                now = time.time()
                if now - last_matrix_time >= 0.5:
                    with self._flight_lock:
                        try:
                            if target is None:
                                # 目标丢失，显示 "?"
                                self.tello.send_expansion_command("mled s r ?")
                            elif 100 <= tof_dist < 8192:
                                # 有效距离，显示米数整数位
                                meters = tof_dist // 1000
                                self.tello.send_expansion_command(f"mled s r {meters}")
                            else:
                                # 无效距离数据，显示 "-"
                                self.tello.send_expansion_command("mled s r -")
                        except Exception:
                            pass
                    last_matrix_time = now
```

- [ ] **Step 2: 验证语法正确性**

Run: `uv run python -c "import py_compile; py_compile.compile('scripts/controller.py', doraise=True)"`
Expected: 无输出（语法正确）

- [ ] **Step 3: 提交修改**

```bash
git add scripts/controller.py
git commit -m "feat: task follow 点阵屏显示 TOF 距离米数"
```
