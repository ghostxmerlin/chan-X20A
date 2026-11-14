# Collector 局域网协议 v1

## 0.4.92：hello 的模式（以 hello 为准，2026-11-14）

主人 2026-11-14 定：模式由 hello 自己决定，服务器只记录、只按"效果"开关自己的功能。开机默认脱口秀（`talk`），模式只在内存里，不写 NVS。切换规则和 `server/mind/modes.py` 是同一套：固件 `collector/mode_rules.h` 逐条重放 `tests/data/mode_vectors.json`（由 `tests/gen_mode_vectors.py` 从 `modes.py`、`mode_table.py` 和下面的数字表生成，共 2100 条，新状态、结果码、提示和效果都要一样）。

**八个模式**（`mode_table.py`）：`talk` 脱口秀 TALK、`chat` 对话 CHAT、`settings` 设置 SET（临时层，记 `return_to`）、`event` 事件驱动 AUTO（外层）、`subtitle` 字幕 SUB ZH>EN / SUB EN>ZH、`crowd` 围观 MEET、`companion` 陪伴 PAL、`idle` IDLE。

**ASR 口令的新含义**（先说 "Hi M Five"、"hello" 或 "hi A S R" 唤醒）。旧的本机用途都去掉了：

| 编号 | 词 | 以前 | 0.4.92 起 |
| --- | --- | --- | --- |
| `0x07` | forward | 无（0.4.88 起解绑） | 进脱口秀；已在脱口秀 = 说一轮 |
| `0x09` | backward | 无 | 离开脱口秀（回 IDLE） |
| `0x16` / `0x17` | play / pause | 无 | 进 / 离开对话 |
| `0x10` / `0x11` | open / close | 开 / 关水平追踪（也取消开机自动追踪） | 进 / 离开设置；不再开关追踪，也不再取消开机自动追踪 |
| `0x12` / `0x13` | start / stop | 连续听开始 / 停止 | 进 / 离开事件驱动 |
| `0x1B` | next | 800 ms 后随机播一段本机语音 | 脱口秀里 = 开提前写；别的模式 `only_talk` |
| `0x1A` | previous | 无 | 脱口秀里 = 关提前写；别的模式 `only_talk` |
| `0x20`–`0x29` | zero..nine | 无 | 数字，见下表 |

不变、仍在本机：`up`/`down`（开 / 关追踪）、`left`/`right`、`turn left`/`turn right`、`front`/`back`（0.4.85 转头），`turn on`/`turn off`（M12 人脸视图），音量词，`hello`。

**数字**（主人 2026-11-14 新表，取代旧预设表）：

| 数字 | 做什么 | 谁执行 |
| --- | --- | --- |
| 0 | 花费全关 | mind（hello 发 `mode_input` `digit` 0，屏幕显示 mind 回的提示） |
| 9 | 花费全开 | mind（同上） |
| 4 / 5 / 6 | 进 IDLE / 围观 / 陪伴 | hello 自己 |
| 7 / 8 | 进字幕 中→英 / 英→中 | hello 自己 |
| 1 / 2 / 3 | 空（`no_action`） | — |

4–8 是"进入"：已经在那个模式（字幕要方向也相同）时回 `already`，什么都不变，不会像 `select` 那样"再点 = 离开"；字幕换方向照常切换。

**首页手势**（`collector/touch_gesture.h`，只在首页）：单击、双击按当前模式（脱口秀：单击 = 说一轮，hello 正在说话时 = 停；双击 = 开提前写；其他模式见 `mode_table.py` 的 `inputs`）。单击要等 300 ms 看有没有第二下。**左滑打开状态摘要页**（以前是轻点）。上滑、下滑、右滑这一版不接。其他页面的触摸照旧。

**屏幕**：右上角角标，所有页面可见：模式的 ASCII 名字加颜色底；`/mouth` 没连上时变灰并加 ` OFF`。每次输入的结果在底部显示 2 秒（例如 `CHAT`、`SAYING`、`NO ACTION`、`OFFLINE`）。眼睛：脱口秀一律笑面男（开着视觉也是）；对话 v05 金色猫眼；围观 v14 绿色机甲眼，热闹表情池（迎接、开心、好奇、搞怪、惊讶）；陪伴 v18 奶油白眼睛；字幕、设置、事件驱动、IDLE 这一版先是笑面男（专用页在 0.4.93）。

**设置**：5 分钟没有任何输入就回原来的模式（hello 自己计时，结果 `by: "timeout"`）。

**`POST /v1/control {"target":"mode","input":<输入词>,"value":<值，可省>}`**（网页模式条用）：照 ASR 口令同样执行，来源记 `page`。`input` 取 `forward`、`backward`、`play`、`pause`、`open`、`close`、`start`、`stop`、`next`、`previous`、`tap`、`double_tap`、`long`、`direction`（`up`/`down`/`left`/`right`）、`select`（`{"mode":..., "dir":"zh_en|en_zh"}`，再选当前模式 = 离开）、`digit`（0–9）。不认识的词回 200、`code: "unknown_input"`；`billing` 或多余字段回 400（计费总闸在 mind）。返回：

```json
{"accepted":true,"code":"ok","toast":"CHAT","changed":true,"reported":true,
 "mode":{"mode":"chat","dir":null,"host":null,"return_to":null,"seq":7,"label":"CHAT","by":"page","since_ms":0,"online":true, ...}}
```

`code` 是规则结果码（`ok`、`already`、`not_in_mode`、`only_talk`、`not_implemented`、`no_action`、`unknown_input`）；数字 0 / 9 是 `sent_to_mind`（已排队发给 mind，`input_seq` 是它的编号）或 `offline`。

**`/mouth` 上的模式消息**（hello ↔ ear；ear 原样转给 mind）：
- 上行 `{"type":"mode_report","seq":N,"mode":"talk","dir":null,"host":null,"return_to":null,"by":"asr|touch|page|timeout|boot","input":"forward","effects":["stop_speech","talk_enter"]}`：每次切换、或者有效果的输入（说一轮、开关提前写、停说）发一条。`seq` 是 hello 本次开机的递增编号。`effects` 取 `stop_speech`、`talk_leave`、`talk_enter`、`say_round`、`prefetch_on`、`prefetch_off`，由 mind 执行（计费总闸照样拦）。
- 每次连上 `/mouth`（收到 ear 的 hello）先发一条当前状态：开机后第一次是 `by:"boot"`、`input:"boot"`；以后 `input:null`、`by` 是最近一次切换的来源。它的 `effects` 是从 mind 上一次收到的状态到现在要做的切换效果，所以离线时切的模式也会让 mind 关掉 / 打开提前写和连续说。断线时没发出去的报告丢掉，由这一条代替。报告按顺序发，socket 收下才算发出；一下子积了 8 条没发出去，就合成一条（当前状态 + 从上次发出的状态起的切换效果 + 最新输入自己的效果）。
- 上行 `{"type":"mode_input","seq":N,"age_ms":A,"source":"asr","input":"digit","value":0}`：只有数字 0 和 9。
- 下行 `{"type":"mode_result","seq":N,"code":"ok","toast":"..."}`：只回给 `mode_input`；`toast` 显示 2 秒（空的不显示）。旧版 ear 推的 `{"type":"mode"}` 快照 0.4.92 忽略。
- 收到的文本消息先查长度（≤ 1600 B）再完整解析，解析失败的不看；JSON 池在 PSRAM；模式处理有自己的锁，不碰音频锁。

**遥测 `telemetry.mode` / `info.mode`**：`mode`、`dir`、`host`、`return_to`、`seq`、`label`、`by`、`since_ms`、`online`、`toast`、`reports_sent`、`reports_pending`、`reports_offline`、`reports_collapsed`、`inputs_sent`、`inputs_queued`、`inputs_dropped`、`inputs_offline`、`results`、`bad_results`、`ignored`、`last_result{seq,code,toast}`。`capabilities.modes="robot"`；`info.asr.mode_words` 列出口令的新含义。telemetry `hearing` 不再有 `asr_starts` / `asr_stops`（口令不再开关连续听）。

## 0.4.46：单一配置端 `POST /v1/control`

- 主端口 8080 只剩 7 条路由：`GET /v1/discovery`、`GET /v1/info`、`GET /v1/telemetry`、`GET /v1/frame`、`POST /v1/control`、`POST /v1/assets`、`POST /v1/playback`；8081 仍只有 `GET /v1/audio`。HTTP 处理器上限由路由表长度决定（`kMainRoutes`），新增路由不会再像首个 0.4.45 镜像那样溢出而重启循环。
- `POST /v1/control`（Bearer + 必带当前 `X-Boot-Id`，JSON 对象 ≤ 768 B）：`{"target": <名称>, ...}`。除 `target` 外，字段、校验、状态码和响应 JSON 与被替换的各接口完全相同；缺少/未知 `target` 返回 400。对照：

| target | 替换的接口 | 字段 |
| --- | --- | --- |
| `remote` | `POST /v1/remote` | `enabled` |
| `pitch` | `POST /v1/settings/pitch` | `angle_deg` |
| `camera` | `POST /v1/camera` | `enabled` |
| `servo` | `POST /v1/servo` | `axis`、`op`、EEPROM/运行时字段 |
| `wifi` | `POST /v1/wifi` | `op`、`ssid`、`password`、`preferred`、`connect` |
| `m12_view` | `POST /v1/m12/view` | `enabled` |
| `m12_mode` | `POST /v1/m12/mode` | `mode` |
| `yaw_step` | `POST /v1/motion/yaw-step` | `delta_deg` 或 `slot`，可选 `speed`、`path` |
| `voice_play` | `POST /v1/voice/play` | `clip_id` |
| `interaction` | `POST /v1/interaction` | `mode`、`enabled` |
| `hearing` | `POST /v1/hearing` | `op` 及各操作字段 |
| `eyes` | `POST /v1/eyes` | `op`、`request_id` 及各操作字段 |
| `mode` | （0.4.92 新增，无旧接口） | `input`、可选 `value`，见 0.4.92 一节 |

- 删除的 GET：`/v1/wifi`（遥测 `wifi` 为同一对象）、`/v1/eyes`（目录在 `/v1/info` 的 `eyes`）、`/v1/eyes/status`（状态在遥测 `eyes`，含 `storage.installing` 与 `last_request`；`?request_id=` 单条查询随之取消）。
- `POST /v1/assets` 合并原 `/v1/eyes/assets` 与 `/v1/voice/assets`：带 `X-Voice-Id` 头为语音片段，否则需 `X-Eyes-Style` + `X-Eyes-State`；长度校验、SD 安装与单任务门控不变。`POST /v1/playback`（PCM 上传，服务端使用）不变。
- `/v1/info`：`capabilities.unified_control="supported"`；新增 `control{endpoint,method,body_max_bytes,headers,targets[]}` 与 `assets{endpoint,selector}`；`pitch_setting.endpoint`、`camera.endpoint` 改为 `/v1/control` 并附 `target`。
- 旧 Android 观察台使用的 `/v1/remote`、`/v1/settings/pitch` 等已移除（用户决定：Android 之后按网页重做，不再兼容）。观察台 `control.mjs` 在固件缺少 `unified_control` 能力时拒绝控制并提示固件过旧。

## 0.4.45：已保存网络、扫描与本体 Wi-Fi 设置页

- 网络列表改为单个 collector NVS 值 `wifi_profiles`（JSON 数组，最多 6 个，首项最先尝试）；首次启动自动从旧 `wifi_primary` / `wifi_ssid` 迁移。候选顺序：已保存网络 → 未改动的出厂列表。断线/重连总从首项开始，每项最多 3 次、间隔 10 s。
- `GET /v1/wifi`（Bearer）：`connected`、`ssid`、`rssi_dbm`、`channel`、`ip`、`status`（本体当前的连接状态文字）、`profiles[{ssid,preferred}]`、`profile_limit`、`factory_count`、`factory[ssid]`（出厂列表的网络名，0.4.91 起）、`scanning`、`scan_age_ms`、`scan[{ssid,rssi_dbm,secure,saved}]`（最近一次扫描，按信号排序、去重、最多 8 个）。不返回密码。遥测 `wifi` 为同一对象。能力 `wifi_profiles`。
- `POST /v1/wifi`（Bearer + `X-Boot-Id`）：`{"op":"add","ssid","password","preferred":true,"connect":true}` 保存（同名替换密码，开放网络密码为空）并按优先级重连；`{"op":"remove","ssid"}`（0.4.91 起，不在已保存列表里的名字会从出厂列表删除；删的是当前连着的网络时立即按顺序重连）；`{"op":"prefer","ssid","connect":false}`；`{"op":"scan"}` 请求一次扫描（连接中时自动稍后重试）；`{"op":"reconnect"}`。202 表示已受理，结果看 `GET /v1/wifi`。
- USB 串口命令 `{"cmd":"wifi","ssid","password","priority":true}` 现在写入同一列表并直接重连，不再重启本体。
- 本体屏幕：摘要页长按进入配对页，向左滑进入 Wi-Fi 页（自动扫描，`*` 已保存，`#` 加密，右上 Rescan，标题返回）；点选热点进入数字键盘（1–9/0、`<` 删除、OK 加入；加密网络至少 8 位；开放网络直接 OK）。保存后作为首选并重连，状态行显示 connecting/connected。

## 0.4.41–0.4.42：步进诊断参数与舵机参数接口

- `POST /v1/motion/yaw-step` 接受整数 `delta_deg`（±1…30，缺省页面仍只发 ±5）、可选 `speed`（50–1000）与可选 `path`（`web` 缺省 / `remote`）。`remote` 重放旧实体遥控的 `setAutoAngleSyncEnabled(true)+moveWithSpeed` 调用序列；两者写入舵机的位置指令相同。响应与 `remote.motor.web_yaw` 增加 `path`、`max_delta_deg`。能力 `yaw_step_diagnostics`。
- `POST /v1/servo`（Bearer + `X-Boot-Id`，能力 `servo_tuning`）：`{"axis":"yaw"|"pitch","op":...}`。`op=eeprom` 写入给定的 `p`/`d`/`i`（0–254）、`punch`（0–1000，‰）、`dead_cw`/`dead_ccw`（0–16 步）、`max_torque`（0–1000，‰）；本体先解锁 EEPROM、写入、加锁、再读回 9..27。`op=restore` 写回出厂值 P15 D15 I0 punch30 死区1/1 最大扭矩1000。`op=refresh` 只重新读取。`op=runtime` 设置本次开机的 `goal_time_ms`（0–9999）/`goal_speed`（0–1000，随每次位置写入发送，固件默认 20/0）、`spring_stiffness`/`spring_damping`（同时为 0 清除覆盖）、`hold_min_ms`/`hold_max_ms`/`tolerance_deg`（卸力保持与到位容差）。请求排队给 motor task，轴空闲时执行，10 s 内轴持续忙则过期；同一时刻只允许一条待执行请求（409）。202 只表示已排队。
- 遥测 `remote.motor.<axis>.tuning`：`state`（idle/queued/applying/written/write_failed/applied/refreshed/expired_axis_busy）、`applied_seq`、`pending`、`eeprom`（读回的全部 9..27 字段）、`runtime`、`last_write`（解锁/加锁/写入与失败计数）。`remote.motor.<axis>.motion_summary`：最近一条运动命令的目标/起点/最后实测/误差、峰值负载（‰）、最终目标写入与卸力时刻差。`remote.motor.<axis>` 反馈增加 `speed_raw`、`voltage_raw`（0.1 V）、`temperature_c`、`moving`，来自同一条 56..70 状态块读取。
- 零点、角度限位、相位与保护位不可通过此接口写入。

## 0.4.39：网页舵机到位保持、运动轨迹证据、机身相机开关

- **自动卸力改为到位门控**（`stackchan/motion/servo.cpp`，两轴共用）：最后一次位置写入后至少保持 500 ms；期间每 200 ms 读一次实测位置，误差 ≤1.0°（约 3 个编码步）才释放扭矩；读取失败或误差过大最多保持 2000 ms 后仍按原厂逻辑释放。`ReadMove=0` 不再单独作为释放依据。显式停止/取消立即释放并清除保持。原 0.4.38 现场记录为最终目标写入后 35.4 ms 即释放，与主机复现的 20 ms 一致。
- `POST /v1/motion/yaw-step` 增加可选整数字段 `speed`（50–1000，弹簧映射输入，不是物理速度）；缺省 600，与实体遥控已验收的 yaw 速度相同（此前固定 100，目标每 2–7 个 20 ms tick 才前进 1 个编码步）。响应增加 `speed`；`remote.motor.web_yaw` 增加 `speed` 与 `default_speed`。
- `GET /v1/telemetry?servo_diag=1` 在 `remote.motor.{yaw,pitch}.diagnostics.motion_trace` 返回最近一条运动命令的有界轨迹：`source`（web_yaw / tracking / pitch_setting / boot_pitch）、`request_seq`、`target_deg`、`started_us`、`closed_us`、`active`、`truncated`、`start` 反馈、`samples`（最多 160 行 `[timestamp_us, position_raw, angle_deg]`，取自 motor task 既有的 100 ms 位置读，不新增 UART 流量；读取失败时后两项为 `null`）、以及属于该命令窗口的 `final_goal_write` 与 `torque_disable` 事务。轨迹是实测位置记录，`trajectory_complete` 仍不等于到位。
- **机身相机默认关闭**：`collector` NVS 的 `camera_on` 缺省为 0；关闭时不采集、不编码 JPEG，`GET /v1/frame` 返回 503。`POST /v1/camera {"enabled":bool}`（Bearer + `X-Boot-Id`）保存并立即生效，响应 `{accepted,enabled,persisted}`。`/v1/info` 的 `camera` 增加 `enabled`、`default_enabled=false`、`endpoint`，`capabilities.camera_control="supported"`；`health.camera_enabled` 为当前状态。传感器驱动仍在开机初始化，关闭只停止取帧/编码/对外输出。Android 观察台未改，相机关闭时只会收到 503。
- 本体摘要页第一行显示所连 Wi-Fi 名称（断开时仍显示 `offline`）；HTTP 遥测仍不返回 SSID。

## 0.4.18：芯片温度遥测

`GET /v1/info` 和 `GET /v1/telemetry` 增加 `temperature`；`capabilities.chip_temperature=supported`。
字段：`available`（驱动启用）、`valid`（读取成功且小于三秒）、`celsius`（无效为 null）、`timestamp_us`、`seq`、`error`；固定 `source=esp32s3_internal`、`scope=chip_not_ambient_or_case`、`unit=degC`、`range_min=20`、`range_max=100`、`target_hz=1`。这是 ESP32-S3 芯片内部温度，不是 M12、舵机、外壳或室温。独立低优先级任务采样，HTTP 只读缓存；旧客户端可忽略该字段。

M12 仍使用已有 `results/last_result_us/requested_us/faces` 等字段；网页基于设备计数计算实际结果更新率。没有新增 M12 温度或视频的串口字段。

## 0.4.17 基础版（当前源码）

- `/v1/info.profile` 为 `basic`；`capabilities.emotion_display` 和 `capabilities.face_detection` 为 `unsupported`。情绪 `supported/enabled=false`；不再生产情绪遥测、情绪首页标签。
- `POST /v1/emotion`、`POST /v1/settings/face-detection` 不再注册（404）。机身检测适配器恒关闭，`face_detection.supported/enabled/ready=false`，`reason=disabled_in_basic_profile`；Android 旧版会将人脸框开关置为不可用。M12 检测独立保留。
- M12 开机请求 Audio FFT 待机；ASR turn on 显式请求 Face Detector 并打开人脸页；turn off 或轻触退出后请求待机。检测持续到显式关闭，不因无人脸或时间自动待机；普通触摸、遥控心跳和其他 ASR 指令不唤醒。
- 不发 Face Recognition 命令、不显示缓存身份。`m12.phase` 运行只用 watching/dozing，`function` 是请求态，`valid` 仅表示 UART/任务就绪，不证明模组执行成功；画面数据过期显示 `M12 data stale`。
- 每次功能切换发送对应命令。没有回应时先六次每 8 秒重发，再每 30 秒重发；待机收到明确 Audio FFT 回复后停止重发。长时间待机无法切回检测的历史症状仍需单独实测。
- 基础相机/音频/传感器采集、HTTP/UDP 连接、舵机/仰角、屏幕开关和心心/叉号及提示音保留，不改 NVS 中的设置。

下文各旧版章节仅为历史协议，不覆盖以上当前行为。

## 0.4.11–0.4.13 M12 人脸视图（顶部蓝色口）

用户 2026-11-07 要求：说 “turn on” 时，StackChan 启动 M12 的人脸检测，在自己屏幕上标出人脸方框；说 “turn off” 时关闭，回到首页。

**接线和串口：**
- M5Stack UnitV2 M12 接顶部蓝色口（Port C）。CoreS3 官方引脚表：G17 是 TX，G18 是 RX。
- 机器人用 UART0，115200 8N1。
- **日志控制台改为只走 USB（USB Serial/JTAG）**，以腾出 UART0。三个串口的分配：UART0 = M12，UART1 = 舵机总线，UART2 = Unit ASR。刷机和看日志仍然插同一根 USB 线。
- 开机时只发一个空行，清掉 M12 在 TX 引脚还没配置时收到的杂字节（它会回一条无害的 `invalid json format`），不发功能命令，M12 保持原来的状态。
- **中断放在 1 号核（0.4.12 起）：** 两个串口驱动都在固定于 1 号核的任务里安装。0.4.11 把 UART0、UART2 和 GPIO 中断服务都装在 0 号核，0 号核的通用中断被用完；WiFi 第一次连上路由器、解密 WPA 密钥时要再申请 AES 中断，申请失败就 abort（每次开机约 35 秒崩溃）。0.4.12 实测：开机 8 秒时 0 号核剩 3 个、1 号核剩 13 个通用中断，WiFi 连上后不再崩溃。每次开机 8 秒时 USB 日志会打印一次中断分配表。

**命令：** 每条是一行 JSON，以 CRLF 结尾。M12 会把函数名转成小写、空格换成下划线，再去查自己的函数表。

| 用途 | 命令 |
| --- | --- |
| 开始人脸检测 | `{"function":"Face Detector","args":""}` |
| 待机 | `{"function":"Audio FFT","args":""}` |

- 待机时摄像头关闭，CPU 约 15%，不往串口输出。
- 发出命令 8 秒后还没有回应就重发，最多 6 次。人脸模式下，“没有回应”指没有收到新的人脸结果；待机时，指 4 秒后仍有人脸结果。
- M12 上电后约 30 秒才能接受命令，切换一次功能要 1.5–2.5 秒。

**结果格式：** 每行一个 JSON，字段为 `num`、`face[]` 和 `running`（实测 `running` 在行尾）；每张脸有 `x`、`y`、`w`、`h`、`prob`、`mark`，坐标在 640×480 画面中，**是小数**（如 `"x":168.92`）。
- 坐标按小数读取再四舍五入。0.4.11 按整数读，ArduinoJson 对小数返回整数默认值 0，所有脸宽为 0 被丢弃，屏幕一直显示 `no face`；0.4.13 修正，并把真机抓到的一行加入主机测试。
- 实测人脸检测约 7.5 条结果/秒。
- 固件最多保留 6 张脸，方框裁到画面以内，宽或高为 0 的脸丢弃。
- `{"msg":…}` 和 `{"error":…}` 记作回复。
- 超过 4096 字节的行整行丢弃。

**屏幕：** 人脸视图是一个全屏页。
- **画面：** M12 的 640×480 画面缩到整个 320×240 屏幕，像自拍一样左右镜像。人脸用绿色 3 px 方框表示。
- **状态行（最下方）：**

  | 状态 | 什么时候出现 |
  | --- | --- |
  | `starting M12... Ns` | 已发命令，还没收到人脸结果 |
  | `no data from M12 - check blue port` | 10 秒内一个字节都没收到 |
  | `no face` | 最近 1.5 秒没有新结果，或者结果里没有脸 |
  | `faces N` | 当前画面里有 N 张脸 |

- **退出：** 说 “turn off” 或轻触屏幕，都会回到首页，同时让 M12 进入待机。
- 人脸视图里不显示爱心和叉号。

**info：** `capabilities.m12_serial="supported"`；`m12` 说明端口、串口号、引脚、画面尺寸、两条命令，以及 ASR 命令表。

**telemetry 的 `m12` 对象：**

| 字段 | 含义 |
| --- | --- |
| `valid` | 串口驱动和任务是否在运行 |
| `requested`、`request_seq`、`requested_us`、`sends` | 最近的请求（`face_detector` / `standby` / `none`）、请求次数、最近一次写命令的时间、总发送次数（含重发） |
| `running`、`last_reply` | M12 最近报告在运行的功能、最近的回复或错误 |
| `results`、`last_result_us`、`num`、`faces` | 收到的人脸结果数、最近一次的时间、M12 报的人数，以及保留的人脸框（M12 画面坐标） |
| `rx_bytes`、`last_rx_us`、`lines`、`invalid_lines`、`overflow_lines`、`replies`、`errors` | 串口收发诊断 |

## 0.4.88：开机自动追踪，forward/backward 解绑（2026-11-13）

用户要求：开机后 M12 在，就打开视觉检测和追踪；M12 不在也没关系。`down` 关追踪。`forward`/`backward` 另作他用，不再显示爱心和叉号。

- **开机自动追踪**（`collector/boot_tracking.h`，由 ASR 任务每 100 ms 推进）：开机 30 秒内收到 M12 心跳，就启用舵机（`remote_arm(true)`），舵机就绪后（最多再等 10 秒）打开追踪，追踪会把 M12 切到检测。只做一次。30 秒内没有 M12，就什么都不做。在它生效前说了任何转头词或开关追踪的词（含 `down`；0.4.92 起 `open`/`close` 是模式口令，不再取消它），它就取消，之后也不会再自动打开。结果在 `info.asr.boot_tracking`：`waiting_m12`、`tracking_on`、`no_m12`、`servos_not_ready`、`arm_refused`、`cancelled_by_user`；日志行 `AsrUnit: boot tracking: …`。
- **`down`** 仍是关追踪（0.4.85 起），随时可用；关掉后开机自动追踪不会再打开它。
- **`forward`(0x07) / `backward`(0x09)**：不再显示爱心和叉号，只记日志（0.4.92 起是模式口令：进 / 离开脱口秀，见最上面一节）。`info.asr.screen_reactions` 标为 reserved。遥控器上下推原本也有爱心和叉号；2026-11-13 的审计发现它从提交 `7700e7d` 起就已不再触发（`remote.cpp` 的 `reaction` 没有地方赋值），与这次改动无关，待修。
- 0.4.86 的白屏修复（舵机开机等待回话、读舵机让出 CPU）包含在内。主机测试 500 项通过（4 项跳过），新增 `tests/boot_tracking_test.cpp`。

## 0.4.85：说话转头（Unit ASR 方向词）

用户 2026-11-13 要求：用 ASR 出厂英文词控制水平舵机。八个词都在出厂词表里，编号与 M5Stack `unit_asr` 库的 `commandList` 一致（M5Unit-ASR `src/unit_asr.hpp`）。仍然先说 “Hi M Five”、“hello” 或 “hi A S R” 唤醒，约 20 秒内说方向词。左右都按**机器人自己的左右**，与网页“左/右”按钮相同。

| 编号 | 词 | 做什么 |
| --- | --- | --- |
| `0x01` | up | 开追踪，走 `open` 的同一入口（两步动作进行中不接受） |
| `0x02` | down | 关追踪，走 `close` 的同一入口，随时可用 |
| `0x03` | left | 向左一档 45°（从最近的一档算起） |
| `0x04` | turn left | 向左两档 90°，到 −90° 为止 |
| `0x05` | right | 向右一档 45° |
| `0x06` | turn right | 向右两档 90°，到 +90° 为止 |
| `0x08` | front | 回中；如果头正背对着，先转回来再回中 |
| `0x0A` | back | 转身约 180°；头不在中间时先回中再转 |

**规则（本轮的决定）：**
- **相对档位，到边就停。** left/right 每次一档，turn left/turn right 每次两档；超过 ±90° 的部分截掉。已经在 −90° 时说 left 或 turn left，不动，结果记 `at_limit`。不会出现“往右说却往左走”的情况。
- **转身沿用 0.4.83 的定时转身**：原厂轮转模式、1200 ms、方向交替。背向时位置读数进入盲区，所以固件只记“朝前 / 背向 / 不确定”（遥测 `remote.motor.yaw_turn.facing`）。完整转完一次才在朝前和背向之间切换；转到一半被停用、失败或断电，记为“不确定”。之后只要有一次带有效读数的左右位置动作被接受，就重新记为朝前。
- **front：** 朝前或不确定时直接回中（仍要有效读数）。背向时先做一次反向转身；转完、停 0.5 秒、读数有效后，偏离中心超过 10° 才再回中，否则就此结束。
- **back：** 已经背向时不动（`already_back`）；不确定时不转（`facing_unknown`，请先说 front 或在网页回中）。朝前且离中心超过 10° 时，先回中，停稳 0.5 秒且实测在 ±15° 内才开始转身；回中没到位就放弃（`centre_missed`）。
- **背向时不接受 left/right/turn left/turn right**（`facing_back`），避免在没有读数的位置上瞄准。
- **说方向词会关掉追踪**，与网页按钮相同（追踪状态变成“手动接管”）。要继续追踪就再说 up 或 open。
- **动作进行中说的词直接丢掉，不排队**（`busy`）。两步动作（先转再回中、先回中再转）进行中，除 down 以外的方向词和 up 都算忙；down 随时可以关追踪。两步动作中途如果舵机被停用、有人开了追踪、网页发了别的左右动作、或 6 秒内没走完，第二步取消，绝不在重新启用后补做。
- 所有动作都走网页按钮已有的入口（`remote_yaw_slot`、`remote_turn_yaw`、新加的多档 `remote_yaw_notches`），限位、无进展保护、停用、故障锁存和“只有电机任务写舵机串口”都保持原样。ASR 读取任务每 100 ms 检查一次两步动作，没有新任务、新串口或新缓冲区。

**结果怎么看：**
- USB 日志（监视台右侧总 log、机器人日志页）：`AsrUnit: #12 id=0x03 left -> yaw_left_45 accepted`；两步动作结束时另有一行 `AsrUnit: #13 return_turn plan -> centring`。
- telemetry `asr.events[]` 中方向词带 `action` 与 `result`；`asr.motion` 是最近一次方向词：`seq`、`word`、`action`、`result`、`plan`（`idle` / `centre_then_turn` / `turn_then_centre`）、`plan_result`。
- 动作本身照旧显示在舵机卡片：`web_yaw.state`、`measured_slot`、`yaw_turn.state`、`yaw_turn.facing`，追踪状态。

`result` 取值：`accepted`、`busy`、`motor_off`（舵机未启用）、`no_feedback`（没有 1 秒内的有效读数）、`at_limit`、`facing_back`、`already_back`、`facing_unknown`、`rejected`（舵机侧拒绝，例如仰角在动、正在调参）、`unavailable`（up 时 M12 不在线）。`plan_result`：`pending`、`turn_started`、`front`（转回来已在中间）、`centring`、`centre_missed`、`stopped`、`superseded`、`timeout`。

**其他词不变：** `turn on`/`turn off`、音量词不变。（当时 `forward`/`backward` 已在 0.4.88 解绑，`open`/`close` 开关追踪，`start`/`stop` 开关连续听，`next` 随机播语音；0.4.92 起这些都是模式口令，见最上面一节。）`info.asr.yaw_words` 列出上表，`capabilities.asr_yaw_words="supported"`。

## 0.4.10 Unit ASR（顶部黑色口）

M5Stack Unit ASR（CI-03T）接在顶部黑色口（Port B），使用出厂英文词表（用户 2026-11-07 决定先不定制）。0.4.10 = 0.4.9 源码加 ASR；0.4.11 起日志只走 USB，UART0 给 M12（见下节）。

**串口和接线：**
- 机器人用 UART2，115200 8N1，**只接收**：TX 不分配引脚，固件从不驱动这两根线。UART1 是舵机总线；UART0 在 0.4.10 是日志控制台，0.4.11 起给 M12。
- **模块 TX 接在 GPIO8**（2026-11-07 开发机实测）。GPIO9 是模块的 RX，固件不驱动。
- 0.4.10/0.4.11 曾用 GPIO 中断统计两根线的跳变来自动判断 TX；0.4.12 起改为固定 GPIO8，省下 GPIO 中断服务。读取任务和串口中断在 1 号核。

**帧格式：** 每条识别结果是 `AA 55 <id> 55 AA`；少数回复多带一个值字节，格式是 `AA 55 <id> <value> 55 AA`。编号与 M5Stack `unit_asr` 库一致，例如：

| 编号 | 词 |
| --- | --- |
| `0x01`–`0x06` | up, down, left, turn left, right, turn right（0.4.85 起转头，见上节） |
| `0x07` | forward |
| `0x08` | front（0.4.85 起回中） |
| `0x09` | backward |
| `0x0A` | back（0.4.85 起转身） |
| `0x32` | hello |
| `0xFF` | Hi M Five |

出厂固件的唤醒词是一串乱码，等于没有设。实际要先说 “Hi M Five”、“hello” 或 “hi A S R” 中的一个（这三个是免唤醒词），之后约 20 秒内的其他命令才会被识别。

**屏幕反应（0.4.88 起取消，0.4.92 起这两个词是模式口令）：** 当时首页上 `0x07 forward` 显示爱心，`0x09 backward` 显示实心叉号。复用遥控上推、下推的同一套表情：同样的图、1000 ms、首页限定、新事件替换旧事件。
- **不播提示音：** 模块自己会说出回复语，再加一声会叠在一起，也会被麦克风收进去。
- **日志：** 每一帧都以 `AsrUnit` 标签写日志，在机器人左滑的日志页上能直接看到。

**info：** `capabilities.asr_unit="supported"`；`asr` 说明端口、串口号、波特率、RX 引脚、帧格式、反应表（含 `0x14 turn on` / `0x15 turn off`），以及 `reaction_sound=false`。

**telemetry 的 `asr` 对象：**

| 字段 | 含义 |
| --- | --- |
| `valid` | 串口驱动和任务是否在运行 |
| `rx_gpio`、`rx_seen` | 接收引脚（8）、是否收到过有效帧 |
| `frames` | 收到的有效帧数 |
| `discarded_bytes` | 丢掉的字节数（不成帧的字节） |
| `events` | 最近 16 条记录，每条含 `seq`、`timestamp_us`、`id`，已知编号带 `word`，带值的回复带 `value` |

- `timestamp_us` 是帧最后一个字节被读到的设备单调时间，不是说话的时刻。
- Android 观察台原样记录 telemetry JSON，所以 ASR 记录会自动进入 `telemetry.jsonl`。

## 0.4.8 表情时长

info的`remote.reaction_duration_ms`、telemetry的同名字段及`remote.gesture.reaction_duration_ms`统一为1000。首页收到新鲜表情后，从UI接受时刻计算1秒；保留输入500ms判鲜，过期事件不补播，持续拨杆不循环。提示音仍为360ms、音量80%，声音待播750ms上限独立于显示时长。上下不驱动俯仰，中心仍切换屏幕。

## 0.4.7 人脸框试验接口

`POST /v1/settings/face-detection` 沿用 Bearer 鉴权，正文只能为 `{"enabled":true}` 或 `{"enabled":false}`，最多64字节。成功返回200、`accepted=true`及`face_detection`快照；类型错误、额外键或超长正文返回400。确认表示开关已应用，模型就绪由后续`ready`表达。开关只在本次启动有效，重启默认关闭，不写NVS、不调用运动控制。客户端不自动重放未确认请求。

`/v1/info`、`/v1/telemetry` 的 `face_detection` 包括 `supported/enabled/ready/epoch/frames/errors/frame_seq/timestamp_us/count/inference_us/error/target_hz/max_boxes/model/persistent`。模型为固定 MSR+MNP S8 V1，目标4Hz、最多4个框。开启时相机目标4fps，关闭恢复原10fps目标；实际帧率取决于负载。`camera.base_target_fps=10`。推理与JPEG顺序处理同一帧，不排队、不把旧框套到新画面。关闭时当前推理可以结束，其结果因epoch变化丢弃，下一次采集释放模型和RGB缓冲。

成功推理的`/v1/frame`在原JPEG响应增加`X-Face-Detection`头，例如 `{"epoch":1,"frame_seq":123,"boxes":[[250,200,700,850,910]],"inference_us":80000}`。每框为`[x1,y1,x2,y2,score]`，坐标与置信度均为0–1000整数，对应完整原图；空数组表示本帧未检出。头中的frame_seq必须等于`X-Seq`，缺失或非法元数据只隐藏框，不丢弃JPEG。

App按FIT_CENTER缩放及黑边位置叠加绿色角框。断线、状态过期、开关待确认、关闭、epoch不匹配或本地画面停更1.5秒即隐藏，不用插值制造检测。录制仍保存原JPEG，框以`face_detection`事件保存`boot_id/timestamp_us/frame_seq/epoch/boxes/inference_us`；当前回放不叠框。本轮未接入人形检测、身份识别或自动追踪。实机状态以最新验证记录为准。

## 历史协议状态

状态：2026-11-01，协议版本仍为 1。最终 `collector-0.4.3` 已只写开发机应用分区并独立校验，Android `0.4.3/code13` 已安装到原观察台模拟器；新增磁场、光照与接近快照及可选舵机缓存诊断。最终 63.597 秒 App 会话已封存并通过记录校验，三组环境快照全部有效，录制窗口内 LTR553 无新增 I²C 错误；开机累计仍有一次超时，UART 帧错误也仍存在。前两候选证据与最终固件分开，不表示舵机反馈根因已修复。真实 Android 手机跨 IP 迁移仍未实测，不能用模拟器固定转发地址的结果替代。产物、媒体连续性与反馈有效率见[验证记录](collector-validation.md)最新段。

0.3.9 中心开关屏及既有摘要/日志/表情继续保留；本轮未重新进行物理按键或屏幕动画验收。所有数值含义均以实际能力声明为准。

## 连接

机器人提供 HTTP 8080 控制/快照端口与 HTTP 8081 音频端口。数据与控制端点要求 `Authorization: Bearer <token>`；0.4.0 唯一新增的公开 HTTP 端点是下面的绑定证明，不返回凭据或采集数据。token 是每台设备生成并存于独立 collector NVS 的 128 位随机十六进制值，屏幕/本地串口用于首次配对。示例不含真实值。该令牌不提供传输加密，首版限可信局域网。

`GET /v1/info` 返回 `protocol_version`、`device_id`、本次启动唯一的 `boot_id`、`firmware`、`capabilities`、`audio_port`，以及 `audio`（sample_rate、channels、bits、channel_roles）和 `camera`。采集端必须在连接、重连时获取 info；boot_id 变化时分段，不能延续旧序号。

### 0.4.0 已绑定设备自动连接与发现

观察台在应用私有偏好中保留已验证的 `device_id`、token 和最近可用地址，应用备份保持禁用；挑战通过且鉴权 info 返回相同 ID 后才保存，首次持已有凭据连接时可确认并保存该 ID。再次进入前台自动连接，用户不必重复输入配对码。IP 地址是可更新的路线，不能代替设备身份；应用包中不硬编码开发设备凭据，也不通过导出的 Activity Intent 覆盖已绑定地址或 token。手动暂停只作用于本次前台，离开再返回时恢复自动连接。退出前台仍结束前台采集、监听、发现和录制，保存绑定不等于获得后台采集能力。

这不是首次匿名配对入口。新安装且尚无凭据的客户端仍需通过已有本地授权取得 token；本轮开发设备可迁移其已有私有凭据。机器人不通过 UDP、公开 HTTP 或广播返回 token，也不因自动连接而取消原数据、控制和播放端点的鉴权。

`/v1/info` 声明 `capabilities.bound_discovery="supported"`，并提供 `discovery={"udp_port":8082,"endpoint":"/v1/discovery","requires_existing_binding":true}`。客户端对保存地址和新候选地址先发无 Authorization 的请求：

```text
GET /v1/discovery?nonce=<32个小写十六进制字符>
```

nonce 由客户端为本次挑战随机生成 16 字节后编码；查询仅接受 `nonce=` 加 32 个字符，不附其他参数。成功 JSON 为：

```json
{"protocol":1,"device_id":"example-device","nonce":"0123456789abcdef0123456789abcdef","proof":"<64个小写十六进制字符>","port":8080}
```

示例 nonce 仅演示格式，不可在运行时复用。proof 的精确定义是 `hex_lower(HMAC-SHA256(key, message))`：key 为 token **32 个十六进制字符自身的 UTF-8 字节**，不是解码后的 16 字节；message 为以下拼接的 UTF-8 字节，两个换行均为单字节 LF，末尾没有换行：

```text
"chan-discovery-v1\n" + device_id + "\n" + nonce
```

客户端检查协议值 1、当前挑战 nonce、已绑定设备 ID 和 64 字符 proof，并以固定时间比较证明。全部通过后才向该端点发送 Bearer 请求。`port` 不在证明消息内，因此只接受协议固定值 8080，不能让响应任意指定带凭据请求的端口。证明用于确认对方持有已有配对凭据，不提供传输加密，也不解决可信局域网之外的主动转发攻击。

保存地址不可达时，客户端向本地 IPv4 接口广播地址及 `255.255.255.255:8082` 发送最多 512 字节的 UDP JSON：

```json
{"protocol":1,"device_id":"example-device","nonce":"0123456789abcdef0123456789abcdef"}
```

机器人仅处理协议和绑定 ID 匹配、nonce 格式有效的请求；至多每 200 ms 回复一次，单播回数据报来源地址和端口。回复结构及 proof 与 HTTP 相同，没有 token。客户端验证后使用 **UDP 来源地址** 加固定 8080 作为候选，再进行一次新的 HTTP nonce 挑战；不信任数据报中额外声称的 host，也不向不匹配设备尝试凭据。

观察台离线时约每 2 秒发起一轮有界发现。换到同一已绑定设备的新地址时保留当前录制器，记录 `connection_route_changed` 事件并隔离旧连接的回调；设备重启仍按 `boot_id` 分段。更换路线不补造中断期间的媒体，也不把重连当作自动控制请求。

发现使用现有 lwIP UDP，不新增 mDNS 组件、依赖或工具链版本。广播可能被接入点或模拟器网络过滤，失败时保留已保存地址的重试和“重新寻找机器人”入口。现有开发模拟器的本机 TCP 转发只覆盖 8080/8081，不因此具备 UDP 广播发现能力；模拟器转发成功不能冒充真手机地址变化发现已验收。

0.4.0 的 lwIP socket 总额设为 16：两个 HTTP server 内部各占 3 个，控制端与音频端允许的客户端数分别为 3 和 2，另有 1 个 UDP 发现 socket；声明容量合计至少需要 12。构建断言检查最低总额。若沿用旧的 10，App 三路接收时会耗尽总额；HTTP server 尚有会话槽，LRU 不会先回收，导致额外连接被重置。

## 传感器

`GET /v1/telemetry` 返回 JSON：

```json
{
  "boot_id": "example-boot",
  "seq": 1,
  "timestamp_us": 1000000,
  "imu": {"valid": true, "timestamp_us": 990000, "accel": [0,0,9.80665], "gyro": [0,0,0], "accel_unit": "m/s^2", "gyro_unit": "deg/s"},
  "head_touch": {"valid": true, "timestamp_us": 995000, "values": [0,0,0]},
  "touch": {"valid": true, "count": 0, "x": 0, "y": 0},
  "power": {"valid": true, "battery_percent": 80, "charging": true},
  "health": {"heap_free": 100000, "psram_free": 4000000}
}
```

以上为格式示例，不是实测数据。单位以字段为准；无效或未支持数据不得伪造为 0/满电。顶层时间为快照时间，传感器时间为最近一次成功采样时间。客户端可约 10 Hz 轮询，按 seq 去重，记录原 JSON 和接收时间。

### 0.4.3 磁场、环境光与接近

本节为已部署的 0.4.3 定义。`/v1/info` 将 `magnetometer`、`light`、`proximity` 声明为 `supported`，表示固件有采集路径；每组的 `available` 才表示本次启动实际读到预期芯片 ID 并完成配置。BMM150 预期 ID 为 `0x32`，LTR553 的 part / manufacturer ID 为 `0x92` / `0x05`。识别或配置失败不会中止摄像头、音频、六轴或遥控，也不以零值冒充测量。

`/v1/telemetry` 顶层增加同名三组，均包含：

| 公共字段 | 含义 |
| --- | --- |
| `available` / `valid` | 已检测配置成功 / 当前测量可用；可用还须未饱和、未溢出、未过期 |
| `seq` | 本组完整读到的新测量计数，uint32；从 0 开始，按 `boot_id` 分段，与顶层 IMU 序号独立 |
| `timestamp_us` | 新测量读取完成的开机单调微秒；尚无样本为 0，不是硬件转换起始时刻或 HTTP 序列化时刻 |
| `error` | 字符串：`none`、`not_initialized`、`chip_id`、`i2c`、`config`、`trim`、`invalid`、`overflow`、`saturated` 或 `stale` |
| `unit` | 磁场 `uT`、光照 `lx`、接近 `count` |

无新数据就绪标志时不递增序号、不更新时间戳；此前有效值可以保留至满 1,000 ms 后失效。I²C 失败或硬件无效状态立即令 `valid=false`，保留具体错误和此前原始样本；只有重新取得新测量才恢复。尚无测量时为 `not_initialized`，不制造序号 1 的零样本。即使读数完全相同，只要芯片标为新测量，也会递增序号。新三组是最新快照，未加入 IMU 批量历史；轮询遗漏的环境样本不补造。

| 分组 | 专属字段与空值规则 |
| --- | --- |
| `magnetometer` | `field_ut` 是 XYZ 三元素有限数数组，仅 `valid=true` 时提供，否则整个字段为 null；`raw` 为带符号 XYZ 原始计数，`rhall` 为 Hall 原始计数，未读过样本时两者为 null；`overflow` 为布尔值。X/Y 的 −4096、Z 的 −16384 为溢出哨兵，不输出工程向量 |
| `light` | `lux` 仅有效时为有限非负数，否则 null；`raw_ch0` / `raw_ch1` 为 0…65535 原始通道计数，未读样本时 null；`gain` 为测量状态报告的增益倍数，无样本或保留编码时 null；`integration_ms=100`；任一通道满量程时 `saturated=true`、lux 无效 |
| `proximity` | `raw` 为 0…2047 计数，未读样本时 null；无效后仍保留读到的 raw，须结合 valid 使用。`saturated` 是已启用的芯片强环境光饱和指示，不是厘米或距离越界判断；raw 为 2047 本身仍是计数上限 |

BMM150 在 BMI270 的 AUX 总线上，启动时读出工厂 trim，配置 10 Hz 正常模式；BMI270 以 25 Hz 自动读取 8 字节。现有 50 Hz 六轴读取已包含该数据，不增加运行时 I²C 事务；只有 BMI270 AUX 和 BMM150 本身均声明新数据才采纳。磁场使用 Bosch 工厂 trim 与 RHALL 补偿为 µT，不将原始计数缩放假称校准。坐标仍是头部芯片坐标；没有整机硬铁／软铁、安装坐标或舵机磁场干扰校准，不发布航向或指南针。

LTR553 使用现有板级 I²C 总线的独立低优先级任务，目标 10 Hz，当前运行时每次总线调用超时参数为 50 ms。正常按 100 ms 周期轮询；遇到迟到周期至少阻塞 1 tick 并重新排期，不连续追赶旧周期。每轮最多检查状态并各读一次 ALS / PS，不循环重试，也不把光学读取加入 50 Hz IMU 任务。共享总线异常仍可能影响并发，不能仅由任务独立就保证零影响。启动配置为 ALS 增益 1×、积分及重复周期 100 ms；接近增益 16×、LED 峰值 20 mA、4 个脉冲、重复周期 100 ms，启用环境光饱和指示。两路分别检查新数据与有效性，ALS 无效不抹除独立有效的接近读数。

初始化阶段每次总线调用的超时参数为 50 ms；仅 I²C 错误允许至多 3 次初始化尝试，尝试间等待 100 ms 并让出任务调度。错误 ID 和配置回读不匹配立即停止，不通过盲目重试绕过识别或校验。每次都重新读 ID，先令 ALS / PS 待机，再重写及回读配置，成功后才启用测量，不需要硬复位或重新建立板级总线。失败后保留曾成功读出的 ID，但 `available=false`；全部配置回读完成也只表示硬件可用，首个新测量前仍 `valid=false/seq=0`。固定设备的总线注册保持至本次启动结束，不与其他设备的活动传输并发删除。尝试用尽后任务退出，不无限重试或持续刷日志。这是有界尝试及单次 API 超时定义，不是硬实时的总启动截止。

光照依照 Zephyr 的 LTR553 实现采用通道比值分段系数、增益和积分时间换算；红外占比超出该模型时标无效，不伪造为黑暗。未做 CoreS3 盖板透光补偿或照度计对照，显示 lux 不能视为精度验收。接近仅为反射光原始强度，不能换算或宣称距离。参考：[M5Stack 提供的 LTR553 手册](https://m5stack.oss-cn-shenzhen.aliyuncs.com/resource/docs/datasheet/core/K128%20CoreS3/LTR-553ALS-WA.PDF)、[Zephyr LTR553 换算实现](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/sensor/liteon/ltr55x/ltr55x.c)、[Bosch BMM150 SensorAPI](https://github.com/boschsensortec/BMM150_SensorAPI)。

`/v1/info` 的三组同时给出 `sensor`、`source`、`status`、`target_hz=10`、`stale_after_ms=1000`、`chip_id`、`timestamp_semantics="new_measurement_read_completion"`；LTR553 另有 `manufacturer_id`。尚未成功读取的 ID 为 null。磁场另有 `aux_poll_hz=25`、坐标与补偿说明、`heading_supported=false`；光照说明换算来源及无盖板校准；接近声明 `distance_supported=false` 与光源配置。`status` 是生成 info 时的错误快照，不是永远不变的能力值。

`info.light.initialization` 是两路共用的 LTR553 初始化诊断，只记录启动阶段，不随正常轮询更新：

| 字段 | 含义 |
| --- | --- |
| `attempts` / `max_attempts` | 实际尝试数 / 上限 3；尚未进入初始化时实际数为 0 |
| `transaction_timeout_ms` / `retry_backoff_ms` | 初始化 API 超时参数 50 / 两次尝试之间的调度等待 100 |
| `last_i2c_error` | 最近一次初始化读写失败的 ESP 错误码；没有记录到失败时为 0，不能单独据此断定配置已成功 |
| `last_i2c_register` / `last_i2c_operation` | 该失败访问的寄存器十进制地址及 `read` / `write`；没有记录到失败时均为 null |

若后续尝试成功，上述最近失败仍保留，不能将它误读为当前采样故障；当前状态看 `available/valid/error`。

`light.runtime` 在 info 和 telemetry 中均提供，与 `initialization` 分开，只计初始化结束后的 API 调用。`scope="LTR553_ALS_PS"` 表明它涵盖两路共用的 LTR553 访问，不是光照无效次数；HTTP 只复制缓存，不额外查询传感器：

| 字段 | 含义 |
| --- | --- |
| `transaction_timeout_ms` | 50，单次 IDF API 等待参数，非整个轮询的绝对截止 |
| `transactions` / `errors` | 本次启动运行期 API 调用数／非成功返回数，uint32；同一失败被 HTTP 重复观察不会再次计数 |
| `last_i2c_error` | 最近失败的 ESP 错误码；尚无失败时为 0 |
| `last_i2c_register` / `last_i2c_operation` | 最近失败寄存器地址及 `read` / `write`；尚无失败时均为 null |
| `last_error_timestamp_us` | 最近失败 API 返回时的设备单调时间；尚无失败时为 0 |

成功恢复后保留最近失败及其原时间，不能将旧错误误认作当前失效。当前采样仍看各组 `valid/error`；I²C 失败立即无效且不推进采样序号或时间，已有 raw 保留。初始化错误不计入运行计数。

历史候选：首诊断候选 BMM150 观察窗口约 10.07 Hz、全部有效；LTR553 识别后配置报 `i2c`、序号保持 0。第二候选在 10 ms 运行等待下仍有 I²C 失败；用户完成照光／靠近试验，178.629 秒封存记录中 ALS 从 raw `(0,1)` 的无效状态出现有效照度 0.8296–21.4982 lx，接近有效 raw 达 160，增益始终 1×。这些数值说明响应可见，不证明照度精度或距离。

最终固件的启动短窗三组各 36/36 快照有效；LTR553 运行计数从 108 次／0 错误至 1,040 次／1 错误，失败为开机 19.250471 秒读寄存器 `0x8d` 的 `ESP_ERR_TIMEOUT`（263）。随后最终 App 记录 63.597 秒、单启动／单分段、255 条遥测：三组均 255/255 有效，无饱和、溢出或字段约束违规。按各传感器自身序号推进估算磁场／光照／接近读取频率为 10.029／9.150／8.701 Hz；实际保存的不重复快照为 249／245／242 个，HTTP 快照少于采样次数，不能宣称保存了所有转换或把未观察序号当网络丢包。光照 7.34–15.897 lx、1× 增益，接近 raw 0–9；这次没有重复人工照光／靠近验收。

最终录制首末 LTR553 运行事务为 7,219→8,956（新增 1,737 次），错误为 1→1（新增 0 次）；保留的最后失败仍是上述启动旧错误。记录结束后的另一次快照为 9,306 次／1 错误，追加 350 次也未见新增错误。该短窗支持当前采集路径，不证明永久无错、光学精度或长期并发稳定。

Android 0.4.3 底栏显示 lx 与接近 raw，机器人设置显示磁场 XYZ µT；接收和回放仍原样保存／使用 telemetry JSON，不新增伪造的距离或航向列。UI 独立检查采样年龄与接收中断：1 秒失效，允许异步环境采样时间比顶层 IMU 快照最多新 250 ms。旧固件／旧记录缺整组时显示“未提供”；无效或过期时显示缺失，不把保留的 raw 当有效工程值。

### 0.3 批量运动记录

`GET /v1/telemetry?imu_after=N&remote_after=M` 中 N/M 是同一 boot 最近接受的 IMU / 遥控输入序号。首次传 0，重启必须清零。不带游标参数时只返回当前快照，不序列化历史数组，避免旧客户端每次重复下载整段缓存。显式传 `imu_after=0` / `remote_after=0` 才请求初始历史，后续传已收序号取增量；新客户端也兼容没有批量能力的旧固件。

- `imu_samples` 为最近 100 个读数中序号大于 N 的部分，每个含 `seq`、`timestamp_us`、`valid`、`accel` 和 `gyro`。无效样本不附向量。时间为读取完成的设备单调时间，不是 IMU 硬件中断时间。
- `imu_history_lost` 表示从已知游标到环形缓存最早样本之间丢失的数量；初始游标 0 不把启动之前数据计为丢失。图像、音频与传感器仍按各自采样时间记录。
- 采集读取目标 50 Hz，BMI270 ODR 100 Hz，加速度 ±8g、角速度 ±2000°/s。实际采样率须从导出时间差计算。没有声称 100 Hz 全量 FIFO。
- `accel` 单位 m/s²，包含重力，坐标为运动头部的传感器轴。安装到肩部/身体的坐标变换未标定；合加速度不是去重力后的线加速度。
- Android 除保留原 JSONL，还按样本序号去重写 `motion.csv`；可供后处理分析。无效向量留空，不写成 0。

### 0.3 官方遥控与舵机反馈

使用已固定上游的 `esp-now` 组件 DATA 数据类型，与官方遥控器一致；不是把裸 8 字节直接交给 `esp_now_register_recv_cb`。不重新初始化 Wi-Fi、不强制跳频道，接收频道跟随采集网络。

| 字节 | 官方含义 |
| --- | --- |
| 0 | 目标 ID；0 广播，当前接收 ID 1 |
| 1–2 | 有符号小端水平角 int16，名义映射 −1280…1280，单位 0.1° |
| 3–4 | 有符号小端俯仰角 int16，名义映射 0…900，单位 0.1° |
| 5–6 | 有符号小端速度，0…1000，无量纲 |
| 7 | 辅助按钮，0/1；只记录，不驱动 GPIO2 附件 |

官方映射可能外推至名义角度范围之外，实机已收到俯仰 95°、97.3°及水平 148.8°。0.3.1 解码保留完整 int16 目标，仍检查 8 字节长度、辅助值 0/1、速度 0…1000 与目标 ID；执行前单独限幅，不污染原始输入记录。

遥控器摇杆模式和姿态模式都发送上述映射后的目标，没有原始摇杆、遥控器 IMU、模式标志、发送序号或心跳。因此不能还原这些原始量、统计空口丢包，或根据静默时间判断遥控器断线。`seq` / `rx_packets` 是机器人实际接受的输入计数，组件负责同一 ESP-NOW 帧重发去重。

遥测 `remote` 包含接收器是否就绪、Wi-Fi 频道、接收 ID、拒绝/其他目标计数、最新 `last`、64 条环形历史中的 `packets` 和 `history_lost`。每包保存机器人接收时间、源 MAC、RSSI、频道和解码目标。Android 写 `remote.csv`，作为输入数据；`remote.motor` 和 `servos.csv` 另存已应用目标及舵机实际反馈。读回失败时 `valid=false`，不使用驱动回退角度冒充实测。

0.3.1 每轴位置回传增加 `transport_error`（0 正常、1 无回复、2 校验失败、3 ID 不符、4 长度不符）、`servo_status`、`position_raw`。仅位置事务正常、舵机状态为 0、原始位置在 0…1000 时 `valid=true` 并给出角度；负载/电流有各自的 `load_valid` / `current_valid`，可选读回失败不否定有效位置。拒绝包的诊断 `last_rejected` 保留长度、时间和可解出的字段，不作为已接受的输入。

#### 0.4.3 可选舵机事务缓存诊断

鉴权 `GET /v1/telemetry?servo_diag=1` 才附加 `remote.motor.yaw.diagnostics` 和 `remote.motor.pitch.diagnostics`，可与 IMU / 遥控游标共用。该参数只接受数值 0 或 1；缺省或 0 不输出此较大载荷，非法值返回 400。普通 App 不请求它。**诊断缓存本身不额外轮询，HTTP 不触发舵机查询、动作或重试**；运动任务另有下述每次启动、每轴最多一次的死区寄存器只读探针，不依赖是否请求诊断。

每轴包含 `generation`（已观察事务计数）、`snapshot_timestamp_us`（运动任务复制缓存的时刻）、`source="cached_existing_uart_transactions"`、`trajectory_complete`（软件轨迹计算是否结束），以及 `all`、`position_read`、`moving_read`、`torque_read`、`torque_disable` 五组累计计数。各组含 `attempts/ok/no_reply/bad_checksum/wrong_id/bad_length/servo_error/tx_error`；发送错误可与接收错误同时发生，不能把各错误计数相加当作互斥的事务总数。当前 `torque_read` 按寄存器 40 的单字节读取，不能将旧候选的双字节读回与其混作同一实现。

`last`、`last_error`、`last_position_write`、`last_torque_disable` 分别保留最近事务、最近失败事务、最近位置写入和最近卸力命令；`valid=false` 时没有其余内容。有效 trace 包含事务开始的 `timestamp_us`、`duration_us`、`id/instruction/address/requested_bytes`、最多 6 字节的命令参数 `tx_data_prefix`、`transport_error`、`status_valid/servo_status`、`raw_valid/raw_value`、`rx_bytes`、最多 24 字节 `rx_prefix`、`discarded_before`，以及发送 API 的 `tx_bytes/tx_calls/tx_submit_span_us/tx_max_gap_us/tx_short_writes/tx_wait_error`。`tx_data_prefix` 是交给 API 的参数前缀，不是完整串口报文；无参数时为空数组。无效状态字或 raw 值为 null。`tx_submit_span_us` 为首个 write 入口到最后一个 write 返回的跨度，不含后续等待和接收；`tx_max_gap_us` 为相邻 write 返回到下次入口的最大间隔。这些是 **API 提交计时，不是 UART 线路波形或线上字节间隔**。

`uart_events` 是两轴共用 UART 总线的事件缓存，`scope="shared_bus_do_not_sum_axes"`；两轴副本不能相加，也不能把事件归因于当时查询的某一轴。`enabled` 表示事件队列已启用，当前队列容量 `queue_capacity=32`。`frame_error_interrupt_enabled` / `frame_error_interrupt_error` 单独报告 FRAME_ERR 中断启用结果及 ESP 错误码，不能仅由事件队列存在就断言该中断在观察。`events_drained` 统计已取出的全部事件，错误子类为 `frame_errors/parity_errors/fifo_overflows/buffer_full/breaks`；`queue_full_observations` 仅统计读取队列前发现已满的次数，不是丢失事件数。IDF 可在队列满时丢弃事件，因此 `coverage="best_effort_queue_may_drop"`、`counts_complete=false`，计数始终只作下界；未见满队列或错误计数为零都不能证明没有线路错误。`last_poll_timestamp_us` 是取出事件的时刻，不是硬件事件发生时间。取队列使用零等待且每次最多处理 32 项，不额外读写或清空 UART 数据。

`deadband_configuration` 保存一次只读探针结果。运动任务在开机至少 5 秒、该轴本轮取得有效位置且软件轨迹已结束后，从地址 26 连读 2 字节，分别为 CW / CCW 死区配置；每次启动、每轴最多尝试一次，失败也不重试，不解锁、不写 EEPROM、不改变死区或动作。字段为：

| 字段 | 含义 |
| --- | --- |
| `source` | `one_time_read_only_register_probe` |
| `attempted` / `valid` | 是否已尝试；收到完整两字节、传输正常且舵机状态为 0 才有效 |
| `timestamp_us` | 探针事务开始的设备单调时间；尚未尝试时为 0 |
| `transport_error` / `status_valid` / `servo_status` | 传输状态；仅已尝试且传输正常时提供状态字，否则为 null |
| `cw_register` / `ccw_register` | 26 / 27 |
| `cw_raw` / `ccw_raw` | 各自独立的配置字节；`valid=false` 时均为 null，不拼成一个 uint16 |
| `unit` / `degrees_per_step` | `step` / 0.3125，为配置单位换算，不是机械死区实测 |

快照时刻更新不代表最后事务或最后错误刚刚发生；`last_error` 可保留很久，须看其自身时间和计数增量。`trajectory_complete` 不证明实测位置到达目标。`torque_disable` 中成功 ACK 仅说明命令得到正常应答，`torque_disable_semantics="acknowledgement_not_measured_torque"`，不证明机械卸力或测得扭矩为零。最终固件启动短窗已确认 FRAME_ERR 中断启用，`frame_errors` 从 95 增至 1,329；新增诊断与单字节读取修正没有消除既有异常，不能称 UART 零错误或旧反馈根因已修复。

0.3.6 在 info/telemetry 的 `remote` 增加 `yaw_control_enabled=true`、`pitch_control_enabled=false`、`pitch_input_role="reserved"`。两种官方遥控模式均只执行水平轴；`last.pitch_deg`、历史包和 `remote.csv` 中的上下输入仍原样保存，不能作为已执行的俯仰目标。`motor.pitch_target_deg=null`，导出的 `servos.csv` 对应单元格留空；`yaw_target_deg` / `applied_speed` 仍表示遥控实际应用的水平目标/速度。

启动俯仰沿用 0.3.5 的单次 13° 目标（5–85° 行程的 10%），通过 `startup_pitch_target_deg` 和 `startup_pose_state` 单独表达，实际角度仍须看 `motor.pitch.valid` / `angle_deg`。从 0.3.6 起左右遥控不取消或重启这条启动轨迹；手动停止会取消，重新启用不会再次归位。最多等待有效起始位置 5 秒，无有效回传时跳过；轨迹结束不等于有效实测到位。Android 0.3.6 将蓝点标为“输入”，明确提示“仅左右转动 · 上下输入保留”，兼容旧固件的双轴目标显示。

### 0.3.7 表情与中心按压扩展

上下映射值改为 `pitch_input_role="home_reaction"`，仍不直接驱动俯仰。向上跨过 60° 触发爱心、向下跨过 30° 触发拇指；各显示 500 ms，保持方向不重复，回中使用 52° / 38° 回差。收到的上下原值继续记录。`remote.gesture` 记录反应类型、输入序号、接收时间及持续时间；它表示事件，不表示当前一定在首页显示。

原厂 8 字节中的 `auxiliary` 是 B 键切换状态，不是摇杆中心按下电平。实机中心按压没有改变该字段，不能用 B 键替代中心按压验收。Mini JoyC 中心键使用地址 `0x54` 的 `0x30` 寄存器，按下为 0；扩展发送端增加消抖和独立事件，原 B 键字段保留为数据。

中心扩展保持前 8 字节原格式，增加两个小端 uint32，总长 **16 字节**：8–11 为非零 `sender_boot_nonce`，12–15 为 `center_press_seq`。遥控器已部署并实测该协议；中心按住不重复计数，模式切换时按住不会产生新事件。`center_press_supported=true` 表示协议可读，最近实际包的 `sender_center_supported` / `heartbeat_supported` 表示发送端支持，不能将协议能力解释成动作绑定。

用户在实测后决定放弃点头效果，此前 0.3.8 中心按压 **仅记录、未绑定动作**：info/telemetry 返回 `nod_trigger="none"`、`center_input_role="record_only"`。中心计数仍进入 `last`、历史及 `remote.csv`。0.3.9 增加下节的开关屏绑定，保持点头禁用。旧 B 键仍只记录，连续左右控制、上下 500 ms 表情与单次启动俯仰保持。

为兼容先前观察台，保留 `motor.nod` 对象：`state="disabled"`，请求序号/阶段/完成计数为 0，速度/段时间/范围/目标为 null。点头调度和专属下沿恢复已移除；`motor.pitch_target_deg` 继续为 null。之前的 250/700 ms 与 450/400 ms 点头试验只属于验证历史，不是当前行为。

发送端每约 200 ms 发送心跳。`remote.csv` 的 schema 2 保存中心协议、启动 nonce 与按压序号，旧包的中心计数留空；心跳复用原死区过滤后的目标，不因细小噪声反复唤醒水平舵机。观察台根据 `center_input_role` 显示用途，不能仅凭已升级遥控器判断动作绑定。

参考：[M5Stack Mini JoyC 文档](https://docs.m5stack.com/en/hat/Hat_Mini_JoyC)、[官方读取实现](https://github.com/m5stack/M5Hat-Mini-JoyC/blob/main/src/M5HatMiniJoyC.cpp)。

### 0.3.9 中心开关屏（已部署，单轮实机确认）

沿用已部署遥控器的 16 字节协议，无需重刷遥控器。一次中心按压关闭显示，再次按压恢复原页面和亮度。关屏时暂停该屏幕重绘，触摸既不唤醒也不导航；开屏前后跨越的按住手势被丢弃，须释放后再操作。关屏收到的上下表情不在开屏后补播。机器人重启默认亮屏，开关状态不写 NVS。

`/v1/info` 声明 `capabilities.display_toggle="supported"`、`remote.center_input_role="display_toggle"`、`remote.nod_trigger="none"`。`display` 能力对象包含 `toggle_trigger="joystick_center_v1"`、`default_enabled=true`、`persistent=false`、`touch_wakes=false`、`requires_motor_enabled=false`。

接收器对首次中心包、来源 MAC 或 `sender_boot_nonce` 改变、中心包间隔达到 3 秒后的首包只建立基线，保留已有显示请求，不执行切换。旧 8 字节包没有中心事件，之后首次扩展包重新建立基线。相同或倒退计数忽略；合法前进差量采用 uint32 序号运算，支持回绕，按差量奇偶累计开关请求，避免 UI 轮询之间的多次按压丢失。按住、释放和重复心跳不会重复切换。

`/v1/telemetry` 新增以下状态；其中序号与时间均限本次机器人启动：

| 字段 | 含义 |
| --- | --- |
| `remote.gesture.display_requested` | 接收事件计算出的亮屏请求，默认 true |
| `remote.gesture.center_event_seq` | 基线后接纳的中心按压累计数，uint32；序号跳跃保留差量 |
| `remote.gesture.center_event_timestamp_us` | 最近接纳中心事件的机器人接收时间；尚无事件为 0 |
| `display.enabled` | LVGL 任务应用后的软件显示开关状态 |
| `display.applied_center_event_seq` | 显示任务最近应用请求对应的事件累计数 |
| `display.changed_us` | 软件显示状态最近改变的单调时间；启动默认状态为 0 |
| `display.state_semantics` | `software_visibility_and_backlight_command` |

`display.enabled` 表示软件可见性和背光命令，不是物理亮度测量。中心接收和显示任务不依赖 `motor.requested`、`motor.enabled`、舵机反馈或观察台连接；关闭显示不停止摄像头、麦克风、六轴采集、日志接收或左右遥控。`motor.nod` 仍为 disabled，连续俯仰目标仍为 null。

本轮仅实测一轮关→开，软件关闭状态持续 1.978926 秒；保守 1.782 秒关屏窗口内持续收到 12 JPEG、89 连续音频块、89 连续 IMU 样本和 9 遥控包。此证据支持本次有限窗口的采集独立性，不替代长按、多轮或长时间运行验收。

### 0.4.0 持久俯仰设置

`/v1/info` 声明 `capabilities.pitch_setting="supported"`，`pitch_setting.endpoint="/v1/settings/pitch"`、`min_deg=5`、`max_deg=85`、`step_deg=0.1`。该设置是用户明确提交的姿态目标，与遥控器上下表情输入分开；水平遥控和中心开关屏继续独立工作，`remote.motor.pitch_target_deg` 仍为 null，不能用它表示本设置或实际角度。

鉴权 `POST /v1/settings/pitch` 接受 1–128 字节 JSON 正文，唯一字段为数值 `angle_deg`：

```json
{"angle_deg":13.0}
```

范围为 5–85°，包含端点，步进 0.1°；非有限数、字符串、越界或不能表示为该步进的值被拒绝，不静默舍入。正文接收总截止为 3 秒，实际退出还受当前 socket 最长 3 秒的 recv 等待和错误响应发送影响；截止返回 408，未收完整或超限的请求关闭连接，不继续等待剩余正文。请求完整接收并通过校验后，只有控制请求开启且运动初始化完成才接受；否则返回 409。非法请求为 400，存储等失败为 503，失败不排队本次动作。成功返回 202：

```json
{"accepted":true,"persisted":true,"request_seq":1,"pitch_setting":{"configured_deg":13.0,"persisted":true,"request_seq":1,"request_source":"user","requested_deg":13.0,"state":"queued"}}
```

该示例省略其余状态字段；实际响应中的任务可能已从 queued 推进。202 表示已成功保存并提交请求，不表示机械到位。设置使用 collector NVS 的独立 `pitch_deg10` 键，按十分之一度保存；写入和 commit 成功后才发布新配置、排队动作，未修改原舵机校准。重复提交相同已保存角度可重新产生一次明确请求，但无需重复写入同值。存储失败不声称成功，也不承诺底层事务能够回滚。

机器人重启按已保存角度尝试一次启动姿态；没有有效保存值时默认 13°。重新连接 App 或重新启用控制不会重放旧设置。用户停止控制会取消当前和先前排队请求，配置本身仍保留；之后必须再次明确提交，或下次机器人启动，才产生新的姿态尝试。App 应按设备 ID 缓存设置，以机器人确认的配置为准，不能在自动重连时把缓存角度重新当成动作命令。

`remote.motor.startup_pitch_target_deg` 固定表示本次启动实际加载的目标，后续用户设置不会改写这条启动历史；`pitch_setting.configured_deg` 才是当前已确认配置及下次启动使用的目标。启动尝试被明确设置替代时，`startup_pose_state="superseded"`。

`/v1/info`、`/v1/telemetry` 和成功 POST 响应包含 `pitch_setting`：

| 字段 | 含义 |
| --- | --- |
| `configured_deg` / `persisted` | 当前配置角度，以及它是否已成功读取或写入持久存储；默认值未保存时为 false |
| `request_seq` / `request_source` | 本次启动中的设置请求序号；启动尝试为 0 / `boot`，明确设置为递增序号 / `user` |
| `requested_deg` / `request_timestamp_us` | 当前请求的目标与设备单调提交时间；启动请求时间为 0 |
| `state` | 下表的任务状态，不是实际到位判断 |
| `feedback_valid` / `feedback_timestamp_us` | 最近俯仰位置反馈及其采集时间；有效位置还须在 1 秒以内 |
| `measured_deg` | 新鲜有效的实际位置，否则 null；不以目标补填 |
| `command_start_deg` | 成功启动轨迹时采用的命令起点，否则 null；可能与实测起点不同 |
| `storage_error` | 最近读取/保存配置的 ESP 错误码，0 表示该存储操作未报告错误 |
| `speed` / `min_deg` / `max_deg` | 固定动作速度 250 及设置范围 5–85°；速度不是角速度实测值 |
| `state_semantics` | `trajectory_state_not_measured_arrival` |

| `state` | 含义 |
| --- | --- |
| `queued` | 成功提交，等待运动任务接纳 |
| `waiting_feedback` | 等待可用起始反馈，最多 5 秒 |
| `moving` | 原厂平滑轨迹运行中 |
| `trajectory_complete` | 轨迹计算完成，不等于有效实测已到目标 |
| `feedback_unavailable` | 等待期限内未取得可用起始反馈 |
| `start_rejected` | 执行前再次检查未能启动 |
| `target_unavailable` | 目标超出当时舵机动态限位 |
| `trajectory_timeout` | 轨迹运行达到 15 秒截止，已取消 |
| `cancelled` | 被控制停止取消，不因重新启用恢复 |

每次请求使用新鲜实际反馈启动。仅允许下限以内最多 2°的有效反馈朝安全范围内部恢复：例如实测 3.7°保留为 3.7°，命令从 5°规划；无效反馈、离下限更远或超出允许方向的起点不伪造为有效位置。俯仰堵转处理、原厂轨迹和自动释放力矩继续使用，用户弃用的中心点头保持 disabled。App 已实际提交 15°并观察到有效反馈由 11.8°变到 13.4°，随后恢复保存为 13°，两个保存值均经重启读取验证。指令轨迹结束不等于机械精确到位，旧舵机回传异常仍未解决。

### 独立遥控控制状态

0.3.2 的 info/telemetry `remote.control_mode="standalone"`、`requires_observer=false`，兼容字段 `lease_ms=0`。`POST /v1/remote` 使用同一 Bearer 鉴权，JSON `{"enabled":true}` 开启接收执行，`false` 停止并释放力矩；不需要续约。`motor.requested` 是请求状态，`motor.enabled` 是执行任务的当前状态，UI 以机器人的返回值为准。Android 0.3.2 仅在用户明确点击开关时发送控制请求，连接、断线、重连、boot 更新均不自动停用独立接收器；对旧固件仍保留原 750 ms 续约。

启动默认准备底座 VM 和舵机，只读校准、关闭力矩、不归位；等待本次启动的新遥控输入。停止后再启用只处理启用以后收到的包。0.3.4 的 info/telemetry 新增 `remote.feedback_required_for_control=false`，`remote.motor.feedback_valid` 表示本次两轴位置是否同时有效。位置读回失败不修改 `motor.requested`，也不阻止用户重新启用；无效位置仍不提供角度，不作为实测值绘图或记录。此前 0.3.2 的失败锁停是采集与控制耦合错误，已撤掉。水平夹到 ±128°、俯仰夹到 5–85°；速度保留官方包的 0…1000（原厂摇杆与姿态模式均发 600），取消此前 400 上限。

0.3.4 观察任务仅额外读取位置，单次观察读回的超时为 2 ms，之后恢复驱动原超时；不额外读取负载和电流，`load_valid` / `current_valid` 为 false，旧字段保留兼容。原厂动作内部的俯仰堵转反馈事务仍保留，未更改其超时。驱动的校验和、ID、长度和原始位置范围检查均未绕过。

连续输入沿用原厂弹簧轨迹，不在每包重复同步实测角度打断速度；新控制会话首包才同步位置。取消 3 秒命令处理窗口，静默时完成剩余轨迹并保留原厂到位自动释放力矩。官方遥控器没有心跳，静默不表示掉线。原厂俯仰堵转保护保留；辅助按钮只记录，不控制 GPIO2 附件。旧 0.3.1 的三秒租约属于历史行为，不适用于独立模式。

依据：[固定官方遥控源代码](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/remote/code/main/joystick/joystick_handle.c)、[机器人接收实现](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/firmware/main/apps/app_espnow_ctrl/app_espnow_ctrl.cpp)、[ESP-IDF 5.5.4 ESP32-S3 ESP-NOW](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-reference/network/esp_now.html)、[M5Stack 运动角度说明](https://docs.m5stack.com/en/StackChan)。

## 0.3.8 单次扬声器播放

`POST /v1/playback` 使用同一 Bearer 鉴权，body 是无 WAV 文件头的 **24 kHz / 单声道 / 有符号 PCM16 小端**数据，建议 `Content-Type: application/octet-stream`。长度必须为正偶数，最多 384,000 字节（8 秒），同时只允许一个上传/播放任务；忙时返回 409，不排队。非法长度为 400，初始化或内存不足为 503，拒绝请求关闭连接，避免残余 body 阻塞主 HTTP 服务。

完整上传且成功回复 `202 {"accepted":true,"job_id":...,"bytes":...}` 后才尝试开启输出。202 不等于播放完成；读取 `/v1/telemetry` 的 `playback` 检查 `state`、`busy`、`submitted_bytes`、`error` 和 `cleanup_error`。上传总截止为 10 秒（实际退出还受当前 socket recv 超时上限影响），播放提交也有时限；截断上传不发声，不自动重播。

开机仍保持 AW88298 硬件复位，显式HTTP播放请求或新鲜遥控表情事件才建立临时输出对象。播放沿用现有 TX 24 kHz/16-bit/双槽配置，将单声道复制到两槽，不停止输入或重配共享 I²S；开始前在复位保持期间用零 PCM 覆盖整个 TX DMA 环并等待。默认音量80，仅写运行态，不持久化到 NVS。每块最多 480 帧，每轮至少让出一个 tick。

结束时补零 PCM、等待尾缓冲、静音和释放输出对象，再尝试恢复硬件复位；失败也走清理。`completed` 表示提交及清理成功，不替代用户听感确认。`health.speaker_enabled` / `playback.output_enabled` 表示软件输出会话状态，`speaker_hardware_reset_held` / `hardware_reset_held` 为复位寄存器读回结果；无法核对时为 null，不能当作已确认静音。任务记录不持久保存上传 PCM。

此接口与观察台的麦克风监听、录制文件回放不同。现场试听时可暂时关闭观察台声音回放，采集继续；不会把中心按压重新绑定为播放。官方试听片段和现场录音只保存在本机，不纳入源码发布。

## 图像

`GET /v1/frame` 返回 `image/jpeg`，响应头包含 `X-Boot-Id`、`X-Seq`、`X-Timestamp-Us`、`X-Width`、`X-Height`。无可用帧或最后成功采集超过 3 秒返回 503。客户端按 `/v1/info` 的 `camera.target_fps` 获取，Android 当前上限 10 Hz；0.1.0 目标 2 fps，0.2.0 目标 10 fps，均不等于保证的实收帧率。重复序号不重复记录；预览慢时只保留最新待显示画面。录制索引记录序号、采集时间、接收时间、尺寸及文件名。

这是连续请求 JPEG 帧的实时画面，尚无 multipart MJPEG 长连接、H.264 或 MP4 文件输出。`health.camera_processing_us`、`camera_jpeg_us`、`camera_jpeg_bytes` 是 0.2.0 新增的最近一次相机处理、JPEG 编码耗时（微秒）和 JPEG 长度，不是端到端延迟。

## 音频

8081 的 `GET /v1/audio` 返回 HTTP chunked 连续二进制；HTTP chunk 边界与音频包边界无关。已部署的 0.4.8 只保证一个音频消费者，忙时返回 503。准备中的 0.4.9（未刷入）允许两个独立游标消费者，共用原音频环；第三路返回 503，不增加采集任务。info 的 `audio_max_clients` 声明容量，health 保留 `audio_client` 布尔值并增加 `audio_clients` 数量。应用层每包包含 32 字节头和 PCM：

| 偏移 | 类型 | 内容 |
| --- | --- | --- |
| 0 | 4 bytes | ASCII `SCA1` |
| 4 | uint32 LE | 采集序号 |
| 8 | uint64 LE | 块首样本估算单调时间，微秒 |
| 16 | uint32 LE | 采样率 Hz |
| 20 | uint32 LE | 每通道帧数 |
| 24 | uint16 LE | 通道数 |
| 26 | uint16 LE | 位深，首版 16 |
| 28 | uint32 LE | payload 字节数 |
| 32 | bytes | 交错有符号 PCM16LE |

约定 `payload_bytes = frames × channels × 2`；客户端限制包长并完整读取，拒绝畸形和截断包。通常 24 kHz、480 帧一块，通道含义从 info 获取；现有 codec 的 mic/reference 不等于两个实体麦立体声。

序号由采集端递增，环形缓冲溢出或慢客户端会产生可检测 gap。块时间是首样本估算时间，非声学硬件同步。记录每块序号、设备时间、接收时间及 PCM 偏移；对缺失写入事件并在索引体现，不静默补齐后称无丢失。

## 会话边界和错误

重新连接或 boot_id 改变须生成分段/事件；设备与手机时钟通过同时记录单调设备时间和手机接收时间建立关联，不能用网络接收时间代替采样时间。视频丢帧、音频 gap、写盘队列溢出、传感器无效均显式记录。token 不写入导出 manifest 或普通调试日志。

## 0.4.6：方向、声音与首选网络

遥控包的`yaw_deg`继续记录发送端原值；执行目标使用`clamp(-yaw, -1280, 1280)`，先扩宽整数再取反，零点、速度上限和原厂运动链保持。`remote.yaw_input_to_target_sign=-1`声明此映射；`motor.yaw_target_deg`才是执行目标。上下仍不传给俯仰舵机。

`remote.gesture.reaction`为`heart/reject/none`，保持阈值、回中滞回及500ms显示时长。提示音为本地合成的24kHz单声道PCM，360ms，有渐入／渐出；拒绝音增大源电平并增加中高频。所有输出仍经单一播放槽、零PCM尾部、codec关闭和功放复位。连续表情最多保留最新一条等待音，超过750ms丢弃，不积累回放；HTTP播放期间不插入表情音。`playback.source`区分`upload/remote_heart/remote_reject/none`，`reaction_queued`记录等待次数，`reaction_dropped`记录忙碌或覆盖等丢弃；`volume=80`。

USB配置命令可附`"priority":true`。首选SSID和密码作为单个`collector/wifi_primary` JSON值提交；既有`wifi_ssid/wifi_password`及工厂SSID存储保留。启动顺序为首选、旧采集配置、去重后的工厂配置。同名SSID使用靠前的密码；每项最多3次、间隔10s，连接成功后停止轮询。下次断线从首选重新尝试，不为扫描首选强拆健康连接。`health`提供`wifi_profile_count`、`wifi_preferred_configured`和已关联时的`wifi_on_preferred`，不返回SSID或密码。热点频道变化时，ESP-NOW遥控器必须同步设置频道。

## 历史 0.4.9 情绪角落接口（0.4.10 起曾随固件上机，0.4.17 停用）

鉴权 `POST /v1/emotion` 只更新内存中的首页文字，不控制舵机、声音或持久设置。最多512字节JSON，接收有3秒期限；仅允许如下8个字段：

```json
{"boot_id":"CURRENT_BOOT_ID","session_id":"0123456789abcdef0123456789abcdef","seq":1,"observed_at_us":1234567,"emotion":"happy","stt_state":"listening","evidence":"text","inferred":true}
```

`boot_id` 必须匹配当前启动；`session_id` 为32位小写十六进制；非零 uint32 `seq` 在同一会话内严格递增。`emotion` 为 unknown/neutral/happy/sad/angry/anxious/surprised；`stt_state` 为 offline/connecting/listening/sleeping/error；`evidence` 为 none/text/vision/both。`inferred` 必须为 true，强调这是推测。

`observed_at_us` 使用相机/音频携带的机器人单调微秒，不能是Mac时钟或回包时刻。非 unknown 标签必须有来源与有效时间，拒绝未来时间、零时间或超过20秒的证据。Mac每2秒续租，8秒没有有效更新则显示未连接；重复心跳不延长原始证据20秒时效。不同session在旧租约有效时被拒绝。无效字段返回400，启动/顺序/时间/会话冲突返回409，成功返回 accepted 与当前情绪快照。

标签仅覆盖首页左下角；明确显示“推测：开心”等，不确定与语音待唤醒/连接中/断开分别显示。熄屏时不刷新，恢复屏幕时重取当前快照。该接口与双音频消费者只完成编译及主机测试，实机容量、界面并发和故障恢复尚待部署后验证。

## collector-0.4.23：显式 M12 识别与俯仰遥控

- `m12_recognition`、`remote_pitch` 能力为 `supported`。M12 `/v1/m12/view` 兼容布尔检测/待机；新增 `/v1/m12/mode` 接受唯一 `mode` 字段（standby/face_detector/face_recognition），与现有网页控制相同鉴权/boot核对，202只代表入队。
- M12 `frame_us`、`frame_recognition` 说明当前展示帧；`faces` 在识别帧增加 `known/name/match_prob`。`results/last_result_us` 继续表示检测，`recognitions/last_recognition_us` 表示识别。切换清展示帧；网页身份必须同时符合当前请求和新鲜度。
- `remote.pitch_control_enabled=true`、`pitch_input_role=servo_pitch`；`motor.pitch_owner` 为 remote 或 setting，前者时 `pitch_target_deg` 才表示已交给 Servo 的遥控目标。保存设置被实体接管时状态为 superseded，保存值不变。旧文档的 yaw-only/home_reaction 描述仅适用于此前版本。

- `last_recognition_report_us` 是识别程序报告时间；不计入 `recognitions`，不产生人脸/身份。识别收到本次启动报告后停止未应答重发，避免无脸静默导致不断重启。官方源码依据：[face_recognition.cpp](https://github.com/m5stack/UnitV2Framework/blob/main/main/face_recognition.cpp)。

- 俯仰映射：`pitch_mapping=center_v1_saved_neutral_legacy_absolute`；`pitch_neutral_input_deg=47.1`，`pitch_neutral_target_deg` 为最新保存仰角。16字节协议没有模式位，回中映射覆盖其摇杆/IMU输入；8字节旧协议仍绝对角。记录包中的 pitch_deg 保持原始映射值，不能与 motor.pitch_target_deg 混淆。

### /mouth liveness (collector-0.4.90, 2026-11-13)

The `/mouth` downlink is silent while hello is quiet. On 2026-11-13 a network blip made the ear close it (missed pongs) while hello kept reporting `connected` for an hour, so lines could not be spoken. Since 0.4.90 hello counts every frame it reads on `/mouth`, including the ear's WebSocket pings (every 20 s, `websockets` default); 45 s without any frame closes the link and reconnects with the usual back-off. Telemetry `mouth.silent_drops` counts these; `mouth.last_error` is `silent`. Logic: `collector/mouth_liveness.h`, host test `tests/mouth_liveness_test.cpp`.
