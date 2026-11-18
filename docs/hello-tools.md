# hello 工具约定（A1，2026-11-18）

> 工具 = hello 能调用的一项能力（查、看、说、动、改设置）。技能由千问调用，技能再调工具；工具也直接开放给千问的函数调用（主人 2026-11-18）。
> 能力盘点见 [hello-capabilities.md](hello-capabilities.md)；工具的权威登记在 [`config/tools/registry.json`](../config/tools/registry.json)，观察台「工具」页（`/tools.html`）直接读它。本文只写规矩。

## 1. 每个工具登记什么

| 字段 | 含义 |
|---|---|
| `name` | 英文小写加下划线，动词或名词短语（`review_period`、`turn_head`）。一经使用不改名，改了就是新工具。 |
| `title` | 中文短名，给人看（页面、日志）。 |
| `group` | 八类之一：`sense` 感知、`recall` 回顾与查询、`self` 解释自己、`world` 外面的世界、`express` 说与表达、`move` 动、`daily` 日常（提醒、日程、帮忙盯着）、`settings` 设置。 |
| `description` | **写给模型看的**一两句中文：什么时候用、不该什么时候用。函数调用时原样交给千问。 |
| `parameters` | 输入的 JSON Schema（`type: object`，`properties`，`required`）。只用千问 / OpenAI 函数调用都支持的子集：string（可带 `enum`、`maxLength`）、integer / number（可带 `minimum` / `maximum`）、boolean、array（`items` 为上面几种）。时间一律用字符串，见第 4 节。 |
| `returns` | 返回的 `data` 里有什么，一句话。 |
| `level` | `R` / `A` / `C`，见第 2 节。 |
| `runs_on` | 在哪执行：`mind`、`ear`、`robot`（经服务器到机器人的命令线）、`external`（外部服务）。 |
| `status` | `have` 已有接口，包一层就行 · `wrap` 代码里有，要先开接口 · `new` 要新做 · `live` 已接进工具框架并测过。 |
| `source` | 现在由哪段代码或哪个接口提供（`GET /switches`、`server/ear/web_search.py` …），`new` 的写计划。 |
| `latency` / `cost` | 大概多快、每次花多少（“无”或金额和计价依据）。 |
| `needs` | 前提：开关（`realtime` / `eye` / …）、模式（`crowd` …）、机器人在线、固件版本。 |
| `notes` | 其他要知道的（副作用、隐私、限制）。 |
| `example` | 一组能直接运行的参数（主人 2026-11-18：「试一下」默认带上，不用手填）；测试会检查它符合 `parameters`。 |

## 2. 三个级别

- **R 只读**：只看不改，随时可调，可以缓存几秒。付费的只读（看一眼、联网搜索）也是 R，但受各自开关、暂停和每日上限约束。
- **A 会被看到或听到**：让 hello 说话、出表情、转头、出字。不改任何保存的设置。每个工具有频率上限（写在 `notes` 里），主人说「别动 / 别说了」时全部停下。
- **C 要确认**：改设置、写记忆库、学人脸、花大钱、对外发东西。别人或 hello 自己发起时，工具**只生成一条提议**，主人在页面上（或口头）确认后才执行；**主人亲口要求的（认出是主人），就算已经确认**，直接执行（2026-11-18）。

## 3. 统一的返回

```json
{"ok": true, "data": {}, "error": null, "ms": 12, "cost": {"amount": 0.0004, "cur": "CNY"}, "source": "mind", "trace": "…"}
```

- 失败时 `ok: false`，`error = {code, message}`。`code` 只用这几种：`bad_args` 参数不对 · `not_available` 现在用不了（没接、没装） · `switch_off` 开关关着 · `paused` 没人暂停中 · `budget` 今天的钱用完了 · `robot_offline` 机器人不在线 · `timeout` 超时 · `refused` 主人或规则不让 · `failed` 其他错误。`message` 是一句中文，hello 可以照着说。
- `data` 只放模型需要的东西，长文本截断并注明总数（`{"items": […], "total": 37, "shown": 10}`）。
- 不把密钥、令牌、内部地址放进任何返回。

## 4. 时间、人和隐私

- 时间参数接受两种写法：绝对时间（`2026-11-18 09:00`，北京时间）或相对说法（`1小时前`、`今天上午`、`昨天`、`上周三`）。由工具框架统一解析，返回里同时给出解析后的起止时间，方便模型复述。
- 人用称呼（`主人`、记忆库里的名字或别名），框架负责换成 person id。
- **谁的话都能查**（主人 2026-11-18）。但敏感度规则照旧：标了 `child` / `private` 的内容只对主人讲；有别人在场时，hello 说出来之前要考虑场合（由技能负责）。

## 5. 日志、花费、测试

- 每次调用记一条运行日志，类别 `call.tool.<name>`，和触发它的那句话在同一条链上；付费的带 `cost`，进当天总账、受每日上限约束。
- 每个工具至少：一个离线测试（假数据，验证参数、返回、错误码），一个实机冒烟测试（真服务器 / 真机器人）。测过的把 `status` 改成 `live`。
- 给千问的工具列表由注册表导出（`name`、`description`、`parameters`），只导出 `status: live` 的；C 级导出的是“提议”版本。

## 6. 接口（B1 起）

- mind：`GET /tools` → 给模型的函数列表（只含 `live` 且已实现的；`?all=1` 含所有已实现的）和 `available`；`POST /tool/call {name, args, trace?, caller?}` → 第 3 节的返回（错误也在返回里，HTTP 仍是 200）。实现在 `server/mind/tools.py`（框架：查参数、计时、记日志）和 `server/mind/tool_impls.py`（各工具）。注册表随 mind 部署到 `~/soul-mind/tools/registry.json`。
- 观察台「工具」页：已实现的只读工具旁有「试一下」，填参数后经观察台转给 mind 调用，结果原样显示。
- 已接入（`live`）：`switches_status`、`mode_status`、`cost_today`（B1）；`scene_now`、`notes_search`、`review_period`（B2，时间词由 `server/mind/tool_time.py` 统一解析，一次最多 31 天）；`memory_search`、`knowledge_search`、`person_lookup`、`people_list`（B3，人名按称呼或别名找，记忆带“只对主人讲”标记）；`speech_search`、`recent_speech`、`recent_sounds`、`hello_said`（B4，谁的话都能查，按声纹认人，去掉 hello 自己的声音；`speech` 加了说话人 + 时间索引）；`why_last_turn`、`capabilities`（B5，读运行日志的判定链和这份注册表）；`who_is_here`、`look_now`、`web_search`、`say`、`stop_speaking`、`set_length`、`set_quiet`、`set_role`（L1，经耳朵的本机接口 `POST 127.0.0.1:52743/ask`，`server/ear/ask_api.py`，同一个令牌）；`live_data`（L2，CoinGecko / frankfurter.dev / wttr.in，免费）、`search_verify`（L2，千问搜索 + Google 搜索 + luna 对照，约 $0.019、38 秒）；`switch_propose`、`memory_propose`、`person_note_propose`（L3，C 级：`POST /tool/call` 带 `owner: true`（认出是主人亲口说的）才直接执行，否则进 `hello.proposals`，工具页「待确认」同意 / 不同意，`POST /proposal/decide`；24 小时作废；同样的提议只留一条）；`reminder_*`、`schedule_*`、`watch_*`（L4，`server/mind/daily.py`，每 10 秒检查，经耳朵 say 在 Docking 里说出来，没人在就攒着等人回来；时间词支持「10分钟后、明天早上8点、下周三下午3点」）；`semantic_search`（RAG，`server/mind/semantic.py`，DashScope text-embedding-v4 512 维，记忆 / 知识 / 纪要 / 片段 518 条首次回填 ¥0.014，之后每 10 分钟补新的；记忆照 `memory_search` 的规矩过滤）；`deep_research`、`research_status`（`server/mind/research.py`，aihubmix gpt-6.2-sol + web_search，每个任务最多 ¥2，做完 hello 有人时说出来；C 级）。
- 口头触发（L1）：「我们在聊天 / 我在看电视 / 你别插话」→ 少插话 30 分钟（「可以说话了」恢复）；叫了 hello 之后说「当个大姐姐，陪陪老人」→ 临时角色 2 小时（「做回你自己 / 恢复」结束）；安静只经工具，被叫就醒。
- `db_query`（L5）：只读，只能查 `DB_QUERY` 白名单里的集合和字段（`server/mind/tool_impls.py`），记忆和人物走各自的工具。
- 千问用工具（L6）：开关 `realtime_tools`（工具页，默认关），开着时 Docking 会话带上 `GET /tools` 的工具（去掉 `say`、`stop_speaking`、`search_verify`、`set_voice`）；千问发起调用 → 先放「我查查」→ 耳朵经 `POST /tool/call`（caller `qwen`，C 级只成提议）→ `function_call_output` → 千问接着回答；一句话最多连用 3 次；开着时耳朵自己的「搜一下」关键词不再触发（不重复搜）。2026-11-18 实测模型支持（`live_data` 往返一次）。

