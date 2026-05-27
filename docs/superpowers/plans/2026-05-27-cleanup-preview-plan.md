# 预览功能清理与重构：实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除挑战卡自动检测预览及自动弹窗逻辑，新增手动 `preview_yolo_start`，更新 SKILL.md 文档

**Architecture:** controller.py 裁剪 preview-pad 线程及相关状态，`_handle_yolo` detect 改为单帧推理不再启动预览线程，`_handle_vision` 新增 `preview_yolo_start` 手动启动；mission_pad.py 删除 detect/detect_stop 子命令；task_search_pad.py 移除预览调用

**Tech Stack:** Python 3, DJITelloPy, OpenCV, ultralytics YOLO

---

## 文件结构

| 文件 | 角色 | 改动类型 |
|------|------|----------|
| `scripts/controller.py` | TCP 服务器，管理所有预览线程和 mission_pad 处理 | 修改（删除 + 新增） |
| `scripts/vision.py` | 视觉 CLI | 修改（新增子命令） |
| `scripts/mission_pad.py` | 挑战卡 CLI | 修改（删除子命令） |
| `scripts/tasks/task_search_pad.py` | 挑战卡搜索任务脚本 | 修改（删除调用） |
| `SKILL.md` | 技能文档 | 修改（补充说明） |

---

### Task 1: controller.py — 删除 challenge pad 预览相关代码

**文件:** `scripts/controller.py`

**涉及行号:** 78–84, 690–742, 1159–1168, 1174–1176, 1178–1215, 158–159

- [ ] **Step 1: 删除 `__init__` 中的 pad 预览状态变量（lines 78–84）**

```python
# 删除以下 7 行：
        # --- 挑战卡预览共享状态（_state_lock 保护） ---
        self._preview_pad_stop = Event()
        self._preview_pad_thread = None
        self._pad_shared = {
            "id": -1, "x": 0, "y": 0, "z": 0,
            "active": False,
        }
```

- [ ] **Step 2: 删除 `_preview_pad_loop` 方法（lines 690–742）**

删除从 `def _preview_pad_loop(self):` 到 `self._pad_shared["active"] = False` 前的第 742 行为止的整个方法（约 53 行）。

- [ ] **Step 3: `_handle_mission_pad` `id` action — 删除 `_pad_shared` 写入（lines 1159–1168）**

将：
```python
        elif action == "id":
            pad_id = self.tello.get_mission_pad_id()
            if 1 <= pad_id <= 8:
                with self._state_lock:
                    self._pad_shared["id"] = pad_id
                x = self.tello.get_mission_pad_distance_x()
                y = self.tello.get_mission_pad_distance_y()
                z = self.tello.get_mission_pad_distance_z()
                with self._state_lock:
                    self._pad_shared.update({"x": x, "y": y, "z": z})
            return str(pad_id)
```

改为：
```python
        elif action == "id":
            pad_id = self.tello.get_mission_pad_id()
            return str(pad_id)
```

- [ ] **Step 4: `_handle_mission_pad` `xyz` action — 删除 `_pad_shared` 写入（lines 1169–1177）**

将：
```python
        elif action == "xyz":
            x = self.tello.get_mission_pad_distance_x()
            y = self.tello.get_mission_pad_distance_y()
            z = self.tello.get_mission_pad_distance_z()
            pad_id = self.tello.get_mission_pad_id()
            if 1 <= pad_id <= 8:
                with self._state_lock:
                    self._pad_shared.update({"id": pad_id, "x": x, "y": y, "z": z})
            return f"{x} {y} {z}"
```

改为：
```python
        elif action == "xyz":
            x = self.tello.get_mission_pad_distance_x()
            y = self.tello.get_mission_pad_distance_y()
            z = self.tello.get_mission_pad_distance_z()
            return f"{x} {y} {z}"
```

- [ ] **Step 5: `_handle_mission_pad` — 删除 `detect` 和 `detect_stop` action 分支（lines 1178–1215）**

删除从 `elif action == "detect":` 到 `return json.dumps(...)` 行为止的整个 `detect` 和 `detect_stop` 分支（约 38 行）。

删除后 `_handle_mission_pad` 以 `elif action == "fly":` 继续。

- [ ] **Step 6: `_dispatch` — 删除 `detect`/`detect_stop` 特殊路由（lines 158–159）**

将：
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

改为：
```python
        elif module == "mission_pad":
            if action == "fly":
                with self._flight_lock:
                    self._update_cmd_time()
                    return self._handle_mission_pad(action, remaining)
            else:
                with self._flight_lock:
                    return self._handle_mission_pad(action, remaining)
```

- [ ] **Step 7: 验证——运行 controller 语法检查**

```bash
uv run python -c "import py_compile; py_compile.compile('scripts/controller.py', doraise=True)"
```

- [ ] **Step 8: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: 删除挑战卡预览线程及 detect/detect_stop action"
```

---

### Task 2: controller.py — YOLO detect 取消自动弹窗 + 新增 preview_yolo_start

**文件:** `scripts/controller.py`

**涉及行号:** 854–907 (_handle_yolo detect 分支), 370–437 (_handle_vision action 路由)

- [ ] **Step 1: 重写 `_handle_yolo` 的 `detect` 分支为单帧推理（lines 866–907）**

将 `if action == "detect":` 到 `return json.dumps(target, ensure_ascii=False)` 之间的代码（约 42 行）替换为：

```python
        if action == "detect":
            results = self._yolo_model.track(
                frame, classes=[0], persist=True,
                tracker='botsort.yaml', verbose=False
            )
            detections = self._parse_track_detections(results[0], model_type)

            if not detections:
                self._follow_target_id = None
                return json.dumps({}, ensure_ascii=False)

            target = None
            if self._follow_target_id is not None:
                target = next(
                    (d for d in detections if d['track_id'] == self._follow_target_id), None)
            if target is None:
                target = min(detections, key=lambda d:
                    (d['center'][0] - frame_cx) ** 2 + (d['center'][1] - frame_cy) ** 2)
                self._follow_target_id = target['track_id']

            return json.dumps(target, ensure_ascii=False)
```

- [ ] **Step 2: 在 `_handle_vision` 中新增 `preview_yolo_start` action（插入在 `preview_yolo_stop` 之后、`if action == "stream_on"` 之前）**

在 line 394 前插入：

```python
        elif action == "preview_yolo_start":
            model_type = "pose"
            for i, a in enumerate(args):
                if a == "--model" and i + 1 < len(args):
                    model_type = args[i + 1]
                    break
            with self._state_lock:
                yolo_running = (self._preview_yolo_thread is not None
                                and self._preview_yolo_thread.is_alive())
            if yolo_running:
                return "ok"
            with self._state_lock:
                fr = self._frame_read
            if fr is None:
                return "error: stream not started"
            self._preview_yolo_stop.clear()
            with self._state_lock:
                self._yolo_shared["fresh"] = False
            t = Thread(target=self._preview_yolo_loop, args=(model_type,),
                       daemon=True, name="preview-yolo")
            t.start()
            with self._state_lock:
                self._preview_yolo_thread = t
            logger.info("YOLO 预览窗口已开启")
            return "ok"
```

- [ ] **Step 3: 验证——运行 controller 语法检查**

```bash
uv run python -c "import py_compile; py_compile.compile('scripts/controller.py', doraise=True)"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/controller.py
git commit -m "refactor: yolo detect 取消自动弹窗，改为单帧推理；新增 preview_yolo_start 手动启动"
```

---

### Task 3: vision.py — 新增 preview_yolo_start 子命令

**文件:** `scripts/vision.py`

- [ ] **Step 1: 新增 `preview_yolo_start` 子命令解析器**

在 `sub.add_parser('preview_yolo_stop', ...)` (line 29) 之后插入：

```python
    p_ylo_start = sub.add_parser('preview_yolo_start', help='手动开启 YOLO 标注预览窗口')
    p_ylo_start.add_argument('--model', '-m', choices=['pose', 'seg'], default='pose',
                              help='模型类型（默认 pose）')
```

- [ ] **Step 2: 新增 `preview_yolo_start` 命令组装逻辑**

在 `elif args.action in ('preview_start', 'preview_stop'):` (line 37) 之后插入：

```python
    elif args.action == 'preview_yolo_start':
        cmd = f"vision preview_yolo_start --model {args.model}"
```

- [ ] **Step 3: 验证——运行 vision.py 帮助信息**

```bash
uv run python scripts/vision.py --help
```

- [ ] **Step 4: 提交**

```bash
git add scripts/vision.py
git commit -m "feat: vision.py 新增 preview_yolo_start 子命令"
```

---

### Task 4: mission_pad.py — 删除 detect/detect_stop 子命令

**文件:** `scripts/mission_pad.py`

- [ ] **Step 1: 删除 `detect`/`detect_stop` 子命令解析器（lines 20–21）**

删除：
```python
    sub.add_parser('detect', help='开启挑战卡预览窗口（非阻塞）')
    sub.add_parser('detect_stop', help='关闭挑战卡预览窗口并返回最后检测结果')
```

- [ ] **Step 2: 验证——运行 mission_pad.py 帮助信息**

```bash
uv run python scripts/mission_pad.py --help
```
确认只显示 `enable`、`disable`、`id`、`xyz`、`fly`。

- [ ] **Step 3: 提交**

```bash
git add scripts/mission_pad.py
git commit -m "refactor: 删除 mission_pad detect/detect_stop 子命令"
```

---

### Task 5: task_search_pad.py — 删除 detect/detect_stop 调用

**文件:** `scripts/tasks/task_search_pad.py`

- [ ] **Step 1: 删除 `preview_started` 变量及相关的 detect/detect_stop 调用**

删除以下内容：
1. Line 28: `preview_started = False`
2. Lines 37-39: `if not preview_started:` 块（含 `send_command("mission_pad detect")` 和 `preview_started = True`）
3. Lines 72-74: `if preview_started:` 块（含 `send_command("mission_pad detect_stop")` 和 print）

最终脚本为：

```python
#!/usr/bin/env python3
"""方向搜索挑战卡——小步飞行→等待→检测挑战卡→飞上方+亮灯+屏显"""
import argparse
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='方向搜索挑战卡')
    parser.add_argument('--direction', '-d', required=True,
                        choices=['f', 'b', 'l', 'r'])
    parser.add_argument('--step', type=int, default=30, help='每步距离 cm')
    parser.add_argument('--max-attempts', type=int, default=10, help='最大尝试次数')

    args = parser.parse_args()
    direction = args.direction
    step = args.step
    max_attempts = args.max_attempts

    dir_map = {'f': 'f', 'b': 'b', 'l': 'l', 'r': 'r'}

    send_command("mission_pad enable")

    found = False
    for i in range(max_attempts):
        resp = send_command(f"flight move {dir_map[direction]} {step}")
        if resp.startswith("error"):
            print(f"飞行错误: {resp}")
            break

        time.sleep(1.0)

        pad_id = -1
        for _ in range(5):
            resp = send_command("mission_pad id")
            if resp.startswith("error"):
                print(f"检测错误: {resp}")
                break
            try:
                pid = int(resp)
            except ValueError:
                pid = -1
            if 1 <= pid <= 8:
                pad_id = pid
                break
            time.sleep(0.2)

        if 1 <= pad_id <= 8:
            print(f"检测到挑战卡 #{pad_id}")
            send_command(f"mission_pad fly --id {pad_id}")
            send_command("led solid 0 0 255")
            send_command(f"matrix static b {pad_id}")
            found = True
            break

        print(f"尝试 {i+1}/{max_attempts}，未检测到挑战卡")

    if not found:
        print(f"超 max_attempts={max_attempts}，未找到挑战卡")
        send_command("led solid 255 0 0")
        time.sleep(1)
        send_command("led off")

    send_command("mission_pad disable")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证——运行 syntax check**

```bash
uv run python -c "import py_compile; py_compile.compile('scripts/tasks/task_search_pad.py', doraise=True)"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/tasks/task_search_pad.py
git commit -m "refactor: task_search_pad 删除 mission_pad detect/detect_stop 调用"
```

---

### Task 6: SKILL.md — 文档更新

**文件:** `SKILL.md`

- [ ] **Step 1: 连接管理部分——补充 controller 进程清理指引**

在"连接管理"小节的引用块之后（line 59 的 `>` 块之后）插入：

```
重新连接无人机后，先清理旧 controller 再启动：

\`\`\`bash
pgrep -f "scripts/controller.py" && pkill -f "scripts/controller.py" && sleep 1
uv run scripts/controller.py &
\`\`\`

> \`pgrep\` 检查残留进程，有则 \`pkill\` 终止，\`sleep 1\` 等待退出，最后启动新 controller。
```

- [ ] **Step 2: vision.py 模块——补充预览命令**

将 vision.py 模块的命令块（lines 117–123）替换为：

```
\`\`\`
python scripts/vision.py stream_on
python scripts/vision.py stream_off
python scripts/vision.py photo --name <文件名>
python scripts/vision.py record_start --name <文件名>
python scripts/vision.py record_stop
python scripts/vision.py preview_start <forward|downward>   # 手动开启纯净预览窗口
python scripts/vision.py preview_stop <forward|downward>    # 关闭纯净预览窗口
python scripts/vision.py preview_yolo_start [--model <pose|seg>]  # 手动开启 YOLO 标注预览窗口
python scripts/vision.py preview_yolo_stop                  # 关闭 YOLO 标注预览窗口
\`\`\`
```

- [ ] **Step 3: 提交**

```bash
git add SKILL.md
git commit -m "docs: SKILL.md 补充预览命令及 controller 清理指引"
```

---

## 验证方案

全部任务完成后，在可连接无人机时执行：

1. **YOLO 单帧检测无弹窗**：
   ```bash
   uv run scripts/yolo.py detect  # 确认无 GUI 窗口弹出，仅输出 JSON
   ```

2. **手动 YOLO 预览**：
   ```bash
   uv run scripts/vision.py preview_yolo_start  # 确认手动可开窗口
   uv run scripts/vision.py preview_yolo_stop    # 确认关闭
   ```

3. **挑战卡搜索无下视切换**：
   ```bash
   uv run scripts/vision.py record_start --name test.avi
   uv run scripts/tasks/task_search_pad.py --direction f --step 30
   uv run scripts/vision.py record_stop
   # 确认录像文件正常保存，未被中断
   ```

4. **Controller 清理**：
   ```bash
   pgrep -f "scripts/controller.py" && pkill -f "scripts/controller.py" && sleep 1
   uv run scripts/controller.py &
   ```
