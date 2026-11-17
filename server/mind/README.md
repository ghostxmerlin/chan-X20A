# mind（服务器端情景把控）

**当前角色说话与长期记忆**：见 [场景、长期记忆与角色说话](../../docs/memory-speech.md)。新增独立 `soul-memory`、版本化记忆、关注筛选、场景与方言设置；部署脚本同时更新四个服务。以下是原始时间线基础设施说明，现行设置由 `mind_settings` 最新版本管理，文件只作首次引导。

设计：`docs/superpowers/specs/2026-11-10-mind-design.md`；第 0 阶段计划：`docs/superpowers/plans/2026-11-10-mind-phase0.md`。

第 0 阶段只用规则：每分钟把 `hello` 库里的原始记录（`speech / tingwu / sounds / vision / sessions`，只读）整理成分钟包和强力元素，写进 `mind_packages`（每次重算产生新版本，旧版本保留）和 `mind_state`。不调用模型。

- **运行**：服务器上的临时单元 `soul-mind`（和耳朵的 `soul-spike` 一样由 `systemd-run` 启动，服务器重启后需重新部署）。用耳朵的 venv（需要 pymongo）。
- **部署**：`server/mind/deploy.sh`（`--dry-run` 只列出要拷的文件）。本机 `.env` 可选 `SOUL_MIND_DIR`（默认 `soul-mind`）、`SOUL_MIND_UNIT`（默认 `soul-mind`）。
- **设置**：`~/soul-mind/settings.json`，只在服务器上，含真实称呼；第一次部署时从 `settings.example.json` 复制。改完不用重启，下一分钟生效；要让历史也按新设置算，用重算。
- **重算**：在服务器 `~/soul-mind` 下 `~/soul-spike/.venv/bin/python -m mind.runner --settings settings.json --rebuild 2026-11-10T00:00 2026-11-11T00:00`（本地时间）。
- **接口**：只监听服务器 `127.0.0.1:52740`，令牌与耳朵相同；`/state`、`/timeline?from=&to=`（毫秒，≤24 小时）、`/minute?t=&version=`。Mac 上由监控页经 ssh 转发访问（`/mind.html`）。
- **密钥**：`MONGO_URI` 运行时从 hubmix 的 `~/soul-spike/hello.env` 读，不进仓库。

## 库（第 1 期）

五个集合，全部只追加（每次修改是一条新版本，旧版本不动）：`people_cards`（人物卡）、`people_notes`（人物笔记）、`memory_items`（记忆）、`knowledge_items`（知识），另有 `voice_labels`（声纹标注，第 4 期才写）。

| 方法 路径 | 说明 |
| --- | --- |
| `GET /db/overview` | 各集合数量、状态、来源统计，以及已有设置/记忆的数量 |
| `GET /db/people` | 全部人物卡（含有效笔记数） |
| `GET /db/person?id=` | 一张卡、全部版本、笔记、相关记忆与知识 |
| `GET /db/memories?kind=&person=&topic=&q=&from=&to=&offset=&limit=` | 记忆列表（limit ≤ 100） |
| `GET /db/knowledge?kind=&person=&q=&offset=&limit=` | 知识列表 |
| `GET /db/item?c=&id=` | 任一条的版本历史（最多 50 版） |
| `POST /db/person`、`/db/person/note`、`/db/memory`、`/db/knowledge` | 新建或修改：`{id?, expected_version?, fields, reason?}`；恢复旧版：`{id, expected_version, restore_version}`。`reason` 截到 200 字 |

错误：格式不对 400，没有这条 404，版本已被改过 409。**没有删除接口，删除只由主人手动维护数据库。** 设计见 `docs/superpowers/specs/2026-11-13-backend-people-knowledge-memory-design.md` 第 3 节。

### 长期记忆阶段 A（2026-11-14）

评估和默认值见 `docs/superpowers/specs/2026-11-14-long-term-memory-assessment.md`。

- **整理**（`memory_extract.py`，跑在 `soul-memory` 里）：每天一次，把前一天的纪要和纪要下的原话、没有纪要的原话、情景段、对话模式的轮次、主人打分交给 luna，抽出一件件的事写进 `memory_items`（`origin = extracted`，带出处、`extract_key`、人和角色、置信、重要程度、敏感标记）。不送听悟。写入即生效；低置信的只在页面上看得到。
- **开关和上限**：库页面"最近变化"上的"记忆整理"开关（`hello.switches.memory_extract`，默认开）；每天的整理还要情景开着、长期记忆学习开着、当天整理花费不到 0.2 美元；回填一共不超过 2 美元。
- **主人改过的不再动**：对 / 错（停用，不删）/ 改（主人版本）/ 合并（`merged_into`）都写一条主人版本，以后整理只记"拦下"。
- **读**：`memory_recall` 是每条记忆当前版本的派生副本（可用 `python3 -m mind.memory_extract rebuild-recall` 重建）。对话模式和脱口秀用同一个回忆池（`recall.py`：现有日常记忆 + 库里的记忆）：对话每句最多 3 条；脱口秀每轮最多 1 条、不用孩子和隐私的、同一条 3 天内不再用。
- **回填**：`python3 -m mind.memory_extract backfill --from 2026-11-07 --to 2026-11-14 [--dry-run] [--lane live --yes-live]`，默认写 `replay` 道，只打印数量和花费。

| 方法 路径 | 说明 |
| --- | --- |
| `GET /db/changes?lane=&offset=&limit=` | 自动整理出的记忆（含主人后来的版本），按最近变化排 |
| `GET /db/sources?id=` | 一条记忆的出处（纪要、原话和录音位置、情景段、对话轮次……） |
| `GET /db/extract` | 记忆整理的开关、上限、花费、最近的任务 |
| `POST /db/memory/review` | `{id, expected_version, action: confirm / wrong / merge, into?, reason?}` |
| `POST /db/extract/switch`、`/db/extract/run` | `{on}`；`{day?}`（请后台现在整理这一天，默认今天，仍受开关和上限管） |

## 模式（第一步，1b：以 hello 为准）

**hello 自己切模式**（主人 2026-11-14 改的方向，计划 `docs/superpowers/plans/2026-11-14-hello-modes-step1b-robot-owned.md`），用的是和 `modes.py` 同一套规则（测试向量）。mind 只记 hello 报上来的模式，按报上来的"效果"开关自己的功能；计费开关（= 预设 9 / 0）和预设 0 / 9 仍在 mind。模式表、预设表在 `mode_table.py`，规则参照 `modes.py`，存储 `mode_store.py`（`report()`），计费开关和预设 `billing.py`，守门和钩子在 `runner.wire_modes`。

- **集合**：`hello.mode`（`_id: current`：hello 最近报的模式、mind 自己的 `seq`、`since`、`by`、hello 的 boot）；`hello.mode_log`（每次报告 / 每个输入一行，带结果）。hello 的报告按 `boot + seq`（hello 自己的编号，`device: robot.report`）去重；mind 的 `seq` 在模式或计费开关变了时加 1（付费钩子用它判断自己有没有被后来的变化取代）。
- **接口**：

| 方法 路径 | 说明 |
| --- | --- |
| `GET /mode` | `{snapshot, robot, log}`：snapshot = hello 报的模式 + 计费开关（上一次整体动作）；robot = `{connected, boot, conn, seq（hello 的报告编号）, at}`；log 最近 10 条 |
| `GET /mode/seq` | `{seq}`（mind 的编号，只读内存） |
| `POST /mode/report` | ear 转 hello 的 `mode_report`：`{boot, conn, age_ms, seq, mode, dir, host, return_to, by, input, effects}`。状态必须是规则能产生的（否则 400）；同一 `boot + seq` 只执行一次；比上一条旧的报告结果 `stale`、什么都不改；等了超过 3 秒（`age_ms`）的报告照样记模式，但只执行"关"（`talk_leave`、`prefetch_off`），停说和花钱的效果丢掉 |
| `POST /mode/robot` | ear：`{boot, conn, event: closed}`，hello 的 `/mouth` 断了（只认最近一次报告的那条连接） |
| `POST /mode/input` | ear 转 hello 的数字：`{device, boot, seq, age_ms, source, input: digit, value}`，缺 `boot`/`seq` 400；其他输入 409 `{code: on_hello}` |
| `POST /mode` | 页面：只有 `{input: billing, value}`；其他 409 `{error: mode is switched on hello, code: on_hello}`（页面切模式走观察台 → hello `/v1/control {target: mode}`） |
| `POST /preset` | `{n: 0-9, source}`：0 = 花费全关，9 = 花费全开（提前写、连续说只在脱口秀里开；回 `robot` = 机器人那一半，只由页面执行）；1–8 在 mind 上 `undefined_preset`（4–8 是 hello 自己切模式，1–3 空） |
| `POST /switches {name: billing, on}` | 计费开关：开 = 预设 9，关 = 预设 0（回复带 `robot`，机器人那一半由页面做）；`GET /switches` 多一项 `billing: {on, at}` |

  回复都带 `effects`（真正执行的效果）；有步骤没成时带 `failed`。
- **效果**（hello 报来，mind 执行）：`stop_speech` 停正在说的；`talk_leave` 提前写、连续说关（存货丢掉）；`talk_enter` 提前写开 + 连续说开 + 家人声音关（本来就关就不写设置版本）；`say_round` 说一轮（今天的流程），先过守门：不在脱口秀 `only_talk`、嘴不在 TTS `mouth_not_tts`、已有一轮在跑 `busy`、离上一轮开始（含页面的说一轮）不到 10 秒 `too_soon`；`prefetch_on` / `prefetch_off`。保险：报上来的模式不是脱口秀而提前写 / 连续说还开着，就关掉（不管带没带 `talk_leave`）。设置 5 分钟回去的计时在 hello 上（它报 `by: timeout`），mind 不再自己计时。重连后 hello 先补报一次当前状态（`input: null`、`by` = 上次切换的来源、没有效果），mind 只当作刷新。
- **说话只在脱口秀**（按 hello 报的模式）：`/say`、`/say/round`、`/say/play`，以及打开 `/say/prefetch`、`/say/auto`，不在脱口秀时 409 `{error: 只在脱口秀模式, code: only_talk}`；关掉永远可以；`/say/stop` 永远放行。
- **计费开关**（主人 2026-11-14 晚："计费开关是这样，启动默认全开，我就使用网页单点开关，或asr9 asr0来整体开关。其他奇怪的设定都取消。"）：
  - 只是"上一次整体动作"：开 = 预设 9（听悟、识别、纪要、情景开，嘴 TTS，在脱口秀里提前写 + 连续说开），关 = 预设 0（这些全关，停正在说的）。网页开关、口令 9 / 0 都是这两个动作。
  - **mind 每次启动 = 全开**（跑一遍预设 9 的服务器那一半；不在脱口秀就不开提前写、连续说）。计费值不存库，重启后总是开。
  - **不拦任何东西**：单个开关（听悟、识别、纪要、情景、提前写、连续说）随时能单独开关，说话类接口也不看它。拦说话的只有模式（不在脱口秀 `only_talk`）。
  - 关的每一步都试，失败的写进 `failed`，再按一次就是重试。
- **hello 开机补发的 9 不改开关**（2026-11-17）：hello 每次开机自己打开听觉、相机、M12（免费），再向 mind 补发一个 9（来源 `quick`，直到回 ok）。mind 把"这次开机（`boot`）还没有任何数字回过 ok 时来的 quick 9"认作开机补发的 9（`billing.Billing.power_on`，查 `mode_log`，mind 重启也认得）：只回 ok（toast `POWER ON: SWITCHES KEPT`），听悟、识别、纪要、围观、情景、嘴都保持库里原样。之后同一次开机里的 quick 9（快捷页"全开"）、ASR 的 9、网页的 9 照旧全开。来源写 `boot` 的数字（以后的固件可以这样标）永远算开机补发。两个罕见的边角：开机后第一次连上之前主人离线说的 9 也会被当作开机补发；开机 9 的 ok 回复如果半路丢了、hello 重发，重发的那次会当成主人的 9。
- **暂停**（2026-11-17，`pause.py`）：没人（M12 没看到脸、没听到认识的声音、主人没有任何明确操作，`pause.away_min` 分钟，默认 10）或今天所有付费项合计到了 `pause.cap_cny_per_day`（默认 20 元，按运行日志总账，美元 × `pause.usd_cny` 默认 7.2，80% 提醒）就暂停：听悟、围观会话（也不因新的话重开）、视觉爆发、识别、纪要、情景、记忆的 10 分钟窗口、提前写 / 连续说都不做。暂停是 `hello.switches {_id: pause}` 这份独立的文档，**开关一个都不改**；有人回来（任何一张脸、认识的声音、ASR 词 / 触摸 / 快捷页 / DualKey / 网页开关）就恢复（ear 自己看到脸或主人操作时当场解除它那一侧，mind 5 秒内解除全部）；超上限的暂停到北京时间 0 点、主人按 9（当天放行）或调高上限为止。每天的记忆整理没人时照常跑；到上限时所有模型调用都拒绝（和原来的每日上限一样）。`GET /switches` 多一项 `pause`（暂停与否、原因、从几点起、今天花了多少 / 上限）。旧的 `budget_usd_per_day`（只算 mind 自己的模型调用）不再读，下一次保存设置时去掉。
- **DualKey**：只打分。`/signal` 照旧存信号（打分、"这轮不行"；方向、单击也照存），不再切模式、不再停说。
- **一次性命令行**：一次性命令行工具——`probe.py`（模型能力探测）、`runner.py --rebuild ... --fusion`（重算，模型调用只看情景开关）——由人手动跑，不经过模式和计费开关。
