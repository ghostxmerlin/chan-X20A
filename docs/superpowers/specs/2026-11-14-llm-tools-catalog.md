# hello 的 LLM 工具目录：服务器方法和 hello 本体能力的梳理（设计）

状态：设计草案第 1 版，待主人审阅。只写文档，没有改代码。

日期：2026-11-14。作者：Claude（Opus 5.5），服务器侧。

- 主人的原话："梳理，服务器端，基于视觉，听觉，情景，纪要等我们创造出来的方法，以及，hello端侧可以具备的能力，如果抽象成mcp方法或者可以被llm调用到的方法，下一阶段，我们会让llm去调用。"
- 读的是 HEAD `8810632`（chat v1 复核之后）。另有两个代理正在改 `server/ear/ear_server.py`、`server/ear/mouth_link.py`、`collector/mouth_modes.h` 和对话阶段点，工作区里这几个文件已经有未提交的改动。所以下面这几个文件的行号以读的那一刻为准，以后可能偏几行。
- 没有写数据库，没有调用付费模型，没有连机器人。
- 标 **（未核实）** 的是推断或者还没实测的。
- 仓库是公开的。文中不放真实名字、地址和密钥。工具说明和例子里的人一律写"主人""某同事"。
- xiaozhi 只作参考（`upstream/StackChan/firmware/xiaozhi-esp32/docs/mcp-protocol.md`、`main/mcp_server.cc:45-128`）。我们自己做。

## 0. 先说结论

1. **能做成工具的东西不少。** 服务器端有 53 个方法或接口可以直接包成工具（第 1.2 节）。hello 本体有 17 个控制目标、23 组遥测、3 种服务器能触发的屏幕提示和 43 个口令词（第 1.3 节）。整理成 LLM 工具目录，可以给 LLM 的一共 32 个（第 2 节）：只读 20 个，可撤回动作 8 个，需主人确认 4 个。另有 9 组明确不给 LLM。
2. **有一个大缺口：服务器现在控制不了机器人，也读不到它的遥测。** `/v1/control` 只在机器人的 HTTP 服务上，而这个服务编译时是关的（`main/Kconfig.projbuild:2-4` 默认 n，`sdkconfig:600` 没开）。现在只有 USB 监视台能走 `dispatch_control`（`collector.cpp:1966-1977`）。服务器到机器人只有 `/mouth` 这一条下行，上面只走三样东西：TTS 音频和 `tts.*`、`mode_result`、`chat_stage`（`mouth_modes.h:35-47`）。所以凡是"让 hello 动"的工具，都要先在 `/mouth` 上加一条受控的 `control` 下行，再加一条状态上行。
3. **推荐 (c) 混合方案，以 (a) 为主。**
   - 工具调用在 mind 的对话循环里做，用 OpenAI 式的函数调用。工具的定义按 MCP 的样子写：名字、说明、输入 schema、标注。
   - 机器人的工具由 mind 转给 ear，ear 再走 `/mouth` 发下去；机器人那边再按白名单拦一次。
   - 以后要给别的客户端用（比如 Claude Code 调试），再在同一份工具表外面套一个 MCP 服务。机器人不做 MCP 服务器。
4. **时间预算决定了做法：查东西最多一轮，动作不等结果。**
   - 一次 terra 调用中位数 1.31 秒（`docs/session-handoff.md:12`）。多查一轮，就差不多多一次调用。
   - 所以常用的、便宜的东西继续直接放进上下文。只有"查"类工具才走函数调用，而且每句话最多查一轮。
   - "动"类工具（表情、转头）跟回答一起发出去，不等结果，也不为它再调一次模型。
5. **第一期 7 个工具，全在 mind 本地，不动固件：**
   - `memory_search`
   - `notes_search`
   - `scene_history`
   - `person_lookup`
   - `recent_speech`
   - `knowledge_search`
   - `who_is_here`

---

## 1. 现状盘点

### 1.1 对话现在怎么调模型

| 项 | 现状 | 位置 |
| --- | --- | --- |
| 调用 | 每句一次 `llm.chat`，OpenAI 兼容的 `/chat/completions`，不流式，没有 `tools` | `server/mind/llm.py:18-31`，`server/mind/chat.py:558-590` |
| 模型 | `gpt-6.1-terra`，`reasoning_effort: none`，`max_tokens` 300，温度 0.8 | `chat.py:560-561`，`speech_settings.py:18` |
| 超时 | 总共 6 秒（`LLM_TIMEOUT_S`）；3 秒内出错可以重试一次 | `chat.py:55-56`，`chat.py:566-571` |
| 上下文 | 性格、规则、时间、环境、此刻情景（在做什么/摘要/话题/声音）、最近纪要（≤400 字）、相关记忆（最多 3 条）、说话人的人物卡、最近 8 轮 | `chat.py:195-256`，`chat.py:754-817` |
| 上下文刷新 | 每部分有自己的 TTL（情景 20 s、纪要 30 s、人物和记忆 120 s），一句话最多花 100 ms 去刷新 | `chat.py:63`，`chat.py:68`，`chat.py:732-749` |
| 流式合成 | 一接到这句话，就先开一条 soul-tts 线路，再去想；回答分两段送进去（先第一句） | `chat.py:504-524`，`chat.py:172-180` |
| 预算 | 每天的花费上限（`budget_usd_per_day`）挡住对话调用；每次调用都记进 `mind_runs`（step `chat`） | `chat.py:443-445`，`chat.py:578-583` |
| 耗时 | terra 中位 1.31 s；预计说完到开口 2.9–3.6 s；v1 流式的目标约 2.5 s | `docs/session-handoff.md:12,21-22` |

可以借用的先例：`eye.py` 已经通过 aihubmix 用"强制工具调用"拿到结构化输出（`server/mind/eye.py:17`、`eye.py:112-114`），说明 aihubmix 会把 `tools` 和 `tool_calls` 原样转过去。terra 支不支持函数调用、能不能在同一次回答里既给文字又给 `tool_calls`、多花多少时间，这三件**（未核实）**，第 0 期要先测（第 4 节）。

### 1.2 服务器端：已经有的方法

"读/写"指它对数据的影响。"付费"指会不会调用付费模型。

**听觉（ear）**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 一句话结束事件 | 字、语种、情绪、说话人、声音带、离底噪多少 dB、语速、标签、自己的声音标记 | `server/ear/ear_server.py:654-667` | 产生数据 | 否（本地模型） |
| 说话人识别 | known / unsure / unknown，附第二名 | `ear_server.py:462-469` | 读 | 否 |
| 是不是人声 | CED 人声概率和声音带 | `ear_server.py:471-479` | 读 | 否 |
| 底噪 | 降噪后的底噪、原始麦克风底噪、比底噪高多少 | `ear_server.py:446-460` | 读 | 否 |
| 声音事件 | 打字、咳嗽、音乐…… 开始和结束 | `ear_server.py:703-760` | 产生数据 | 否 |
| 听悟实时 | 开关跟着服务器开关；分句和最终段落 | `ear_server.py:362-431`，`server/ear/tingwu_switch.py` | 产生数据 | 是（听悟） |
| 声纹录入和删除 | `enroll` / `forget`，由机器人发起 | `ear_server.py:773-800` | 写声纹库 | 否 |
| 入库 | `speech` / `sounds` / `tingwu` / `vision` / `frames` / `sessions` | `server/ear/hello_db.py:89-117` | 写 | 否 |
| 原始记录按时间窗读 | 带墙钟时间的 speech、tingwu、sounds、vision | `server/mind/records.py:29-60` | 读 | 否 |
| 是不是 hello 自己说的 | 和台词、最近说过的话比对 | `server/mind/selfvoice.py:57` | 读 | 否 |

ear 没有 HTTP 接口。它只有 WebSocket：听觉（根路径）、`/mouth`、`/signal`、`/vision`（`ear_server.py:983-1015`）。ear 调 mind 用 `mind_client`（`server/ear/mouth_link.py:99-121`），反过来 mind 调不到 ear。

**视觉**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| eye 每分钟一次 | 这一分钟最多 6 张机身画面加 M12 事件，交给 haiku，得到人数、谁、在哪、在做什么、场景、变化、把握 | `server/mind/eye.py:172`，结构 `eye.py:23-31` | 写 `eye_minutes` | 是（haiku） |
| 最新一分钟的视觉 | `GET /eye/latest` | `server/mind/api.py:383-387` | 读 | 否 |
| 一段时间的视觉 | `GET /eye` | `api.py:402-404` | 读 | 否 |
| 最新机身画面 | `GET /frames/latest`（JPEG base64） | `api.py:215-236`，`api.py:381` | 读 | 否 |
| M12 事件 | face_found / face_named / face_unknown / face_lost / person_found / person_lost / vision_on / vision_off / m12_online / m12_offline / face_attrs | `collector/m12_protocol.h:475-480`，`server/ear/vision_link.py` | 产生数据 | 否 |
| 人脸区间 | 从事件拼出谁在画面里的时间段 | `server/mind/package.py:110` | 读 | 否 |

**情景**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 分钟包和强要素 | 规则算出覆盖、主人在不在 | `server/mind/runner.py:37`，`package.py:138`，`strong.py:96` | 写 `mind_packages` | 否 |
| 分钟情景 | 门控 → 模型 → 校验 → 情景记录 | `server/mind/fusion.py:79`，提示词 `prompts.py:112` | 写 `mind_scenes` | 是（terra/luna） |
| 片段 | 按主人状态切段，模型写摘要 | `server/mind/segments.py:89` | 写 `mind_segments` | 是（只在写摘要时） |
| 此刻 | `GET /now`：最新情景、强要素、对话、当前片段 | `api.py:339-348` | 读 | 否 |
| 一段时间的情景 | `GET /scenes`（最近 60 分钟，含话题和声音），`/segments`，`/timeline`，`/minute` | `api.py:414-424`，`api.py:349-352`，`api.py:331-338` | 读 | 否 |
| 存取 | `latest_scene_any`、`scenes_range`、`segment_summary` | `server/mind/store.py:156,182,190` | 读 | 否 |

**纪要**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 切对话、写纪要 | 每分钟过 10 秒运行一次 | `server/mind/notes.py:177`，`notes_runner.py:75` | 写 `conversations` | 是 |
| 一段时间的纪要 | `GET /notes` | `api.py:399-401` | 读 | 否 |
| 对话里用的最新纪要 | 只取 10 分钟内结束的那一段 | `chat.py:766-773` | 读 | 否 |

**记忆**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 回想 | 已确认的记忆，手动的优先，30 天内见过的 | `server/mind/memory.py:262-275` | 读 | 否 |
| 列表、历史、编辑 | `/memories`、`/memory/history`、`POST /memory/edit` | `memory.py:215,227,233`，`api.py:296-306,466-474` | 读/写 | 否 |
| 学习 | 每 10 分钟一个窗口，观察 → 合并 | `server/mind/memory_worker.py:124`，`memory.py:66,175` | 写 | 是 |
| 和这句话相关的记忆 | 按两字词重合打分，最多 3 条 | `chat.py:188-192` | 读 | 否 |

**库（人物、笔记、记忆、知识）**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 概况、人物列表、一个人 | `/db/overview`、`/db/people`、`/db/person` | `server/mind/library.py:264,283,293`，`api.py:251-260` | 读 | 否 |
| 记忆、知识检索 | 按类别、人、话题、关键词、时间 | `library.py:306,316`，`api.py:266-269` | 读 | 否 |
| 一条的所有版本 | `/db/item` | `library.py:323`，`api.py:260-263` | 读 | 否 |
| 新增、改、恢复 | 只追加新版本，旧版本不动 | `library.py:173,216`，`api.py:271-286` | 写 | 否 |
| 对待规则 | 人物卡里的 `tease`（ok / words_only / never）和 `quote`（ok / gist / no） | `library.py:101-117` | 读 | 否 |
| 对话用的人物表 | 声纹名 → 称呼、最多 6 条在用的笔记、调侃规则 | `chat.py:781-795` | 读 | 否 |

**脱口秀**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 一轮 | 并行写五句，再写一句方言兜底，评审排序，开口前再核一次 | `server/mind/speak.py:321`，`say_round.py:80,168` | 写 `say_rounds` | 是 |
| 提前写、连续说 | 有人说话时预先写；2–3 分钟自动说一次 | `say_prefetch.py:26`，`say_auto.py:16`，`api.py:556-575` | 写 | 是 |
| 播某一句、角色、解释、统计 | `/say/play`、`/roles`、`/say/explain`、`/say/rounds`、`/say/stats` | `api.py:530-550,388,307-328,408-413,433-441` | 读/写 | 否 |
| 家人声音 | 主人和两位家人的克隆声音 | `say_round.py:20,57-70` | 读 | 否 |

**说话**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 说一句（脱口秀之外的旧路径） | `POST /say`，只在脱口秀模式 | `speak.py:212`，`api.py:494-510` | 写 | 是 |
| 对话的说话路径 | 无限的声音，只在对话模式，嘴必须是 TTS | `chat.py:592-650` | 写 | soul-tts |
| 试声音、试角色、声音列表 | `/voice/try`、`/role/try`、`/voices` | `speak.py:503,525,531`，`api.py:576-588,475-483,390-394` | 写 | soul-tts |
| 停、在不在说 | `cancel_pending`、`speaking`，`POST /say/stop` | `speak.py:42,52`，`api.py:589-591` | 写/读 | 否 |

**模式、计费、主人信号、开关、设置、成本**

| 方法 | 做什么 | 位置 | 读/写 | 付费 |
| --- | --- | --- | --- | --- |
| 模式镜像 | mind 只记录 hello 报上来的模式；`GET /mode` | `server/mind/mode_store.py:125,378`，`api.py:376-378` | 读 | 否 |
| 模式输入 | 只收 0 / 9 和 billing，其余回"在 hello 上切" | `mode_store.py:217`，`api.py:620-627` | 写 | 间接 |
| 模式表和预设 | 8 个模式，预设 0（全关）、9（全开） | `mode_table.py:29-160,162-168`，`modes.py:131` | 读 | 否 |
| 计费总开关 | 预设 9 和 0 的服务器那一半；机器人那一半只能由网页做（`senses_on_page`） | `billing.py:153-186`，`api.py:603-609` | 写 | 间接 |
| 主人打分 | DualKey 的好/坏，只追加；最近记录、统计 | `owner_signals.py:36,77,136`，`api.py:551-555,425-427` | 写/读 | 否 |
| 开关 | 听悟、识别（eye）、纪要；嘴（off / clips / tts）；情景（`/switch`） | `api.py:163-184,592-619` | 读/写 | 间接 |
| 设置 | `/settings`、`/speech/settings`（有版本，带期望版本号） | `api.py:363-366,484-493,293-294,447-465`，`settings_store.py:139` | 读/写 | 否 |
| 成本 | 今天花了多少、按天统计 | `store.py:170`，`api.py:141-161,405-407` | 读 | 否 |
| 对话轮次 | `chat_turns`，`GET /chat/turns` | `chat.py:819-827`，`api.py:428-432` | 读 | 否 |
| 录音 | ear 存 Opus 和画面文件，一律保留；目录说明在仓库外 | `server/ear/recorder.py`，`server/ear/pull-recordings.sh` | 写 | 否 |

合计：上面这些表一共 53 行，每行是一个方法或一组接口。这就是"服务器端 53 个"的来历。

### 1.3 hello 本体（collector-0.4.93）

**控制通道。** 现在服务器到不了 `/v1/control`（见第 0 节第 2 条）。机器人往外连 ear 的三条链路是：

- 听觉 WebSocket：上行音频和 `self` 标记，下行每 100 ms 一个 tick；
- `/vision`：M12 事件和机身画面，只上行；
- `/mouth`：下行 TTS、`mode_result`、`chat_stage`，上行 `tts.*`、`mode_report`、`mode_input`。

`dispatch_control` 已经和 HTTP 拆开了，用 `ControlReply` 收结果（`collector.cpp:1516-1522`），USB 监视台就是这么用的。所以在 `/mouth` 上加一条 `control` 下行，改动不大**（未核实，要看 ControlReply 能不能在 /mouth 任务里用）**。

**控制目标**（`collector.cpp:1462-1514`，17 个）。"写 NVS"指会不会存到下次开机。

| 目标 | 字段和范围 | 写 NVS | 位置 | 给 LLM？ |
| --- | --- | --- | --- | --- |
| `yaw_step` | `delta_deg` ±1…30 或 ±45，或 `slot` −2…2（每档 45°），`speed` 50–1000，`path` | 否 | `collector.cpp:1220-1251`，`web_control.h:11-14` | 可以，限档位。**注意：手动转头会关掉追踪**（`remote.cpp:967,1002` `manual_override`） |
| `yaw_turn` | `duration_ms` 100–2000，定时转身 | 否 | `collector.cpp:1258-1271` | 只在明确要求时 |
| `pitch` | `angle_deg` 5–85 | **是**（`remote.cpp:1024` `pitch_deg10`） | `collector.cpp:1283-1303` | 不给；点头要另做一个不存的动作 |
| `remote` | `enabled`，舵机接收开关 | 否 | `collector.cpp:1273-1281` | 不给 |
| `servo` | EEPROM PID、扭矩、运行时参数 | **是**（写舵机 EEPROM） | `collector.cpp:1393-1440` | 不给 |
| `camera` | `enabled` | **是** | `collector.cpp:1380-1391` | 需主人确认 |
| `wifi` | add / remove / prefer / scan / reconnect | **是** | `collector.cpp:1337-1378` | 不给 |
| `m12_view` | `enabled`，M12 人脸画面页 | 否 | `collector.cpp:1192-1197` | 不给（会抢走 interaction 对 M12 的控制） |
| `m12_mode` | standby / face_detector | 否 | `collector.cpp:1198-1207` | 不给 |
| `m12_request` | ping / ver / status / cfg / learn / who / forget / faces，异步 | learn / forget 写 M12 人脸库 | `collector.cpp:1467-1489` | 只给 `who`（只读）；learn / forget 不给 |
| `voice_play` | `clip_id` 0–80（0 = 随机） | 否 | `collector.cpp:1208-1219`，`voice_policy.h:5` | 可以（报错文字还写着 0..40，与 `kVoiceCount=80` 不一致） |
| `eyes` | play（30 种状态，once / loop）、configure（v05 / v14 / v18）、stop | configure 是否存**（未核实）** | `eyes/http.cpp:116-127`，`eyes/protocol.h:60-85`，`eyes/catalog.generated.h:12-14` | 只给 play once |
| `volume` | `value` 10–100 | **是**（`playback.cpp:497`） | `collector.cpp:1495-1501` | 要先加一个不存的写法 |
| `mic_gain` | 0–24 dB | **是** | `collector.cpp:1502-1514` | 不给 |
| `mode` | 模式输入词，照 ASR 口令执行 | 否 | `collector.cpp:1442-1460` | 不给（hello 自己定模式） |
| `interaction` | tracking / recognition / vision / advanced 开关 | 否 | `interaction.cpp:40-60,130-133` | 暂不给（第 3.5 节） |
| `hearing` | listen / config（写 ear 地址和令牌）/ enroll / forget / cancel | config **是** | `hearing.cpp:503-544` | 不给 |

**遥测**（`collector.cpp:985-1135`，顶层 23 组）：

- IMU、磁场、光照（`lux`）、接近（`collector.cpp:766-850`）；
- 头顶触摸 `head_touch.values`、屏幕触摸 `touch`（`collector.cpp:1046-1057`）；
- 电量 `power.battery_percent` / `charging`（`collector.cpp:1058-1066`）；
- 芯片温度 `temperature.celsius`（不是室温，见 `docs/collector-protocol.md:109-112`）；
- `remote`（两个舵机的角度、追踪）、`pitch_setting`、`wifi`、`face_detection`、`display`、`playback`（音量、在不在播）、`hearing`、`eyes`、`asr`、`m12`（含 uplink）、`mouth`、`mode`、`interaction`（追踪状态 `interaction.tracking`）、`health`、`monitor`。

这些服务器现在一项都读不到。

**屏幕**

- 角标：模式名和颜色（`screen.h:96` `screen_update_mode`）；
- 提示：`mode_result.toast`，ASCII，≤32 字符；
- 对话三个点：listen / think / skip（`collector/chat_stage.h`）；
- 人脸框页（`screen.h:65-73`）；
- 头顶表情反应（`screen.h:64`）。

服务器不能往屏幕上写任意文字。

**口令词**（`asr_protocol.h:56-97`，43 个）

| 类别 | 词 | 谁处理 |
| --- | --- | --- |
| 方向 | up / down（开关追踪）、left / right（45°）、turn left / turn right（90°）、front（回正）、back（转身） | 本地（`asr_motion.h:8-23`） |
| 模式 | forward / backward、play / pause、open / close、start / stop、next / previous、zero…nine | hello 的模式规则；0 / 9 交给 mind（`asr_mode_words.h:8-42`） |
| 其他本地 | turn on / turn off（M12 画面）、turn on / off the lights、ok、hi ASR、hello、音量 5 个、查版本、announce、Hi M Five | 本地（`asr_unit.cpp:240-275`） |

**和 xiaozhi 对照。**

- xiaozhi 的设备工具有 `self.get_device_status`、`self.audio_speaker.set_volume`、`self.screen.set_brightness`、`self.camera.take_photo`。
- `AddUserOnlyTool` 是只给用户、不给 LLM 的那一类：重启、升级固件、截屏（`mcp_server.cc:45-128`）。
- 我们的"不给 LLM"一级和它的 user-only 是同一个意思。

---

## 2. 工具目录

### 2.1 约定

**安全等级**

| 等级 | 意思 | LLM 能不能直接用 |
| --- | --- | --- |
| **R 只读** | 只读数据，不改任何东西 | 能 |
| **A 可撤回动作** | 有动作，但不存到下次开机，下一次动作或本地规则就能盖掉；有范围和频率限制 | 能，在限制内 |
| **C 需主人确认** | 写库、花钱、隐私、改开关。LLM 只能"提议"，提议进页面上的待确认列表 | 只能提议 |
| **N 不给 LLM** | 写 NVS、网络、舵机参数、声纹和人脸库、模式、计费、录音原件 | 不出现在工具表里 |

**在哪跑**

- **mind**：mind 进程里本地读 Mongo，几十毫秒内。
- **ear→/mouth**：mind 经 ear 转给机器人（新）。一来一回大约 100–500 ms。办公室到服务器的 ping 平均 85–98 ms、最高 300–523 ms（`docs/session-handoff.md:196,528`）。
- **外部**：付费模型或网络服务。

**返回格式**：一律 `{ok: true, data: ...}` 或 `{ok: false, error: {code, message}}`。

- 返回的字数有上限（默认 1,200 字），超了就截断并标 `truncated: true`。
- 时间一律用本地时间的文字（"今天 14:20"），不给毫秒数。

**人名**

- 工具里出现的人一律用库里的称呼（`display`）。
- 不返回声纹名、脸名这类登记名，也不返回 `person_id` 以外的内部键。
- 人物卡的对待规则在工具层就执行，不只靠提示词：`quote: no` 只给大意，`tease: never` 附一句"不要调侃"。

### 2.2 感知

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scene_now` | 此刻的情景，比上下文里的更细 | `{}` → `{time, doing, summary, topics[], sound, people[{who,where,doing}], eye_confidence, owner_state}` | `GET /now`（`api.py:339`）+ 最新 `eye_minutes`（`api.py:383`）+ `strong` 的主人状态 | mind | <50 ms | 无 | 无 | R |
| `scene_history` | 某段时间在干什么 | `{from: "今天 9:00" \| "1小时前", to?, detail: "segments"\|"minutes"}` → `{segments[{start,end,cls,title,summary}]}` 或 `{minutes[{time,text,topic,sound}]}`（≤60 条） | `segments.update(summarize_now=False)`（`segments.py:89`）、`store.scenes_range`（`store.py:182`） | mind | 50–200 ms | 无（不触发新摘要） | 无 | R |
| `recent_speech` | 刚才听到的话（去掉 hello 自己的） | `{minutes: 1–10, who?: "说话人称呼"}` → `{lines[{time, who, text, source: "本地"\|"听悟"}]}`（≤40 句） | `records.window`（`records.py:47`）+ `selfvoice.is_hello`（`selfvoice.py:57`）+ 人物表（`chat.py:781`） | mind | <100 ms | 无 | 无 | R |
| `recent_sounds` | 刚才的声音事件 | `{minutes: 1–10}` → `{events[{time, kind_zh, dur_s}]}` | `records.window` 的 `sounds` | mind | <50 ms | 无 | 无 | R |
| `who_is_here` | 现在谁在 | `{}` → `{people[{who, seen_by: "脸"\|"声音"\|"画面", last: "30秒前"}], owner_state}` | 新：合并最近 2 分钟的 M12 `face_named`、说话人 `known`、eye 的 `people` | mind | <100 ms | 无 | 无 | R |
| `look_now` | 现在看一眼（让视觉模型看最新画面） | `{question?: "≤40字"}` → `{people_count, people[], scene, answer?}` | 新：最新 1–3 张 `frames` + `eye.messages_for` 的变体 | 外部（haiku） | 2–5 s **（未核实）** | 每次一次 haiku | 写 `mind_runs` | R（付费，受识别开关和次数上限约束） |
| `robot_status` | hello 的身体状态 | `{}` → `{battery_percent, charging, chip_c, lux, near, head_touched, touched_ago_s, yaw_deg, pitch_deg, tracking, volume, camera_on, m12_online, mode}` | 新：机器人状态上行的缓存（第 3.4 节） | mind（读缓存） | <10 ms | 无 | 无 | R |

### 2.3 记忆

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notes_search` | 查对话纪要 | `{query?: "关键词", from?, to?, limit: 1–5}` → `{conversations[{start, end, title, notes, people[]}]}` | `conversations` 按时间和关键词（`api.py:399` 的查询 + `notes.candidates` 的两字词打分 `notes.py:105`） | mind | <100 ms | 无 | 无 | R |
| `memory_search` | 查记忆 | `{query: "≤30字", person?: "称呼", limit: 1–8}` → `{memories[{statement, when, kind, source: "学到的"\|"主人写的"}]}` | `MemoryStore.recall`（`memory.py:262`）+ `related_memories` 打分（`chat.py:188`）+ `Library.memories(q)`（`library.py:306`） | mind | <100 ms | 无 | 无 | R |
| `knowledge_search` | 查知识（地点、工作、约定、术语、hello 自己） | `{query, kind?: place\|work\|commitment\|life\|term\|self\|other, limit: 1–5}` → `{items[{name, statement, valid}]}` | `Library.knowledge`（`library.py:316`） | mind | <50 ms | 无 | 无 | R |
| `chat_history` | 更早和 hello 聊过的话 | `{query?, days: 1–7, limit: 1–10}` → `{turns[{time, who, heard, reply}]}` | `chat_turns`（`chat.py:705`、`chat.py:819`） | mind | <100 ms | 无 | 无 | R |
| `memory_propose` | 提议记下一件事 | `{statement: "≤120字", about?: ["称呼"], why: "≤40字"}` → `{proposal_id, status: "待主人确认"}` | 新：`memory_proposals` 集合 + 页面上的确认列表；主人同意后才走 `Library.put`（`library.py:173`） | mind | <50 ms | 无 | 写一条提议 | C |

### 2.4 认识人

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `person_lookup` | 查一个人 | `{who: "称呼或别名"}` → `{display, kind, notes[≤6], recent_memories[≤3], treatment: {tease, quote}}` | `Library.person`（`library.py:293`），按 `display` / `aliases` 找 | mind | <100 ms | 无 | 无 | R |
| `people_list` | hello 认识哪些人 | `{}` → `{people[{display, kind}]}` | `Library.people`（`library.py:283`），去掉 `retired` | mind | <50 ms | 无 | 无 | R |
| `person_note_propose` | 提议给某人记一条笔记 | `{who, kind: trait\|fact\|preference\|boundary\|relation, text: "≤80字"}` → `{proposal_id}` | 新：同 `memory_proposals`，主人同意后才走 `Library.put`（`people_notes`） | mind | <50 ms | 无 | 写一条提议 | C |
| `face_who` | 认一下面前的脸 | `{}` → `{faces[{who \| "没认出", sim}]}` | `m12_request op=who`（`collector.cpp:1467-1489`），结果在遥测 `m12.commands`，要随状态上行带回 | ear→/mouth | 0.5–1.5 s **（未核实）** | 无 | 无 | R |
| 声纹录入、删除；人脸学习、删除 | — | — | `hearing enroll/forget`、`m12_request learn/forget` | — | — | — | 写库 | **N** |

### 2.5 说与表达

对话的回答本身就是"说"，不做成工具。

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `express_eyes` | 做一个表情 | `{state: 30 种之一（happy、sad、surprised、thinking、shy……）}` → `{accepted}` | `eyes op=play mode=once`（`eyes/protocol.h:74-80`），`request_id` 由 mind 生成 | ear→/mouth | 发出 0.1–0.5 s | 无 | 播一次后回到模式的眼睛 | A（每句最多 1 个，不许 loop） |
| `play_clip` | 播一个声音片段（笑声、叹气……） | `{clip_id: 1–80}` 或 `{emotion: "happy"…}` → `{accepted, clip_id}` | `voice_play`（`collector.cpp:1208`）；按情绪选片段用 `config/voice-emotions.json` | ear→/mouth | 0.1–0.5 s | 无 | 和 TTS 抢扬声器（忙时 409） | A（只在 hello 没在说的时候） |
| `set_volume` | 调音量 | `{level: 30–70}` 或 `{step: ±10}` → `{volume}` | 新：`volume` 加不存 NVS 的写法（`playback.cpp:492-497` 现在每次都存） | ear→/mouth | 0.1–0.5 s | 无 | 下一次播放起生效 | A（只在明确要求时，范围固定）；要存下次开机就是 C |
| `stop_speaking` | 别说了 | `{}` → `{cancelled}` | `speak.cancel_pending`（`speak.py:42`） | mind | <50 ms | 无 | 停掉当前的话 | A。一般用不上：本地已有"说话时被点就停"的规则 |
| 换声音、用家人声音 | — | — | `/voice/try`、家人声音（`say_round.py:57-70`） | — | — | — | 用克隆声音 | **N**（主人定了对话用无限的声音） |
| 讲一轮脱口秀 | — | — | `say_round`（`speak.py:321`） | — | — | — | 换到脱口秀的行为 | **N**（只在脱口秀模式；LLM 只能说"说 forward 就进脱口秀"） |

### 2.6 动

所有动作都走机器人自己的保护：

- `remote_*` 入口保留了限位、堵转和故障锁存，所有动作都排进同一个舵机任务（`asr_motion.h:69-72`）；
- ±90° 的范围；
- 202 只表示已排队。

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `turn_head` | 转头到某个方向 | `{to: "左90"\|"左45"\|"正前"\|"右45"\|"右90"}` → `{accepted}` | `yaw_step slot` −2…2（`collector.cpp:1188,1241`、`remote.cpp:994`） | ear→/mouth | 发出 0.1–0.5 s，走完约 0.6 s | 无 | **会关掉追踪**（`remote.cpp:1002`）；见第 3.5 节 | A（每 5 s 最多 1 次，每分钟最多 4 次） |
| `face_front` | 回正 | `{}` → `{accepted}` | `yaw_step slot=0` | ear→/mouth | 同上 | 无 | 同上 | A |
| `turn_around` | 转过身去 | `{}` → `{accepted}` | `yaw_turn duration_ms` 默认 1200（`collector.cpp:1258`） | ear→/mouth | 1–2 s | 无 | 背对着人；转回来要再说一次 | A，只在说话人明确要求时 |
| `nod` / `shake_head` | 点头、摇头 | `{times: 1–2}` → `{accepted}` | 新：固件里加一个不存的小幅动作。`pitch` 会写 NVS，不能拿来点头 | ear→/mouth | 发出 0.1–0.5 s | 无 | 小动作 | A |
| 追踪开关 | — | — | `interaction tracking` | — | — | — | 改变 hello 的长期行为 | 暂 **N**（口令 up / down 归主人；主人决定第 4 条） |
| 舵机参数、俯仰设置、舵机接收开关 | — | — | `servo`、`pitch`、`remote` | — | — | — | 写 EEPROM / NVS | **N** |

### 2.7 设备与设置

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `switches_status` | 各开关现在开没开 | `{}` → `{tingwu, eye, notes, mind, mouth, billing}` | `switches()`（`api.py:170-184`）+ `store.enabled()` | mind | <20 ms | 无 | 无 | R |
| `cost_today` | 今天花了多少 | `{}` → `{usd, calls, budget_usd}` | `store.cost_today`（`store.py:170`）、`stats`（`api.py:141`） | mind | <50 ms | 无 | 无 | R |
| `switch_propose` | 提议开关听悟、识别、纪要、情景 | `{name, on}` → `{proposal_id}` | 新：进确认列表，主人在页面点 | mind | <50 ms | 无 | 写一条提议 | C |
| `camera_propose` | 提议开关机身相机 | `{on}` → `{proposal_id}` | 同上；执行仍由网页走 USB | mind | <50 ms | 无 | 写一条提议 | C |
| Wi-Fi、麦克风增益、听觉配置、M12 模式和画面、设置、说话设置、计费、预设 | — | — | 见第 1.3 节、`api.py:447-493,603-609,651-657` | — | — | — | 写 NVS 或全局设置 | **N** |

### 2.8 模式

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mode_status` | 现在是什么模式，怎么切 | `{}` → `{mode, name_zh, since, how_to_switch: {脱口秀: "说 forward", ...}}` | `ModeStore.current`（`mode_store.py:125`）+ `mode_table.MODES` 的 `enter` 词 | mind | <20 ms | 无 | 无 | R |
| 切模式、预设 0–9、计费 | — | — | `modes().apply`、`/preset`、`/mode` | — | — | — | 改 hello 的模式 | **N**（hello 自己定模式） |

### 2.9 主人信号、录音、网络

| 工具 | 说明 | 参数 → 返回 | 实现 | 在哪 | 延迟 | 花费 | 副作用 | 等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ratings_summary` | 主人最近给的好评和差评（让 hello 知道哪类话受欢迎） | `{days: 1–7}` → `{good, bad, examples[≤3]}` | `owner_signals.stats`（`owner_signals.py:136`） | mind | <100 ms | 无 | 无 | R（后期） |
| 打分 | — | — | `owner_signals.put_signal` | — | — | — | 只能主人打 | **N** |
| 录音原件、画面文件 | — | — | `recordings/` | — | — | — | — | **N**。画面只经 `look_now` 交给视觉模型，音频永远不给 |
| `web_search` | 搜网页 | `{query: "≤40字"}` → `{results[{title, snippet, source}]≤3}` | 新（服务商待定） | 外部 | 1–3 s **（未核实）** | 按次付费 | 查询词离开服务器 | R（付费，有单独开关，默认关） |
| `weather` | 天气 | `{when: "今天"\|"明天"}` → `{text, temp_c}` | 新 | 外部 | 0.3–1 s | 免费或很少 | 无 | R（后期） |

合计可以给 LLM 的 32 个：只读 20 个，可撤回动作 8 个（点头、摇头算一个），需主人确认 4 个。另有 9 组不给 LLM（表里加粗 N 的那几行）。

### 2.10 第一期工具的完整 schema

函数调用里给模型看的就是下面这些，说明写成中文。

```json
[
 {"name": "memory_search",
  "description": "查 hello 记得的事。只在这句话问到过去的事、某人的事、或你不确定是否记得时调用。上下文里已有的记忆不用再查。",
  "parameters": {"type": "object", "additionalProperties": false, "required": ["query"],
    "properties": {"query": {"type": "string", "maxLength": 30, "description": "要找的事，几个词"},
                   "person": {"type": "string", "maxLength": 40, "description": "和谁有关（称呼），可省"},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5}}}},
 {"name": "notes_search",
  "description": "查对话纪要：今天或最近几天大家聊过什么。问“刚才/上午/昨天聊了什么”时调用。",
  "parameters": {"type": "object", "additionalProperties": false,
    "properties": {"query": {"type": "string", "maxLength": 30},
                   "from": {"type": "string", "description": "如“今天 9:00”“2小时前”“昨天”"},
                   "to": {"type": "string"},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}}}},
 {"name": "scene_history",
  "description": "查某段时间 hello 看到和听到的情景：主人在不在、在做什么。问“今天都干了啥”“我几点来的”时调用。",
  "parameters": {"type": "object", "additionalProperties": false, "required": ["from"],
    "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                   "detail": {"type": "string", "enum": ["segments", "minutes"], "default": "segments"}}}},
 {"name": "person_lookup",
  "description": "查 hello 认识的某个人：称呼、特点、最近和他有关的事。说话里提到一个人、而背景里没有他时调用。",
  "parameters": {"type": "object", "additionalProperties": false, "required": ["who"],
    "properties": {"who": {"type": "string", "maxLength": 40, "description": "称呼或别名"}}}},
 {"name": "recent_speech",
  "description": "查最近几分钟别人说过的原话（不含 hello 自己）。问“我刚才说了什么”“他刚说啥”时调用。",
  "parameters": {"type": "object", "additionalProperties": false,
    "properties": {"minutes": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                   "who": {"type": "string", "maxLength": 40}}}},
 {"name": "knowledge_search",
  "description": "查整理过的知识：地点、工作、约定、术语、hello 自己的设定。",
  "parameters": {"type": "object", "additionalProperties": false, "required": ["query"],
    "properties": {"query": {"type": "string", "maxLength": 30},
                   "kind": {"type": "string", "enum": ["place", "work", "commitment", "life", "term", "self", "other"]},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}}}},
 {"name": "who_is_here",
  "description": "现在 hello 身边有谁（脸、声音、画面合起来）。问“谁在”“还有别人吗”时调用。",
  "parameters": {"type": "object", "additionalProperties": false, "properties": {}}}
]
```

每个工具在代码里还要多带几项，这几项不给模型看：

- `class`（R / A / C）；
- `where`；
- `timeout_ms`（第一期一律 300）；
- `max_chars`；
- MCP 风格的标注：`readOnlyHint`、`destructiveHint`、`openWorldHint`。

---

## 3. 怎么调用：架构

### 3.1 三个方案

| | (a) 对话循环里的函数调用 | (b) 真的 MCP 服务 | (c) 混合（推荐） |
| --- | --- | --- | --- |
| 做法 | `chat._ask` 传 `tools`，收到 `tool_calls` 就在 mind 里执行，再调一次模型；机器人的工具经 ear→`/mouth` 转发 | mind 起一个 MCP 服务（stdio / HTTP）；对话循环当 MCP 客户端。机器人的工具要么由 mind 代理，要么像 xiaozhi 那样让机器人自己当 MCP 服务器，在 `/mouth` 上走 `{"type":"mcp","payload":JSON-RPC}` | 执行用 (a)。工具表按 MCP 的样子写，一份定义两处用；以后需要时给工具表套一层 MCP 服务，给 Claude Code 调试或别的客户端用。机器人不当 MCP 服务器，只收白名单里的 `control` |
| 多出的延迟 | 只有工具本身 | 每次调用多一跳进程间 JSON-RPC（几毫秒到几十毫秒） | 同 (a) |
| 工程量 | 小：`llm.chat` 不用改，`chat._ask` 加一个循环 | 中：MCP SDK 依赖、会话管理；机器人做 MCP 要在 ESP32 上解析 JSON-RPC、存工具说明，改一句说明就要刷机 | 小，以后可以加 |
| 机器人的工具 | mind 定义，机器人只按白名单执行 | 机器人自报工具（`tools/list`） | mind 定义；机器人在 `/mouth` 的 hello 里报自己支持哪些目标（现成的有 `control_info_json`，`collector.cpp:1524-1532`），mind 只给模型看机器人支持的那些 |
| 风险 | 工具表只在 mind 里 | 工具说明分散在固件和服务器两处 | 白名单在机器人那边再拦一次，服务器被攻破也只能做 A 级动作 |

**推荐 (c)，理由：**

1. 对话只有一个调用方，就是 mind 自己，用不着跨进程的协议。
2. 机器人的工具很少，而且是固定的。说明放在 mind 里，改起来不用刷机。
3. 机器人那边最要紧的是"拦得住"。白名单加一个来源标记 `llm`，比整套 MCP 有用。
4. 工具表按 MCP 的样子写，以后套 MCP 服务几乎不用改。

### 3.2 时间预算

一句话的时间现在大概这样花（`docs/session-handoff.md:12,21-22`）：

- 识别完到 mind 收到：一百多毫秒；
- 刷新上下文：≤100 ms；
- 模型：中位 1.31 s；
- soul-tts 首包：约 1 s；
- 合起来，说完到开口 2.9–3.6 s，流式的目标约 2.5 s。

| 这句话属于 | 怎么做 | 多花的时间 |
| --- | --- | --- |
| 普通闲聊（大多数） | 不调工具，跟现在一样 | 0 |
| 带动作（表情、转头） | 动作和回答同一次给出，mind 一边送文字给 TTS，一边把动作发给机器人，不再调模型 | 约 0（动作发出 0.1–0.5 s，和 TTS 首包重叠） |
| 要查东西 | 第一次调用给出 `tool_calls`，mind 并行执行（每个 ≤300 ms），第二次调用给出回答 | 约 +1.3–2 s，查的那句话说完到开口约 4–5.5 s |

规则：

- **每句最多查一轮。** 第二次调用不带 `tools`，所以模型只能回答。
- **一轮最多 4 个调用，并行执行**，每个 300 ms 超时；超时的那个就回 `{ok:false,error:{code:"timeout"}}`。
- **整句的期限从 6 s 提到 8 s**（只对查东西的那句）。到期就丢掉工具结果，直接让模型回答，或者跳过这句并记下原因。
- **动作要跟回答一起给出。** 做法有两种，第 0 期实测后选：
  1. 同一次回答里既有文字又有 `tool_calls`，只含动作工具时不再调模型。这要模型支持**（未核实）**。
  2. 回答改成 JSON `{say, actions[]}`，动作不走函数调用。这一定能用，代价是要放弃 `content` 纯文本的流式。
- **想得久，hello 要有表示。** 查东西时机器人的三个点停在 think（ear 已经在发 `chat_stage`，`mouth_link.py:46-55`）。要不要先说一句"我想想"，见主人决定第 2 条。

**直接放进上下文，不做成调用的**（每句都要用、本地读几毫秒、字数少）：

- 时间、环境；
- 此刻的情景；
- 最近纪要；
- 说话人的人物卡；
- 最多 3 条相关记忆；
- 最近几轮对话；
- 模式；
- 新增一行"身体状态"：电量低、正被摸、头朝哪边，只在有异常时写。

### 3.3 和流式合成怎么配合

- v1 一接到这句话就开一条 soul-tts 线路（`chat.py:504-524`）。
- 遇到查东西的那句，线路要多空等 1.3–2 s。soul-tts 线路空着多久会自己关**（未核实）**。
- 办法有两个：
  1. 第一次调用的结果是 `tool_calls` 时，先取消线路，等第二次调用前再开。多花约一次开线路的时间，`open_ms` 已有记录。
  2. 线路照开，让模型在 `tool_calls` 旁边给一句很短的开场白，先送进去说。
  - 默认用第 1 个，第 2 个看主人决定第 2 条。
- "后来的话盖掉前面的"规则（`superseded`，`chat.py:476-482`）不变。查东西的那句更久，被盖掉的机会大一些，`chat_turns` 里会看得到。
- 现在的 `llm.chat` 不流式。要等第 3 期再考虑流式解析：先看第一个块是文字还是工具调用，这样能早一点开口。

### 3.4 机器人这边要加的两条线

1. **`/mouth` 控制下行**（新）。
   - mind 发 `POST ear 127.0.0.1:<端口>/robot/control {id, body:{target,...}}`。ear 要新开一个只听本机的 HTTP 口，带 mind 的令牌。
   - ear 往当前的 `/mouth` 链路发 `{"type":"control","id":N,"src":"llm","body":{...}}`。
   - 机器人用 `dispatch_control` 执行（`collector.cpp:1516`），回 `{"type":"control_result","id":N,"status":202,"body":{...}}`。
   - ear 最多等 1.5 s。
   - 机器人那边有白名单 `kMouthControlTargets`：
     - `eyes`：只收 play once；
     - `yaw_step`：只收 slot，速度用缺省；
     - `yaw_turn`；
     - `voice_play`；
     - `volume`：只收不存的写法；
     - `m12_request`：只收 who；
     - 以后加的点头、摇头。
   - 白名单以外的一律 403。
   - 只在 hello 自己的模式是 `chat` 时收。
   - 每类动作有冷却时间。
   - 本地输入（口令、遥控、触摸）一来，就取消还没做完的 `llm` 动作。
   - 要改的文件里有 `mouth_modes.h`（`Text` 枚举，`mouth_modes.h:35-47`）。它现在正被别的代理改，到时候要排在后面做。
2. **状态上行**（新）。
   - 机器人每 30 s 发一次 `{"type":"status",...}`（≤512 B），有变化时立刻发。字段就是 `robot_status` 的返回值。
   - ear 收下后照"耳朵什么都报"的原则写进 Mongo `robot_status`，同时转给 mind；mind 缓存最新一条。
   - `face_who` 的结果也随这条带回来。

### 3.5 hello 自己定模式，LLM 不跟它抢

1. **工具只在对话模式里有。** `chat.py` 本来就只在 `chat` 时工作（`chat.py:131-135`）。每个动作工具执行前再核一次 mind 的镜像模式，hello 那边也再核一次自己的模式。
2. **LLM 永远不能切模式、不能按预设、不能动计费。** 这些工具根本不在表里。用户想切模式，LLM 只能告诉他该说哪个词（`mode_status.how_to_switch`）。
3. **眼睛**：只许 play once，播完回到模式自己的眼睛（对话模式是 v05，`mode_table.py`）。不许 configure，不许 loop。
4. **转头和追踪会打架。** `yaw_step` 一转，追踪就关掉了，而且不会自己恢复（`remote.cpp:967,1002`）。开机自动打开的追踪，也会被 LLM 的一次转头关掉。有三种处理（主人决定第 4 条）：
   - 默认：固件加一个 `llm` 来源的转头，转完 N 秒后把追踪恢复成转之前的样子；
   - 或者：追踪开着时不许 LLM 转头；
   - 或者：只许"看向说话的人"一次，用追踪本身去转。
5. **停**：hello 本地已有"说话时被点就停"的规则（`modes.py` 的 `stop_speech`），LLM 不需要停的工具。`stop_speaking` 留着备用。
6. **代数**：mode 变化、停、离开对话时，把还没执行的 `llm` 动作作废。办法和 `speak._EPOCH` 一样。

### 3.6 隐私

- **公开仓库**：
  - 工具说明、测试数据和例子里不放真实称呼；
  - 测试用"主人""某同事"和假数据；
  - 工具日志不打印说的话（ear 已经是这个规矩，`mouth_link.py:45`）。
- **送给模型服务商的东西**：
  - 现在的上下文已经把情景、纪要、记忆、人物笔记送给 aihubmix 了；
  - 工具多出来的是 `recent_speech` 的原话，和别人的人物卡；
  - 原话只给最近 10 分钟，人物卡按 `quote` 规则只给大意（主人决定第 8 条）。
- **录音**：
  - 音频永远不给模型；
  - 画面只经 `look_now` 交给视觉模型，走的是 eye 已经在用的那条路，受识别开关和相机开关管。
- **名字**：
  - 只给 `display`；
  - 声纹名、脸名、`owner.voice_names` 这类登记名不出工具；
  - 人物卡是 `retired` 的不给。
- **记录**：
  - 每轮的工具调用记进 `chat_turns.tools[{name, args, ms, ok, chars}]`。
  - 参数里可能有称呼，但 `chat_turns` 在私有库里。

### 3.7 花费上限

- 第一期的工具全是本地读，不花钱。
- 查东西多一次模型调用，每句约多 0.06 美分（按 terra 的价格推算；输入会长一些，**（未核实）**）。
- 规则：
  - 每天的上限 `budget_usd_per_day` 照样挡住整句（`chat.py:443-445`），工具的花费记 `mind_runs` 的 `step: chat_tool`；
  - 付费工具（`look_now`、`web_search`）各有每小时次数上限和单独开关，默认 `look_now` 20 次/小时，`web_search` 关；
  - "关了就不花钱"：识别开关关着时 `look_now` 直接回 `{ok:false,error:{code:"eye_off"}}`，相机关着也一样。

### 3.8 上下文怎么拆："一直带着"和"要时再查"

| 内容 | 现在 | 以后 | 理由 |
| --- | --- | --- | --- |
| 性格、规则、时间、环境 | 带着 | 带着 | 每句都要 |
| 此刻情景（在做什么、摘要、话题、声音） | 带着 | 带着；细节用 `scene_now` | 短，常用 |
| 最近纪要（10 分钟内，≤400 字） | 带着 | 带着；更早的用 `notes_search` | 现在的就够用，更早的要时再查 |
| 记忆 | 回想 8 条，挑相关的最多 3 条 | 一样；不够时用 `memory_search` | 现在常常挑不到（两字词重合太粗），查一下更准 |
| 说话人的人物卡 | 带着 | 带着；别人的用 `person_lookup` | 说话人每句都要 |
| 最近几轮对话 | 8 轮，1 小时内 | 一样；更早的用 `chat_history` | — |
| 一段时间的情景、原话、声音、谁在、知识 | 没有 | 用工具查 | 长，不常用 |
| 模式 | 没写（只在对话模式工作） | 带一行 | 让 LLM 知道还能切到哪些模式 |
| 身体状态 | 没有 | 有异常时带一行；细节用 `robot_status` | 电量低、被摸要马上知道 |
| 工具使用规则 | 没有 | 系统提示里加 3 条：只在背景里没有时才查；查不到就说不记得，不要编；动作每句最多一个 | 防止乱查 |

---

## 4. 分期

每一期都照"在录音上验证"的规矩做：

1. 先拿主人真实的 `chat_turns` 和当时的上下文离线重放，比较改之前和改之后；
2. 再上线；
3. 上线后用 `chat_turns.timings` 看实际耗时。

### 第 0 期：打底（不改对话行为）

- **做什么：**
  - 在 `server/mind/` 里加工具表和执行器。工具表是一个 Python 模块，因为 `deploy.sh` 只发 `server/mind/*.py`（`mode_table.py:3-4` 说过）。
  - `chat._ask` 支持 `tools` 和最多一轮。
  - 记录 `chat_turns.tools` 和 `timings.tool_ms`。
  - 离线重放脚本。
  - 实测 terra 的函数调用，要回答四件事：
    1. 支不支持；
    2. 同一次回答能不能既给文字又给 `tool_calls`；
    3. 带 7 个工具定义后，第一次调用慢多少；
    4. 第二次调用中位多少秒。
- **主人要定：** 无（结果出来再定第 1 条的数字）。

### 第 1 期：7 个"查"的工具，全在 mind 本地

- **顺序**（越靠前越常被问到，越能让对话"变聪明"）：
  1. `memory_search` —— "你还记得……吗"；
  2. `notes_search` —— "我们上午聊了什么"；
  3. `scene_history` —— "我今天几点来的、都干了啥"；
  4. `person_lookup` —— 提到不在场的人；
  5. `recent_speech` —— "我刚才说什么来着"；
  6. `knowledge_search` —— 地点、工作、约定；
  7. `who_is_here` —— "还有谁在"。
- **做什么：**
  - 7 个工具；
  - 系统提示加工具规则；
  - 在页面的对话面板里显示这句话调了哪些工具、各花多久。
- **不动固件，不动 ear。**
- **主人要定：** 第 1、2、8、9 条。

### 第 2 期：hello 动起来（固件 + ear）

- **做什么：**
  - 固件：
    - `/mouth` 的 `control` 下行和白名单；
    - 状态上行；
    - 不存的音量写法；
    - `llm` 来源的转头和追踪恢复；
    - 点头、摇头。
  - ear：
    - 本机 HTTP 转发口；
    - 存 `robot_status`。
  - mind：
    - `express_eyes`、`turn_head`、`face_front`、`robot_status`、`set_volume`、`play_clip`、`turn_around`、`nod` / `shake_head`；
    - 动作跟回答一起给出（第 0 期选定的那种做法）。
- **刷机前**照规矩先比对设备上 ota_1 和源码的版本，先问主人。
- **主人要定：** 第 3、4、5、7 条。

### 第 3 期：要花钱的看、要确认的写

- **做什么：**
  - `look_now`（加次数上限）、`face_who`；
  - `memory_propose`、`person_note_propose`、`switch_propose`、`camera_propose`，加页面上的待确认列表；
  - 可选：流式解析，第一个块是文字就早一点开口。
- **主人要定：** 第 3、6 条。

### 第 4 期：外面的世界，和别的模式

- **做什么：**
  - `web_search`、`weather`；
  - 给工具表套一层 MCP 服务，给 Claude Code 调试或别的客户端用，只开放 R 级；
  - 陪伴、围观、字幕这些模式要用工具时，按模式给不同的工具子集；
  - `ratings_summary`。
- **主人要定：** 第 6、10 条。

---

## 5. 主人要定的事（附建议的默认值）

| # | 问题 | 建议默认 |
| --- | --- | --- |
| 1 | 查东西的那句话，能多等多久 | 每句最多查一轮；那句话说完到开口可以到约 5 s；整句期限 8 s，到期就不用查到的结果 |
| 2 | 查东西时 hello 怎么表示 | 只亮 think 的三个点，不说"我想想" |
| 3 | 对话里能不能直接写记忆和人物笔记 | 不能，只能提议，主人在页面上点同意才写 |
| 4 | 转头和追踪 | LLM 只在有人明确要求时转头，每 5 s 最多一次，不转身（除非明确说"转过去"）；转完 10 s 后追踪恢复原样（固件要改） |
| 5 | 音量 | 只在明确要求时调，范围 30–70，不存到下次开机 |
| 6 | 要花钱的工具 | `look_now` 第 3 期开，跟着识别开关走，每小时最多 20 次；`web_search` 第 4 期才做，单独开关，默认关 |
| 7 | 状态上行多久一次 | 每 30 s 一次，电量、充电、触摸、接近、追踪、音量变化时立刻发 |
| 8 | 别人的原话能给模型多少 | `recent_speech` 只给最近 10 分钟、最多 40 句，人名只给称呼，按人物卡的 `quote` 规则 |
| 9 | 工具能说起哪些人 | 所有已确认的人物卡（`retired` 不算），`tease: never` 的人附"不要调侃" |
| 10 | 要不要现在就做 MCP 服务 | 现在不做；工具表从第一天起按 MCP 的样子写，第 4 期再套 |
