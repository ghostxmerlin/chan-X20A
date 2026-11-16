# hello 运行 / 审计日志：格式与查询

hello 的六个服务（ear / mind / eye / notes / memory / tts）把每件事记进 Mongo 库 `hello`。写入模块是 `server/common/hello_log.py`，读取模块是 `server/common/hello_log_query.py`（只用标准库，传入 pymongo 的 Database 即可）。

## 表

| 表 | 内容 |
| --- | --- |
| `log_YYYYMM` | 流水：一件事一条小记录 |
| `logbody_YYYYMM` | 正文：`{_id: 内容的 sha256, text}`，提示词、输入、输出全文，同一个月内按哈希去重 |

- 月份按北京时间（Asia/Shanghai）划分：11-30 23:59:59.999 在 `log_202611`，12-01 00:00:00.000 在 `log_202612`。正文表和它的流水同月。
- 永久保留，不删、不归档。
- 流水表索引：`at`；`(kind, at)`；`trace`；带 `partialFilterExpression {cost: {$exists: true}}` 的 `at`（总账用）。正文表只有 `_id`。其余筛选（服务、模型、by、模式）在时间范围里扫描。
- 流水 `_id` = `"{svc}:{run}:{seq}"`，补写重复写入会被忽略。

## 流水字段

| 字段 | 含义 |
| --- | --- |
| `at` | 毫秒时间戳（服务器墙钟） |
| `svc` | 哪个服务：ear / mind / eye / notes / memory / tts |
| `kind` | 类型，点分层级，如 `call.realtime.turn`、`gate.realtime`、`switch.set`、`link.open`（清单见下） |
| `trace` | 链路编号：同一件事的各步共用 |
| `seq` | 本服务进程内递增序号；配合 `run` 判断有没有断档 |
| `run` | 服务进程启动编号（每次启动新生成） |
| `ok` | 成功 / 失败；失败时 `err` 一句话 |
| `ms` | 耗时（有的话） |
| `model`、`usage` | 模型名、服务商原样返回的用量（token、音频秒数、字符数……） |
| `cost` | `{amount, cur: "USD"\|"CNY", est: true, basis}`：估算金额、币种、依据（价格表版本）；不花钱的事件没有这个字段；价格未知时 `amount: null`，用量照记 |
| `key` | 这次调用用的密钥指纹（开头 7 个字符，如 `sk-xTVl`），用于和服务商账单对照；从不记完整密钥 |
| `by` | 谁触发的：asr / digit / page / boot / rule / robot / mind / timer…… |
| `mode` | 当时 hello 的模式 |
| `sum` | 一行中文摘要（听到的话前 60 字、回答前 60 字、开关"听悟 开→关"） |
| `body` | 正文引用：`{system, context, input, output, …}` → 正文编号（用 `body()` 取全文）；没有大段内容时省略 |
| `ref` | 指向原件或业务表：录音 `session` + `pos`、照片路径、`realtime_turns.tid`、`mind_runs` 的 id 等 |
| `extra` | 该类型自己的字段。`svc.start`：`code_version`、`pid`、`args`；`svc.stop`：`reason`（sigterm / exit）；`svc.beat`：`counts`、`state`、`dropped`、`spool_errors`、`db_errors`、`backlog_lines` |

小内容（几十字的听到的话、一句回答）直接放 `sum` 也放正文，脚本只靠日志表就能拿到全文。

脱敏：键名含 key / token / secret / password / authorization 的值写成 `***`，URL 查询串里的 `token=`、`key=` 同样处理；图片、音频不存（`data:` URL 换成 `[image omitted, N bytes]`，原件用 `ref` 指向）；单条正文超过 4 MB 截断并带标记。

## kind 清单

- **服务**：`svc.start`、`svc.stop`、`svc.beat`（每分钟一次，带本分钟各类计数）、`svc.error`。
- **连接**：`link.open` / `link.close`（ear 每条机器人链路）、`link.upstream`（ear 到听悟、realtime、soul-tts 的上游连接）。
- **开关 / 模式 / 预设**：`switch.set`、`mode.input`（mind 的 `mode_log` 每行一条）、`mode.change`、`preset.apply`、`billing.set`、`robot.state`（机器人上报的开关）。
- **触发与闸门**：`trigger.*`（`trigger.burst`、`trigger.hush`、`trigger.greet`）、`gate.rescue`、`gate.realtime`（放行 / muted_quiet / muted_unclear / muted_repeat / muted_blind / muted_wait_look / rescue……及理由）、`filler.play`。
- **业务调用**（正文里存系统输入 / 背景 / 用户输入 / 输出全文）：
  - `call.eye`、`call.look`；
  - `call.fusion`、`call.segment`、`call.notes`；
  - `call.realtime.session`、`call.realtime.context`、`call.realtime.turn`、`call.realtime.search`；
  - `call.chat`、`call.say.*`（`call.say`、`call.say.speak`、`call.say.line`、`call.say.huyu`、`call.say.judge`、`call.say.check`）、`call.attention`；
  - `call.memory`、`call.memory.extract`、`call.memory.backfill`；
  - `call.tingwu`；
  - `call.tts`。
- **听到的话**：`heard.asr`（文字、session、pos），链路从它开始。
- 没有包在任何 scope 里的模型调用记成 `call.llm`。

## mind 一侧的记录（eye / notes / memory 同）

- **模型调用**都在 `mind/llm.py` 的 `chat()` 里记：`model`、`ms`、`ok`/`err`、`usage`、`cost`（`basis: "llm.PRICES 2026-11-15"`，按 `llm.PRICES` 标价估算）、`key`、`sum`（最后一条用户消息的最后 60 字）、正文 `system`（系统消息文本的列表）/ `input`（其余消息原样，图片已换掉）/ `output`（回复文字，或 tool_calls 的 JSON）。`kind` 和 `ref` 来自调用方：`call.eye` / `call.fusion` / `call.segment` / `call.notes` 带 `ref.minute`（纪要另有 `cid`，情景另有 `lane`、`prompt`）；`run_json` 的 `call.<step>` 带 `minute`、`lane`、`step`；`call.chat` 带 `ref.tid`（`chat_turns`）和 ear 传来的 `trace`。重试和第一次共用 `trace`，一轮脱口秀（各句 + 裁判）共用一个 `trace`。
- **`switch.set`**：`extra = {name, before, after}`，`by` = page / mode / extract……，`sum` 如 "听悟 开→关"；写库失败时 `ok: false` + `err`。`name` 有 tingwu、eye、notes、realtime、mouth（值是模式）、mind（情景）、memory_extract、say_prefetch（提前写）、say_auto（连续说）。
- **`mode.input`**：`by` = 来源，`mode` = 之后的模式，`extra` = `{input, value, from, to, code, device, boot, dev_seq, sid, seq, effects, toast}`。**`mode.change`**：`extra = {from, to, seq, input}`（`from`/`to` 是 mode、dir、host、return_to）。**`billing.set`**：`extra = {before, after}`。**`preset.apply`**：`extra = {n, code, toast, failed, robot}`，`ok` = 没有失败的步骤。一次输入和它引起的预设、开关变化共用一个 `trace`。
- **心跳** `svc.beat` 的 `state`：`switches` 表里每个开关的值（`*_status` 除外）加 `mind`（情景开关）。

## ear 一侧的记录

写入口是 `server/ear/ear_log.py`（hello_log 没部署时什么都不记，ear 照常跑）。价格表 `server/common/prices.py`（每项带 `BASIS`：来源网址 + 查询日期；查不到的是 `None`，记录里 `cost.amount: null`）。

- **链路** `link.open` / `link.close`：`extra = {link: mouth|signal|vision|hearing, conn（m3 / v4 / c12 / DualKey 设备名）, peer（ip:port）, boot, why, counts}`，`link.close` 的 `ms` 是连接时长。`counts`：mouth `{upstream_drops}`，signal `{signals}`，vision `{events, frames}`，hearing `{utterances, sounds, rx_bytes, session}`。token 不对、连接已满时只记一条 `link.open`，`ok: false`，`why` = bad_token / busy。`link.upstream`：ear 到 soul-tts 的连接起落，`extra = {to: "soul-tts", state: up|down, conn, code, drops}`（连不上时不重复记，只记起落）。
- **`robot.state`**：机器人报上来的状态变了才记，按机器人（`device` = 它的 source，模式为 `null`）分开，`extra = {part, device, before, after, …}`。`hearing` 由这台机器人还活着的听觉链路决定：重连时新旧链路重叠，旧的关掉不记 off，最后一条关掉才记 off（`extra.links` = 还连着的条数）。拒绝的连接（`link.open ok:false`）的 `extra.link` 按路由取 mouth / signal / vision / hearing，不记路径。`part`：`mode`（mode_report 里的 `{mode, dir, host, return_to}`，`by` = 报告的 by，另带 `conn`、`seq`、`input`）、`hearing`（机器人的听觉链路 on / off）、`m12`（online / offline）、`vision`（on / off，来自 vision 事件）。
- **`heard.asr`**：本地识别出有字的每一句。新的 `trace`，`by: asr`，`sum` = 前 60 字，正文 `input` = 全文，`ref = {session, pos, id}`，`extra = {lang, emotion, dur, speaker {band, name（只在 known 时）}, voice, self}`。同一个 `trace` 写回这句的事件（`speech` 表里也有 `trace`），并随 `/chat/heard` 的 `trace` 字段带给 mind。
- **`call.tingwu`**：每个听悟流一个 `trace`。`extra.state` = start / stop / error。stop 那条带 `usage = {audio_s}`、`ms`（流的时长）、`cost`（`prices.TINGWU_CNY_PER_HOUR` × 小时，按送上去的音频时长计费）、`ref = {session, stream, data_id}`、`extra.sentences`。
- **围观 realtime**（`model` = qwen-audio-3.1-realtime-flash，`key` = 指纹，从不记声音 id）：
  - `call.realtime.session`：`extra.state` = open（正文 `system` = 完整 instructions，`extra = {why, search, silence_ms, vad_threshold}`）/ close（`extra.why`，`ms` = 会话时长）/ connect_failed（`ok: false`）。`ref.session` = 本进程的会话编号。
  - `call.realtime.context`：每次 `session.update` 发 instructions 一条，正文 `system` 是全文（相同全文同月只存一份），`extra = {why: context|looks|seen|reply, chars}`。
  - `gate.realtime`：每个回答一次判定，`extra = {verdict: play|muted_quiet|muted_unclear|muted_repeat|muted_blind|muted_wait_look, reason, kind, done}`，`ref = {rid, session}`。
  - `gate.rescue`：被静音的「嗯」被救回，`extra = {outcome: asked|look_wait, texts}`。`trigger.hush`：说了闭嘴，围观关，`extra = {word, action}`。`trigger.greet`：主人出现 / 单击打招呼，新的 `trace`，`extra = {kind, pick}`。`filler.play`：预录垫话「我看看哈」，`sum` = 垫话文字，`extra = {kind, clip, audio_ms}`。
  - `call.realtime.turn`：每个回答一条（垫话、哔哔声不算）。`usage` 是服务商原样的用量，`cost = {amount: cost_cny(usage), cur: CNY, est, basis}`，`ms` = 发出回答到第一段音频，正文 `input = {heard（模型转写）, ear（ear 自己听到的句子）, prompt（招呼 / 救回 / 看图回答时 ear 发的文字）}`、`output` = 回答转写，`ref = {tid（realtime_turns）, session, rid}`，`extra = {kind, state, timings, audio_ms, heard_chars, reply_chars, tokens}`。`state` 是 error / stalled / no_done / robot_gone 时 `ok: false`。
  - `call.realtime.search`：回答里用了联网搜索（用量里的 `plugins.search.count`，或者出现名字带 search 的服务端事件），`usage = {count}`，`cost` 按 `prices.REALTIME_SEARCH_CNY_PER_CALL`。
  - 一轮的 `trace`（裁定 R17）：每一轮有自己的 `trace`，在它这一段开始时生成（服务端 `speech_started`；会话关着时被一句话唤起的第一段，用唤起会话的 ear 起音那一刻，但这个起音只在 5 秒内有效（裁定 R18）：超过 5 秒还没等到服务端的这一段，就作废，下一段从自己的 `speech_started` 算起，所以冷启动慢于 5 秒时，开头那句不会出现在 `extra.heard` 里；作废之后 ear 识别句引出的看图等待 / 爆发 / 闭嘴仍记在那句自己的 `heard.asr` trace 上）。这一轮的闸门、救回、看图等待 / 爆发、垫话和 `call.realtime.turn` 都用它。ear 自己听到了什么记在 `call.realtime.turn`（以及闸门判定时的 `gate.realtime`）的 `extra.heard = [{trace, text, at（毫秒时间戳）}]`：凡是到达时间落在 [这一段开始 − 0.3 秒, 写这条记录的时刻] 之内的 ear 识别句（最近 8 句里找）都列上，`trace` 是那句 `heard.asr` 的（`--db off` 时为 `null`，照样按文字列出）。**这是按时间找的候选，不是确定的归属**：两个 VAD 无法一一对上，相邻的一句可能也落在窗口里；正文 `input.ear` 就是这些候选的文字。
- **视觉爆发**：`trigger.burst`，`extra.action` = start / extend / refused / stop / end（`word` 是启动词或停止词，`why`；end 带 `looks、snaps、failures、duration_s、extended`），`ref.burst_id`。`call.look`：每张一条，`model`、`key`、`ms`、`usage`、`cost`（USD，`vision_burst.PRICE_USD_PER_M`）、正文 `system` / `input`（照片换成 `[image omitted, N bytes]`）/ `output`，`ref = {path（照片文件）, lid（vision_looks）, burst_id}`。一次爆发和它的每张识图共用触发那句的 `trace`。
- **心跳** `svc.beat` 的 `state`：`{tingwu, tingwu_running, realtime, realtime_state, mouth, robot_mode, burst, links {hearing, vision, mouth, signal}}`。
- **停止**：SIGTERM 由 ear 自己的事件循环处理：停止服务、各模块收尾，再在工作线程里记 `svc.stop {reason: sigterm}`；收尾超过 10 秒（或者第二次 SIGTERM）就记下 `svc.stop` 直接退出。

## tts 一侧的记录（soul-tts，hello 的嘴）

写入口是 `voice/tts_service/soul_tts/runlog.py`（不在本仓库；部署时 `server/tts/deploy.sh` 把 `server/common/hello_log.py`、`prices.py` 拷到服务器 `~/soul-tts/`，和 `soul_tts/` 包并排。没部署时什么都不记，soul-tts 照常说）。`MONGO_URI` 取环境变量，否则取 `SOUL_TTS_ENV_FILE`（服务器上 `~/soul-spike/hello.env`）里的同名变量；都没有就只落盘。落盘目录默认 `~/soul-spike/log-spool`（`SOUL_TTS_LOG_SPOOL` 可改），`code_version` 来自 `~/soul-tts/CODE_VERSION`。

- **`call.tts`**：一次合成一条——一次 `/v1/say`（流式输入也只算一条，不是一个片段一条）或一次 `/v1/synthesize`，在合成这一步结束时记（成功、失败、被取消都记；播放结果不在这条里）。
  - `model` = 实际出声的模型（`qwen-audio-3.1-tts-flash`；回退到绑定模型或 HTTP 时是那一个），`ms` = 从收到这句到合成结束，`key` = 百炼 key 指纹，`sum` = 前 60 字，正文 `input` = 全文、`system` = 音色的 instruction（有的话），`ref = {uid}`（soul-tts 的句子 id）。
  - `usage` = 服务商原样的用量（`input_tokens`、`output_tokens`、`characters`……，各次尝试的 token 相加）加上 `chars`（文字字数）、`audio_ms`（合成出的音频时长）。
  - `cost = {amount, cur: CNY, est: true, basis}`：按 token 计费，`input_tokens × prices.TTS_CNY_PER_M_IN + output_tokens × prices.TTS_CNY_PER_M_OUT`（元 / 百万 token）。服务商没报 token（例如中途取消、出错）时 `amount: null`，`chars` / `audio_ms` 照记。
  - `extra = {op: say|synthesize, state: done|failed|partial|cancelled, stream, voice（音色别名；直接给音色 id 时记 direct，从不记音色 id）, rate, timings, attempts [{kind, model, ok, warm, started_s, task_id, request_id, error}]}`。`failed`（一点声音都没出）和 `partial`（出了一部分声音后出错，已有的照常播）时 `ok: false` + `err`；`cancelled`（被打断 / 取消）不算错，`ok: true`。
  - `trace`：调用方在 `/v1/say` 的 JSON 里带 `trace`（1–64 个字母、数字、`. _ -`）就用它，接上调用方的链路；没带或不合格就新开一个。mind 的 soul-tts 客户端（`speak.TtsHttp` 的 `say` / `begin`）带上当前 scope 的 `trace`：「说一句」和写它的模型调用同一个，脱口秀的句子用这一轮的（`say_rounds.trace`），聊天用 ear 传来的。
- **心跳** `svc.beat` 的 `state`：`{player_mode, queue（排队的句数）, player（播放端名字）}`。
- **停止**：soul-tts 在主线程里用 `signal.signal` 处理 SIGTERM（抛 SystemExit 打断 HTTP 服务），不走 asyncio 事件循环，所以用 hello_log 默认的 `install_signal=True`：它排在 soul-tts 的处理前面，先记 `svc.stop {reason: sigterm}`（最多等 3 秒写完），再交给原来的处理退出。

## 判断断档

- 每个 `run` 从 `svc.start`（seq 1）开始，`seq` 连续；跳号只会因为真丢了。
- 每分钟一条 `svc.beat`。心跳缺失 = 服务没了或库断了；有心跳没有业务调用 = 开关关着、服务闲着。
- 故意停服务会记 `svc.stop`，它到下一次 `svc.start` 之间不算断档。

## 查询模块

```python
import sys; sys.path.insert(0, "server/common")
import hello_log_query as q
from pymongo import MongoClient
db = MongoClient(URI)["hello"]          # URI 见 hubmix 环境里的 Mongo 连接串
```

时间一律毫秒时间戳；月份和"天"按北京时间。

| 函数 | 作用 |
| --- | --- |
| `flows(db, start_ms, end_ms, *, kinds, kind_prefix, svc, trace, has_cost, limit=200, before_id)` | 区间 `[start, end)` 内的流水，按 `at` 倒序，可跨月；`limit` 最大 1000。翻页：把上一页最后一条的 `_id` 作为 `before_id` |
| `body(db, at_ms, body_id)` | 取正文全文，找不到返回 `None`；`at_ms` 用来定月份 |
| `chain(db, trace, around_ms)` | 同一 `trace` 前后各 1 天的全部记录，按 `at` 正序 |
| `ledger(db, "YYYY-MM-DD")` | 北京时间一天的花费，按 (svc, model, cur) 汇总：`{"by": [{svc, model, cur, amount, n, unknown_n}], "total": {"USD": x, "CNY": y}, "unknown_n": k}`；`amount` 为 `None` 的记进 `unknown_n` |
| `last_seen(db, svcs, now_ms)` | 每个服务在 `now_ms` 及以前的最新一条 `{at, kind, run}`（或 `None`）：最近 5 分钟任何类型（`at` 索引），否则最新的 `svc.beat/start/stop`（`(kind, at)` 索引），本月起最多往前 2 个月，命中即停 |
| `gaps(db, start_ms, end_ms, *, beat_ms=60000, now_ms=None)` | 断档：`{svc, run, from_ms, to_ms, why}`，`why` 是 `no_beat`（相邻心跳间隔超过 2 倍 `beat_ms`）或 `seq_jump`（`seq` 跳号）。边界规则见下 |

### gaps 的边界规则

- 起点前多读 2 倍 `beat_ms`：断档在区间前开始、在区间内结束的会报（`to_ms >= start_ms`）；区间前就已启动的 run 不算断档。
- 某服务最后一条心跳 / 启动记录不是 `svc.stop`，且离区间终点（`end_ms`，或更早的 `now_ms`）超过 2 倍 `beat_ms`：报一条 `no_beat`，`from_ms` 是那条记录，`to_ms` 是终点，即"服务挂了"。查询未来的区间时传 `now_ms` 以免把还没发生的时间算进去。
- 窗口里一条记录都没有的服务无从判断，不报。
- 区间内某 run 第一条记录若 `seq` 大于 1，只有同一窗口里能看到它前面的记录时才会按 `seq` 跳号处理。
- 时间跨度最多 60 个月，只查实际存在的月表；`flows` 的 `before_id` 找不到时返回空列表；`ledger` 里币种缺失的记在 `"?"` 下。

### 示例

```python
import time
now = int(time.time() * 1000)

# 最近一小时所有调用，带花费的
rows = q.flows(db, now - 3600_000, now, kind_prefix="call.", has_cost=True, limit=50)

# 某条的输出全文
r = rows[0]
text = q.body(db, r["at"], r["body"]["output"])

# 整条链
q.chain(db, r["trace"], r["at"])

# 今天花了多少
q.ledger(db, "2026-11-16")["total"]

# 过去一天有没有断档
q.gaps(db, now - 86400_000, now)
```

## 网页和 mind 接口

本机观察台的「日志」页（`web/monitor/public/log.html`，首页「日志」按钮）经 mind 的只读接口读这些表（带现有令牌，`server/mind/api.py`）：

| 接口 | 参数 | 返回 |
| --- | --- | --- |
| `GET /log` | `from, to`（默认最近 1 小时，最长 31 天）、`kind`（以 `.` 结尾当前缀，如 `call.`；否则精确）、`svc`、`trace`、`cost`（1 只要有花费的，0 只要没有的）、`limit`（默认 200，最大 1000）、`before`（上一页最后一条的 `_id`） | `{from, to, rows, more}`，正文只给编号 |
| `GET /log/body` | `at, id` | `{text}`；没有时 404 |
| `GET /log/chain` | `trace, at` | `{trace, rows}`：`chain()`，按时间正序 |
| `GET /log/ledger` | `day`（默认北京时间今天） | `{day, by, total, unknown_n}` |
| `GET /log/gaps` | `from, to`（同 `/log`） | `{from, to, gaps}`（`now_ms` = 服务器此刻） |
| `GET /log/last` | — | `{now, svcs: {ear…tts: {at, kind, run} \| null}}`：每个服务最后一条（`last_seen()`：最近 5 分钟内任何类型，没有再看最新的 `svc.beat/start/stop`，从本月往前最多 2 个月）；mind 缓存 60 秒，返回的 `now` 是那次读的时刻 |

`hello_log_query.py` 没部署在 mind 旁边时这些接口回 503，mind 照常跑。页面上：窗口里一条记录都没有的服务 `gaps()` 看不见，所以另有「各服务最后一条记录」，看「最近 …」时超过 2 分钟没有记录的服务标红（每 60 秒刷新）；`call.realtime.turn` / `gate.realtime` 的详情列出 `extra.heard` 的候选，点开各自的链路。正文每次点开现取，不缓存。页面上的时间和总账的默认日期一律按北京时间（+08:00）显示，与 Mac 的时区无关。

直接用 mongo shell 也行，例如某天的失败记录：`db.log_202611.find({ok: false, at: {$gte: 1794240000000}}).sort({at: -1})`。
