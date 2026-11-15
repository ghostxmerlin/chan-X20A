# 视觉爆发 屏幕指示 线协议（collector-0.5.00，2026-11-15）

主人在围观（MEET/crowd）说“注意看”后，ear 跑 2 分钟视觉爆发（一张张拍照识别，见 [vision-burst-snap](2026-11-15-vision-burst-snap.md)）。主人要求：爆发进行中，机器人屏幕上要看得出来。本文是机器人侧（collector-0.5.00）已实现的契约，ear 侧按此发送。机器人代码：`main/collector/burst_indicator.h`（纯逻辑，主机测试 `tests/burst_indicator_test.cpp`）、`mouth_link.cpp`（解析、打包）、`collector.cpp`（LVGL 定时器里跟踪、提示）、`screen.cpp` + `top_row.h`（顶栏绘制）。

**只是显示**：不改模式、不回任何消息、不开关相机。

## 1. 帧：ear → 机器人（/mouth 文本帧）

```json
{"type":"burst","on":true,"left_s":120,"looks":0}
{"type":"burst","on":true,"left_s":97,"looks":5}
{"type":"burst","on":false}
```

- 何时发：爆发开始时、每次识别完一张后（约 3–6 s 一次）、结束时。重连 /mouth 后爆发还在跑，下一次识别后照常发 `on:true` 即可恢复显示。
- `on`：必须是 JSON 布尔。
- `on:true` 时：
  - `left_s`：剩余秒数，整数 **0–600**。
  - `looks`：到目前为止识别完的张数，整数 ≥ 0（大于 4095 按 4095 记；屏幕最多显示 999）。
- `on:false` 时其他字段一律忽略。
- 整条消息 **≤ 200 字节**；多余/未知字段忽略。
- 坏消息（超长、`on` 缺失或非布尔、`on:true` 时 `left_s` 缺失/非整数/越界、`looks` 缺失/负数/小数/字符串/布尔/null）：只计数 `mouth.bad_messages`，**不回复、不改显示**。
- 旧固件（≤ 0.4.99）把 `burst` 当未知类型静默忽略。

## 2. 屏幕表现

- **顶栏**（首页，0.4.98 的那一行）：在名字和 Wi-Fi 信号之间加一组：粉色眼睛图标 + 白色张数 + 下方 2 px 粉色时间条（按 `left_s / 本次爆发见过的最大 left_s` 从右往左变短，底下是暗灰轨道）。
  - 名字优先：名字放得下时显示完整一组；名字太长放不下时，张数让位，只留眼睛和时间条（名字仍按字符截断，和 0.4.98 一样）。
  - 倒计时在机器人时钟上从最后一帧起走（向上取整到秒），不需要 ear 每秒发。
- **提示条**（屏幕底部 2 s，和模式提示同一个位置，后到的覆盖先到的）：
  - 关 → 开：`LOOKING 2 MIN`（`left_s` 向上取整到分钟；不足 60 s 显示 `LOOKING 45 S`，0 显示 `LOOKING`）。
  - 开 → 关：`LOOK DONE <n>`，n = 最后一次收到的 `looks`。由停止帧、超时或断线导致的关闭都显示这一条（原因只写日志 `burst off (frame|timeout|link)`）。
- 屏幕关闭时不画、不弹提示，但跟踪照常（开屏后看到的是当前状态）。

## 3. 超时与断线

- **安全超时**：从最后一帧（任何合法 `on:true` 帧）到达起，`max(left_s + 10 s, 15 s)` 内没有新帧，指示自动关闭（停止帧丢了也不会一直亮）。每一帧都重新起算，即使内容和上一帧一样。
- **/mouth 断线**：指示立即关闭。重连后 ear 的下一条 `on:true` 会再次显示（并再弹一次 `LOOKING …`）。
- 超时关闭后若又收到 `on:true`（ear 只是慢了），视为新一轮开始。

## 4. 机器人内部（审阅用）

- /mouth 任务解析后把帧打包成一个 32 位原子字（内部 RAM，原子量不能放 PSRAM）：`[31:24] seq(1–255) [23] on [22] lost [21:12] left_s [11:0] looks`。只有 /mouth 任务写；LVGL 定时器每轮无锁读一次。断线时若当前为开，写一个 `lost=1` 的关闭字。
- LVGL 定时器里的 `burst::Tracker`（PSRAM，开机后首次用到时分配一次）：seq 变化 = 新帧；否则只走倒计时和超时。顶栏只在张数或时间条像素变化时重画（2 分钟、约 40 px 的条约每 3 s 变 1 px）。
- 遥测 `GET /v1/telemetry` 的 `mouth.burst`：`{on,left_s,looks,lost,frames}` = 最后收到的帧（不是倒计时后的值），`frames` 为合法 burst 帧计数。

## 5. 测试

- `python3 -B -m unittest discover -s tests -p 'test_burst_indicator.py' -v`：解析（含 200 B 边界、各类坏值）、打包、开始/更新/停止、超时（`left_s+10`、最少 15 s、每帧重新起算）、断线、提示文字、时间条。
- `test_top_row.py`：布局里眼睛组的位置、名字让位规则。
- 主机 LVGL 预览 `tests/display/top_row_preview.cpp`（`burst-*` 各图）：开始提示、张数、即将结束、无名字、长名字只留眼睛、结束提示，并检查名字不被遮挡。
