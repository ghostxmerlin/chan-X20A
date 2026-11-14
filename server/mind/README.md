# mind（服务器端情景把控）

**当前角色说话与长期记忆**：见 [场景、长期记忆与角色说话](../../docs/memory-speech.md)。新增独立 `soul-memory`、版本化记忆、关注筛选、场景与方言设置；部署脚本同时更新四个服务。以下是原始时间线基础设施说明，现行设置由 `mind_settings` 最新版本管理，文件只作首次引导。

设计：`docs/superpowers/specs/2026-11-10-mind-design.md`；第 0 阶段计划：`docs/superpowers/plans/2026-11-10-mind-phase0.md`。

第 0 阶段只用规则：每分钟把 `hello` 库里的原始记录（`speech / tingwu / sounds / vision / sessions`，只读）整理成分钟包和强力元素，写进 `mind_packages`（每次重算产生新版本，旧版本保留）和 `mind_state`。不调用模型。

- **运行**：服务器上的临时单元 `soul-mind`（和耳朵的 `soul-spike` 一样由 `systemd-run` 启动，服务器重启后需重新部署）。用耳朵的 venv（需要 pymongo）。
- **部署**：`server/mind/deploy.sh`（`--dry-run` 只列出要拷的文件）。本机 `.env` 可选 `SOUL_MIND_DIR`（默认 `soul-mind`）、`SOUL_MIND_UNIT`（默认 `soul-mind`）。
- **设置**：`~/soul-mind/settings.json`，只在服务器上，含真实称呼；第一次部署时从 `settings.example.json` 复制。改完不用重启，下一分钟生效；要让历史也按新设置算，用重算。
- **重算**：在服务器 `~/soul-mind` 下 `~/soul-spike/.venv/bin/python -m mind.runner --settings settings.json --rebuild 2026-11-10T00:00 2026-11-11T00:00`（本地时间）。
- **接口**：只监听服务器 `127.0.0.1:52740`，令牌与耳朵相同；`/state`、`/timeline?from=&to=`（毫秒，≤24 小时）、`/minute?t=&version=`。Mac 上由监控页经 ssh 转发访问（`/mind.html`）。
- **密钥**：`MONGO_URI` 运行时从 hubmix 的 `~/aidea/agent/.env` 读，不进仓库。

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

错误：格式不对 400，没有这条 404，版本已被改过 409。**没有删除接口，删除只由主人手动维护数据库。本期没有任何提示读取这些集合。** 设计见 `docs/superpowers/specs/2026-11-13-backend-people-knowledge-memory-design.md` 第 3 节。

## 模式（第一步）

mind 是唯一改模式的地方（设计 `docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md`，计划 `docs/superpowers/plans/2026-11-14-hello-modes-step1-framework.md`）。模式表、预设表在 `mode_table.py`，纯状态机 `modes.py`，存储 `mode_store.py`，总闸和预设 `billing.py`，守门和钩子在 `runner.wire_modes`。

- **集合**：`hello.mode`（`_id: current`：当前模式、`seq`、`billing`、`since`、`by`、机器人 boot）；`hello.mode_log`（每个输入一行，带结果；设备输入按 `device + boot + seq` 去重，mind 重启后也认）。模式或总闸变了 `seq` 都加 1。
- **接口**：

| 方法 路径 | 说明 |
| --- | --- |
| `GET /mode` | `{snapshot, robot, log}`（log 最近 10 条，新的在前） |
| `GET /mode/seq` | `{seq}`，只读内存（ear 每秒问） |
| `POST /mode` | 页面：`{input, value?}`，来源 `page` |
| `POST /mode/input` | ear 转机器人：`{device, boot, seq, age_ms, source, input, value?}`，缺 `boot`/`seq` 400；`age_ms > 3000` 结果 `stale` |
| `POST /mode/robot` | ear 转 `/mouth` 事件：`{boot, conn, event: hello/applied/closed, applied_seq?}`；新 boot = 机器人重启 = 回脱口秀 |
| `POST /preset` | `{n: 0-9, source}`，回 `robot`（机器人那一半，第一步只由页面执行） |
| `POST /switches {name: billing, on}` | 总闸；`GET /switches` 多一项 `billing: {on, at}` |

  回复都带 `effects`（真正执行的效果；被后来的输入取代的说一轮不在里面，这时即使 `code` 是 ok 也什么都没说）；总闸关得不完整时带 `failed`（还开着或不确定的步骤名）。
- **钱**：进入脱口秀（机器人开机、切过来、从设置回来）= 提前写开 + 连续说开 + 家人声音关（本来就关就不写设置版本）；其他模式进入不花钱。离开脱口秀：提前写、连续说关，存货丢掉；任何切换都停正在说的。mind 重启不算进入（两个照旧从关开始）。
- **说话只在脱口秀**：`/say`、`/say/round`、`/say/play`，以及打开 `/say/prefetch`、`/say/auto`，不在脱口秀时 409 `{error: 只在脱口秀模式, code: only_talk}`；关掉永远可以；`/say/stop` 永远放行。机器人 / DualKey 在脱口秀里 forward 或单击 = 说一轮（今天的流程），先过守门：总闸关 `billing_off`、嘴不在 TTS `mouth_not_tts`、已有一轮在跑 `busy`、离上一轮开始（含页面的说一轮）不到 10 秒 `too_soon`；hello 正在说时单击 = 停。
- **总闸**（第一步 = 关掉 + 禁止再开 + 拦说话）：关 = 先把总闸记成关（`seq` 加 1，正在跑的付费钩子看到自己已过时），再关听悟、识别、纪要、情景、连续说、提前写，停正在说的；每一步都试，失败的写进 `failed`，再关一次就是重试。开只开总闸本身，什么都不恢复（"全开"是预设 9）。关着时：打开 `tingwu`/`eye`/`notes`、打开情景（`/switch`）、说话类接口、`/voice/try`、`/role/try` 一律 409 `{error: 计费关着, code: billing_off}`；关开关永远可以（预设 1 的页面那一半会发 `tingwu:false`/`eye:false`）。库里存的总闸值坏了（不是布尔）按关；mind 启动时总闸是关的，就再执行一遍关（上次没关干净的这次关掉）。
- **DualKey**：`/signal` 存下信号后，`kind: mode` 的方向、`kind: click` 的 single/double/long（= tap/double_tap/long）交给模式，按 `device + boot + seq` 去重、超过 3 秒的丢掉（`stale`），结果写进那条信号的 `result`。打分不受 3 秒规则影响。
- **不受总闸约束**：一次性命令行工具——`probe.py`（模型能力探测）、`runner.py --rebuild ... --fusion`（重算，模型调用只看情景开关）——由人手动跑，不经过模式和总闸。进程级的模型调用闸门是后一步。
