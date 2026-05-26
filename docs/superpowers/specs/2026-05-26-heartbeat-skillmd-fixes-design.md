# 设计文档：心跳保活修复 + SKILL.md 文档完善

## 背景

两个问题需要修复：

1. **controller 心跳计时器被非飞行控制命令重置**：当前 `_dispatch()` 中 sensor/led/matrix 等模块也会调用 `_update_cmd_time()`，但这些命令（ESP32 扩展命令、UDP 读取命令）不会重置 Tello 固件的 15 秒自动降落计时器。后果：持续查询传感器而不发飞行控制命令时，心跳 `rc_control(0,0,0,0)` 不会触发，Tello 可能自动降落。

2. **SKILL.md 文档缺失**：CLI 命令没有预期输出说明、没有禁止直接调用 `_client.py` 的提示、没有 controller 后台运行的指引。

### SDK 依据

SDK 3.0 第 2 节明确：`battery?` 查询不重置 15 秒计时器。由此推及：
- 所有 UDP 读取命令（`speed?`、`time?` 等）同理不计入保活
- ESP32 扩展命令（`EXT led`、`EXT mled`、`EXT tof?`）通过串口发给开源控制器，不经 Tello UDP 8889，不计入保活
- `streamon`/`streamoff` 是 SDK 控制命令（UDP 8889），计入保活

## 设计

### 改动 1：controller.py — 心跳计时器修复

**文件**：`scripts/controller.py`

在 `_dispatch()` 方法中，移除以下分支的 `self._update_cmd_time()`：

| 模块 | 受影响分支 | 原因 |
|------|-----------|------|
| `sensor` | 全部 | `battery?` 被 SDK 明确排除；`EXT tof?` 走 ESP32 |
| `led` | 全部 | `EXT led` 走 ESP32 串口 |
| `matrix` | 全部 | `EXT mled` 走 ESP32 串口 |
| `vision` | `photo`、`record_start`、`record_stop`、`preview_yolo_stop` | 仅本地帧操作，不触发 Tello 控制命令 |
| `mission_pad` | `enable`、`disable`、`id`、`xyz`、`detect`、`detect_stop` | 非飞行动作（`detect`/`detect_stop` 本就不调用，无需改动） |

保留 `_update_cmd_time()` 的分支：

| 模块 | 分支 | 原因 |
|------|------|------|
| `flight` | 全部 | 飞行控制核心（UDP 8889 控制命令） |
| `vision` | `stream_on`、`stream_off`、`preview_start`、`preview_stop` | 触发 Tello UDP 8889 控制命令 |
| `mission_pad` | `fly` | 实际飞行动作 |
| `task follow` 循环内 | rc_control 发送 | 闭环飞行控制 |

### 改动 2：SKILL.md — 补充 CLI 命令预期输出

在每个模块速查代码块后添加输出说明行，混合方式：
- 返回 `ok`/`error` 的模块归类说明
- sensor 模块按子命令列出返回值含义
- yolo detect（返回 JSON）、mission_pad detect_stop（返回 JSON）、task follow（返回 JSON）单独说明

格式示例：
```
> 输出：成功返回 `ok`，失败返回 `error: ...`
```

### 改动 3：SKILL.md — 禁止直接调用 `_client.py`

在"核心架构"节末尾添加说明：`scripts/_client.py` 是内部通信模块，禁止直接执行，必须通过各模块 CLI 脚本操作。

### 改动 4：SKILL.md — controller 后台运行指引

在"连接管理"节补充：controller 首次调用脚本时自动启动。如需手动启动，后台运行并看到启动日志后即可继续后续流程。

## 影响范围

- `scripts/controller.py` — `_dispatch()` 方法中 5 处 `_update_cmd_time()` 移除
- `SKILL.md` — 3 处文档补充（输出说明、_client.py 禁止、controller 后台）
- 无新增文件，无 API 变更，向后兼容
