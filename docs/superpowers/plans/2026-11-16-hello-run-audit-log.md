# hello 运行与审计日志 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 hello 服务器上的每次运行事件和业务调用都写进 Mongo `hello` 库里统一的流水账，能在网页上翻，也能用脚本查。

**Architecture:** 一个共享写入模块 `server/common/hello_log.py`。部署时分别拷进 ear、mind、tts 三处。调用方把记录丢进内存队列就返回；后台线程先追加到本机文件，再批量写进按月分的 `log_YYYYMM`（流水）和 `logbody_YYYYMM`（正文，按 sha256 去重）。读取侧是 `server/common/hello_log_query.py`，mind 的只读接口和网页监控的新页面都用它。

**Tech Stack:** Python 3.11（服务器 venv `~/soul-spike/.venv`，已有 pymongo）、unittest、Mongo 6.0；网页是 Node（`web/monitor`），测试用 `node --test`。

**Spec:** `docs/superpowers/specs/2026-11-16-hello-run-audit-log-design.md`（先读它，尤其是 §7 和 §9）

## Global Constraints

- 只记 hello 的服务：ear（soul-spike）、mind（soul-mind）、eye（soul-eye）、notes（soul-notes）、memory（soul-memory）、tts（soul-tts）。`svc` 取值固定为 `ear / mind / eye / notes / memory / tts`。
- 表名：流水 `log_YYYYMM`，正文 `logbody_YYYYMM`；按 Asia/Shanghai 划月；永久保留，不删也不归档。
- 流水表只建 4 个索引：`at`；`(kind, at)`；`trace`；带 `partialFilterExpression {cost: {$exists: true}}` 的 `at`。正文表只有 `_id`。
- 每条流水的 `_id` = `"{svc}:{run}:{seq}"`。补写重复写入要能被安全忽略（幂等）。
- 写日志绝不抛异常，也绝不阻塞调用方：`emit()` 在调用方线程里只做组装和入队。
- 写日志不得调用任何付费服务。
- 脱敏：键名含 `key / token / secret / password / authorization`（不分大小写）的值一律写成 `"***"`。URL 查询串里的 `token=`、`key=` 同样处理。密钥只记指纹 `key_fp()`：开头 7 个字符。
- 不存图片和音频。消息里 `data:` 开头的 URL 换成 `"[image omitted, N bytes]"`，原件用 `ref` 指向。
- 单条正文超过 4 MB 时截断，并加标记 `"…[truncated, N bytes total]"`。
- 本机落盘目录：`~/soul-spike/log-spool/`。文件名 `<svc>-YYYYMMDD.jsonl`，进度文件 `<svc>-YYYYMMDD.offset`。全部入库后保留 7 天再删。
- 测试用 unittest：`python3 -B -m unittest tests.test_<name> -v`，在仓库根目录跑。测试里不连 Mongo、不连网，一律用假的 db 对象。
- 测试数据里不得出现家人或孩子的真实名字（仓库规矩）。
- 不提交、不推送 git，除非主人开口。每个任务结束时测试全绿，并看一遍 `git diff --stat`。
- 部署前停下来问主人（Task 8）。
- `voice/tts_service` 不在任何 git 仓库里：改之前先把整个目录打包备份到 `backups/tts_service-before-log-YYYYMMDD.tgz`。

## Review Focus

1. **Mongo 只是变慢、没断**：每批写入都要 5 秒超时。写不进去就留在本机文件里待补写。`emit()` 的耗时始终在 1 毫秒以内（Task 1 测试 `test_emit_fast_when_db_slow`）。
2. **消息里夹着 base64 图片**：eye 和 look 的 `messages` 里带着整张 JPEG。正文里不能出现 base64 原文（Task 1 测试 `test_data_url_stripped`）。
3. **月份交界**：北京时间 11-30 23:59:59.999 的记录要进 `log_202611`；12-01 00:00:00.000 的进 `log_202612`。它的正文也进同月的正文表（Task 1 测试 `test_month_boundary_beijing`）。
4. **落盘目录不可写或磁盘满**：照样直接写 Mongo，失败次数记进心跳，不抛异常（Task 1 测试 `test_spool_unwritable_still_ships`）。
5. **重启后补写两次**：同一行只入库一次（Task 1 测试 `test_replay_idempotent`）。

---

### Task 1: 写入模块核心（入队、脱敏、正文去重、先落盘再入库、补写）

**Files:**
- Create: `server/common/hello_log.py`
- Test: `tests/test_hello_log.py`

**Interfaces:**
- Produces:
  - `init(svc: str, *, db=None, db_uri: str|None=None, spool_dir: str|Path, code_version: str="", args: dict|None=None, clock_ms=None) -> Logger`：每个进程只调一次。`db` 是给测试注入的假库。
  - `emit(kind: str, **fields) -> None`：模块级函数，转给当前的 Logger；没调过 `init` 时什么也不做。`fields` 按 spec §7.3 的信封字段取值，`body` 是 `dict[str, str|list|dict]`，值可以是文本，也可以是 JSON 结构。
  - `key_fp(key: str|None) -> str|None`
  - `month_of(at_ms: int) -> str`：返回 `"YYYYMM"`，按 Asia/Shanghai。
  - `redact(obj) -> obj`
  - `Logger.flush(timeout_s: float=5) -> bool`：测试和退出时用。
  - `Logger.stats() -> dict`：`queued / shipped / dropped / spool_errors / db_errors / backlog_lines`

- [ ] **Step 1: 写失败的测试** `tests/test_hello_log.py`，`sys.path` 加入 `server/common`，用 `FakeDB`（字典里存 collection，记录 `insert_many(ordered=False)`、`update_one(upsert)`、`create_index` 的调用）：
  - `test_emit_writes_flow_and_body`：`emit("call.eye", body={"system": "S", "output": "O"}, sum="x")` 后调 `flush()`，`log_202611` 里有 1 条，`_id` 是 `"eye:<run>:1"`，`body` 是 `{"system": <sha>, "output": <sha>}`；`logbody_202611` 里有 2 条，`text` 分别是 `"S"` 和 `"O"`。
  - `test_body_dedup_within_month`：同一个 system 文本 emit 3 次，正文表里只有 1 条。
  - `test_redact_keys_and_url`：`{"api_key": "sk-abc", "nested": {"Authorization": "Bearer x"}, "url": "wss://h/p?token=abc&x=1"}` 变成 `***`，URL 变成 `token=***&x=1`。
  - `test_key_fp`：`key_fp("sk-xTVl1234567")` 是 `"sk-xTVl"`，`key_fp(None)` 是 `None`。
  - `test_data_url_stripped`：messages 里的 `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+"A"*5000}}` 进正文后变成 `"[image omitted, 5023 bytes]"`，正文里搜不到 `"base64,"`。
  - `test_month_boundary_beijing`：`at=1796054399999`（北京时间 2026-11-30 23:59:59.999）进 `log_202611`，`at=1796054400000` 进 `log_202612`，它们的正文也分别进同月的正文表。
  - `test_spool_then_ship`：`flush()` 后 `<svc>-YYYYMMDD.jsonl` 有 1 行，`.offset` 等于文件长度。
  - `test_db_down_then_replay`：假库的 `insert_many` 抛异常；emit 2 条，`flush()` 返回 False，文件有 2 行、offset 是 0。换成正常的假库后用同一目录重新 `init`，开始时补写，2 条入库，原 `_id` 和 `at` 不变。
  - `test_replay_idempotent`：同一份落盘文件补写两次，只入库一次（假库对重复 `_id` 抛 `BulkWriteError` 那种 11000 错误，写入方当成已入库处理）。
  - `test_emit_fast_when_db_slow`：假库每次写入 `sleep(2)`，连续 100 次 `emit` 总共不到 50 毫秒。
  - `test_spool_unwritable_still_ships`：`spool_dir` 指向只读目录，`flush()` 后照样入库，`stats()["spool_errors"] >= 1`。
  - `test_big_body_truncated`：5 MB 的文本存成 4 MB 加截断标记。
  - `test_indexes_created_once_per_month`：同月写两批，`create_index` 只在这个月第一次写时调用 4 次。
  - `test_emit_never_raises`：传进不能 JSON 化的对象（比如 `object()`），不抛异常，那个字段存成 `repr`。
- [ ] **Step 2: 跑测试，确认失败**
  Run: `python3 -B -m unittest tests.test_hello_log -v`，Expected: `ModuleNotFoundError: hello_log`
- [ ] **Step 3: 实现** `server/common/hello_log.py`
  - 内存队列 `queue.Queue(maxsize=20000)`，一个后台守护线程。
  - 线程循环：取一批（最多 500 条，或等 0.5 秒），追加到当天的落盘文件，并 `flush` 和 `os.fsync`；然后正文按 `{_id: sha}` 用 `$setOnInsert` 做 upsert，流水 `insert_many(ordered=False)`，批写超时 5 秒（`MongoClient(serverSelectionTimeoutMS=5000, socketTimeoutMS=5000)`）；成功后写进度文件。
  - 落盘的每行带上该条流水和它的正文文本，补写时不必再算哈希。
  - `run` 是进程启动时生成的 8 位十六进制随机串；`seq` 从 1 开始递增，emit 时就分配好。
  - 正文去重：进程内 LRU 缓存最近 5000 个 sha，命中时只引用、不再 upsert。
- [ ] **Step 4: 跑测试，确认通过**
  Run: `python3 -B -m unittest tests.test_hello_log -v`，Expected: 全部 OK
- [ ] **Step 5: 检查点**：`git diff --stat` 只多出这两个文件。

### Task 2: 链路、范围、心跳、起停

**Files:**
- Modify: `server/common/hello_log.py`
- Test: `tests/test_hello_log.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `init / emit / Logger`
- Produces:
  - `new_trace() -> str`：12 位十六进制。
  - `scope(kind: str|None=None, *, trace: str|None=None, **fields)`：上下文管理器，用 `contextvars` 实现。范围内的 `emit` 会自动带上没写的 `trace` 和 fields；`current() -> dict` 返回当前范围。
  - `Logger.set_beat_state(fn: Callable[[], dict])`：心跳时调用，拿各开关的当前状态。
  - 心跳：每 60 秒 emit 一次 `svc.beat`，`extra = {counts: {kind: n}, dropped, spool_errors, db_errors, backlog_lines, state: fn()}`。
  - `init` 时 emit 一次 `svc.start`，带 `{code_version, args: redact(args), pid}`；收到 SIGTERM 或进程退出（`atexit`）时 emit `svc.stop`，带 `{reason: "sigterm"|"exit"}`，然后 `flush(3)`。

- [ ] **Step 1: 写失败的测试**
  - `test_scope_fills_trace_and_kind`：`with scope("call.notes", trace="t1", ref={"minute": 5}): emit(None, sum="a")` 写出的那条 `kind="call.notes"`、`trace="t1"`、`ref={"minute":5}`；同一个 `emit` 里显式传的字段优先。
  - `test_scope_nested_and_thread_isolated`：嵌套时里层覆盖外层；另一个线程里 `current()` 是空的。
  - `test_beat_counts_and_state`：用注入的 `clock_ms` 推进 60 秒，调用 `Logger._beat_once()`，写出 `svc.beat`，`counts` 里有 `{"call.eye": 2}`，`state` 等于 `fn()` 的返回值。
  - `test_start_record`：`init` 后第一条是 `svc.start`，`seq=1`，`args` 里的 `token` 已脱敏。
  - `test_stop_on_sigterm`：直接调 `Logger._on_stop("sigterm")`，最后一条是 `svc.stop`，`reason="sigterm"`。
- [ ] **Step 2: 跑测试，确认失败**（`AttributeError: scope`）
- [ ] **Step 3: 实现**。SIGTERM 处理器先记录、再调用原来的处理器；如果原来是默认处理器，就 `sys.exit(0)`，不吞掉 systemd 的停止信号。
- [ ] **Step 4: 跑测试，确认通过**
- [ ] **Step 5: 检查点**

### Task 3: 查询模块与格式说明

**Files:**
- Create: `server/common/hello_log_query.py`
- Create: `docs/hello-log.md`（字段、kind 清单、示例查询，中文；内容对照 spec §7.3 和 §7.4）
- Test: `tests/test_hello_log_query.py`

**Interfaces:**
- Consumes: Task 1 的表结构和 `month_of`
- Produces:
  - `flows(db, start_ms: int, end_ms: int, *, kinds: list[str]|None=None, kind_prefix: str|None=None, svc: str|None=None, trace: str|None=None, has_cost: bool|None=None, limit: int=200, before_id: str|None=None) -> list[dict]`：可以跨月，按 `at` 倒序；`limit` 最大 1000。
  - `body(db, at_ms: int, body_id: str) -> str|None`
  - `chain(db, trace: str, around_ms: int) -> list[dict]`：在 `around_ms` 前后各 1 天里按 trace 查，按 `at` 正序。
  - `ledger(db, day: str) -> dict`：`day` 格式 `"YYYY-MM-DD"`，北京时间。返回 `{"by": [{svc, model, cur, amount, n, unknown_n}], "total": {"USD": x, "CNY": y}, "unknown_n": k}`。
  - `gaps(db, start_ms, end_ms, *, beat_ms=60000) -> list[dict]`：返回 `{svc, run, from_ms, to_ms, why: "no_beat"|"seq_jump"}`；两次心跳之间如果有 `svc.stop` 加 `svc.start`，就不算断档。

- [ ] **Step 1: 写失败的测试**（用 Task 1 的 FakeDB 加上 `find` 和 `aggregate`，或者直接在内存里过滤）：
  - `test_flows_cross_month`：11-30 和 12-01 各 1 条，查询区间覆盖两天时都能拿到，按时间倒序。
  - `test_flows_filters`：按 `kind_prefix="call."`、`svc`、`has_cost=True` 都筛得对。
  - `test_chain_orders_by_time`
  - `test_ledger_sums_by_currency`：USD 0.01、USD 0.02、CNY 0.004、一条 `amount=None`，结果是 `total={"USD":0.03,"CNY":0.004}`，`unknown_n=1`。
  - `test_gaps_no_beat_and_seq_jump`：心跳缺 3 分钟，报一条 `no_beat`；seq 从 10 跳到 15，报一条 `seq_jump`。
  - `test_gaps_ignores_intentional_stop`
- [ ] **Step 2: 跑测试，确认失败**
- [ ] **Step 3: 实现，再写 `docs/hello-log.md`**
- [ ] **Step 4: 跑测试，确认通过**
- [ ] **Step 5: 检查点**

### Task 4: mind 侧接入（模型调用、开关、模式、预设）

**Files:**
- Modify: `server/mind/llm.py`（`chat()`）
- Modify: 调用方，用 `hello_log.scope(...)` 包住调用，kind 固定如下：`server/mind/eye.py:224,230` → `call.eye`；`fusion.py:144,150` → `call.fusion`；`segments.py:68` → `call.segment`；`notes.py:225,231` → `call.notes`；`speak.py:185` → `call.say.speak`；`chat.py:594` → `call.chat`；`model_calls.py` 的 `run_json` → `call.` 加上 `step`（say / memory / attention / round 等），覆盖 say_round、scene_speech、speech_generation、memory_worker、memory_extract、attention。`ref` 带各自的定位，比如 minute、tid、run_id。
- Modify: 开关写入点：`server/mind/runner.py:152-165`（`set_switch / set_mind / set_mouth`）、`server/mind/api.py` 里 `/switches` 的写入（约 690–708 行）、`server/mind/memory_extract.py:772`（`set_switch`）、`server/mind/mode_store.py`（每行 `mode_log` 同时 emit 一条 `mode.input`；模式真正变化时 emit `mode.change`）、`server/mind/billing.py:170`（`apply_preset` 结束时 emit `preset.apply`）
- Modify: 4 个入口各调一次 `hello_log.init`：`runner.py`（svc `mind`）、`eye_runner.py`（`eye`）、`notes_runner.py`（`notes`）、`memory_worker.py`（`memory`）。`spool_dir=~/soul-spike/log-spool`，`db_uri` 用 hello.env 里的 `MONGO_URI`，`code_version` 读 `~/soul-mind/CODE_VERSION`（Task 8 生成），`set_beat_state` 传一个读 `switches` 表加 `store.enabled()` 的函数。
- Test: `tests/test_mind_log.py`，以及现有的 `tests/test_mind_*.py` 全部要继续通过

**Interfaces:**
- Consumes: Task 1/2 的 `init / emit / scope / key_fp`
- Produces:
  - `llm.chat` 返回前 emit 一条：kind 取当前 scope，没有就是 `"call.llm"`；`model=body["model"]`、`ms`、`ok=err is None`、`err`、`usage`、`cost={"amount": cost_usd(model, usage), "cur": "USD", "est": True, "basis": "llm.PRICES 2026-11-15"}`（价格不认识时 `amount=None`）、`key=key_fp(key)`、`body={"system": [system 消息的文本], "input": [其余消息], "output": 回复内容或 tool_calls 的 JSON}`、`sum`（用户消息最后 60 个字）。
  - 开关事件 `switch.set`：`extra={name, before, after}`，`by` 是来源（page / mode / boot / asr / rule / extract）。`before` 在写之前读出来。
  - `mind/__init__` 不改；mind 包内用 `import hello_log`（运行时 `~/soul-mind` 在 sys.path 上，测试时 `mind_fixtures.py` 把 `server/common` 加进 sys.path）。

- [ ] **Step 1: 写失败的测试** `tests/test_mind_log.py`：在 `mind_fixtures.py` 里加 `server/common` 进 sys.path，并给一个 `capture_log()` 帮手函数（用假 db `init`，返回已写的流水列表）。
  - `test_llm_chat_logs_call`：把 `urllib.request.urlopen` patch 成返回固定 JSON（带 usage），`with scope("call.eye", trace="t"): llm.chat(...)`。之后有一条 `call.eye`：`model`、`cost.amount` 等于 `cost_usd` 算出的值，`key="sk-xTVl"`，正文里有 system 和 output。
  - `test_llm_chat_logs_error`：urlopen 抛 `HTTPError` 时记 `ok=False`、`err` 以 `"HTTP"` 开头。
  - `test_switch_set_logs_before_after`：用 FakeDB 的 Ctx 调 `set_switch("tingwu", False)`，之前是 True，记成 `before=True, after=False, by="mode"`。
  - `test_preset_apply_logged`：用现有 `test_mind_billing` 的夹具跑 `apply_preset(0, …)`，有一条 `preset.apply`，`extra.n=0`、`extra.failed=[]`。
  - `test_mode_input_logged`：`mode_store` 处理一次数字输入，有 `mode.input`。
- [ ] **Step 2: 跑测试，确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑全部 mind 测试**
  Run: `python3 -B -m unittest discover -s tests -p 'test_mind*.py'`，Expected: OK（数量不少于改动前；先跑一次记下基线）
- [ ] **Step 5: 检查点**

### Task 5: ear 侧接入（连接、听到的话、听悟、围观、视觉爆发、价格）

**Files:**
- Modify: `server/ear/ear_server.py`：启动时调 `init("ear", …)`（参数同 Task 4，`code_version` 读 `~/soul-spike/CODE_VERSION`）；`:1039/1046/1054/1059/1068/1075/1083/1107` 这几处连接打印旁边各 emit `link.open / link.close`，带 `{link: mouth|signal|vision|hearing, conn, boot, peer, why, counts}`；`:433/408` 听悟开始和结束时 emit `call.tingwu`（结束那条带音频秒数和花费）。
- Modify: `server/ear/hello_db.py:102`（写 speech 时）emit `heard.asr`：带 `new_trace()`、`sum=文字`、`ref={session, pos}`；同一个 trace 存到这句话的事件上，供后面沿用。
- Modify: `server/ear/realtime_link.py`：`:951` 会话打开时 emit `call.realtime.session`，正文 system 是完整的 instructions；`:1002/1130/1151/1269` 每次 `session.update` 发送 instructions 时 emit `call.realtime.context`，正文同上，靠去重控制体积；`:906` 关闭时 emit `call.realtime.session`，带 `{state: "close", why}`；`_judge`（`:1290`）每次判定 emit `gate.realtime`，带 `{verdict, reason}`；`_try_rescue`（`:1370`）emit `gate.rescue`；`_hush`（`:1395`）emit `trigger.hush`；`_finish_turn`（`:1569`）emit `call.realtime.turn`：用量、`cost={"amount": cost_cny(...), "cur":"CNY", ...}`、正文 input 是模型转写加 ear 自己的识别，output 是回答转写，`ref={tid}`；预录垫话播放时 emit `filler.play`。trace 沿用触发这一轮的那句 `heard.asr`。服务端事件里如果出现联网搜索（用量里的搜索计数，或者搜索相关的事件），就另外 emit 一条 `call.realtime.search`，带 `cost`（`prices.REALTIME_SEARCH_CNY_PER_CALL`）。
- Modify: `server/ear/mouth_link.py:218`（`note_report`）：机器人上报的听觉、相机、M12、模式状态有变化时 emit `robot.state`，带 `{before, after}`，只在变化时记。
- Modify: `server/ear/vision_burst.py`：`start / ask / stop`（`:198/224/229`）emit `trigger.burst`；`dashscope_look` 每次调用 emit `call.look`，正文是 system 和 output，图片按全局约束替换，`ref={frame 路径, lid}`。
- Modify: `server/ear/mouth_link.py`：`chat_heard` 往 mind 发的 body 里加 `"trace"` 字段（mind 的 `/chat/heard` 收到后用 `scope(trace=…)` 包住这一轮）。
- Create: `server/common/prices.py`：`TINGWU_CNY_PER_HOUR`、`REALTIME_SEARCH_CNY_PER_CALL`、`TTS_CNY_PER_10K_CHARS`，每项带 `BASIS = {name: "来源网址 + 查询日期"}`。实现前用网页查 DashScope 官网当前标价；查不到的写 `None`，日志里记 `amount=None`。
- Test: `tests/test_ear_log.py`，以及现有的 `tests/test_realtime_link.py`、`test_vision_burst.py`、`test_hello_db.py`、`test_mouth_link.py` 全部要继续通过

**Interfaces:**
- Consumes: Task 1/2
- Produces: 上面列出的 kind，字段与 `docs/hello-log.md` 一致。

- [ ] **Step 1: 写失败的测试** `tests/test_ear_log.py`（用 `capture_log()` 的同款帮手）：
  - `test_realtime_turn_logged_with_cost`：用 `test_realtime_link.py` 现有的假 WebSocket 夹具跑完一轮，有 `call.realtime.turn`，`cost.cur="CNY"`，`cost.amount` 等于 `cost_cny(usage)`，trace 等于触发它的那句 `heard.asr` 的 trace。
  - `test_gate_mute_logged`：喂一句"嗯"类回答，有 `gate.realtime`，`extra.verdict` 以 `"muted_"` 开头。
  - `test_instructions_dedup`：同样的 instructions 发 3 次，正文表里 1 条，流水 3 条。
  - `test_look_logged_without_base64`：`dashscope_look` 用假 urlopen，有 `call.look`，正文里没有 `"base64,"`。
  - `test_link_open_close_logged`：用现有 ear wiring 测试的方式连一次 vision 再断开，有一对 `link.open / link.close`，`extra.link="vision"`。
  - `test_tingwu_cost`：一段 60 秒的听悟结束，`cost.amount` 等于 `prices.TINGWU_CNY_PER_HOUR/60`（价格是 None 时等于 None）。
  - `test_robot_state_only_on_change`：同一个状态报两次、换一个状态报一次，总共只有 2 条 `robot.state`。
- [ ] **Step 2: 跑测试，确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑 ear 相关的全部测试**
  Run: `python3 -B -m unittest tests.test_ear_log tests.test_realtime_link tests.test_vision_burst tests.test_hello_db tests.test_mouth_link -v`，Expected: OK
- [ ] **Step 5: 检查点**

### Task 6: soul-tts 自己记录

**Files:**
- 先备份：`tar czf backups/tts_service-before-log-$(date +%Y%m%d).tgz -C .. voice/tts_service`（在 `chan-X20A` 根目录执行；`..` 指 `~/soul`）
- Modify: `../voice/tts_service/soul_tts/server.py`（或者实际处理 `/v1/say` 的模块）：启动时调 `init("tts", …)`；每次合成结束 emit `call.tts`：`model`（声音和模型名）、`ms`、`usage={chars, audio_ms}`、`cost`（`prices.TTS_CNY_PER_10K_CHARS`）、`key=key_fp(DASHSCOPE_API_KEY)`、正文 input 是全文、`ref={uid}`；出错时 `ok=False`。流式合成按"一次 say 一条"记，不是一个片段一条。
- Modify: `server/tts/deploy.sh`：同步时把 `server/common/hello_log.py` 和 `prices.py` 拷到 `~/soul-tts/`。
- Test: `../voice/tts_service/tests/test_log.py`，以及现有的 `../voice/tts_service/tests/` 全部要继续通过

**Interfaces:**
- Consumes: Task 1/2，Task 5 的 `prices.py`

- [ ] **Step 1: 写失败的测试** `test_say_logs_call_tts`：用 `fakes.py` 里的假引擎合成一句 12 个字的话，有 `call.tts`，`usage.chars=12`，`ok=True`；`test_say_error_logged`：引擎抛异常时记 `ok=False`。
- [ ] **Step 2: 跑测试，确认失败**
  Run: `cd ../voice/tts_service && python3 -B -m unittest discover -s tests -v`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试，确认通过**
- [ ] **Step 5: 检查点**：备份包已存在，`voice/tts_service` 的改动列成清单写进 Task 8 的部署记录。

### Task 7: mind 只读接口 + 网页"日志"页

**Files:**
- Modify: `server/mind/api.py`：加 4 个 GET，全部走现有的令牌检查：
  - `/log`：参数 `from, to, kind, svc, trace, cost, limit, before`，返回 `flows()` 的结果，正文只给编号。
  - `/log/body`：参数 `at, id`，返回 `{text}`。
  - `/log/ledger`：参数 `day`。
  - `/log/gaps`：参数 `from, to`。
- Modify: `web/monitor/mind.mjs:12`（PATHS 加这 4 个路径）、`:14`（PARAMS 加 `svc, trace, cost, before, day, at`，已有的不用重复加）；`web/monitor/server.mjs` 的转发名单同样加上。
- Create: `web/monitor/public/log.html` 和 `web/monitor/public/log-ui.js`。页面内容：
  - 顶部筛选：时间范围（默认最近 1 小时）、kind 前缀下拉（call / gate / trigger / switch / mode / link / svc）、服务、只看有花费的；
  - 列表每行：时间、服务、kind、摘要、花费，向下翻页用 `before`；
  - 点开一行：显示信封 JSON 和各段正文全文（逐段按需取）；点 trace 显示整条链；
  - 右上角总账：当天按服务、模型、币种合计，含未知价格时提示"含未知 n 笔"；
  - 断档时段标红条。
  - 首页导航加一个"日志"入口，风格沿用 `mind.html`。
- Test: `tests/test_mind_log_api.py`（4 个接口，以及不带令牌时返回 401）；`web/monitor/log-ui.test.mjs`（筛选参数拼 URL、花费格式、断档渲染）；现有的 `web/monitor/*.test.mjs` 全部要继续通过

**Interfaces:**
- Consumes: Task 3 的 `flows / body / chain / ledger / gaps`

- [ ] **Step 1: 写失败的测试**
  - `test_log_requires_token`
  - `test_log_filters_pass_through`（`kind=call.` 当前缀用）
  - `test_log_body`
  - `test_log_ledger_day`
  - `test_log_gaps`
  - `log-ui.test.mjs`：`buildQuery({kind:"call.", cost:true})` 等于 `"kind=call.&cost=1&limit=200"`；`fmtCost({amount:0.0042,cur:"CNY"})` 等于 `"¥0.0042"`，`amount=null` 时显示 `"未知"`。
- [ ] **Step 2: 跑测试，确认失败**
  Run: `python3 -B -m unittest tests.test_mind_log_api -v && node --test web/monitor/log-ui.test.mjs`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试，确认通过**
  Run: `node --test web/monitor/*.test.mjs` 和 `python3 -B -m unittest discover -s tests -p 'test_mind*.py'`
- [ ] **Step 5: 检查点**

### Task 8: 部署脚本、代码版本、上线验证

**Files:**
- Modify: `server/ear/deploy.sh`（FILES 加 `../common/hello_log.py ../common/hello_log_query.py ../common/prices.py`，同步完写 `~/soul-spike/CODE_VERSION`）、`server/mind/deploy.sh`（把这三个文件同步到 `~/soul-mind/`，并写 `~/soul-mind/CODE_VERSION`）、`server/tts/deploy.sh`（Task 6 已改，这里再加写 `~/soul-tts/CODE_VERSION`）。
- `CODE_VERSION` 的内容：`$(git rev-parse --short HEAD)` 加上工作区有改动时的 `-dirty`，再加部署时间。
- 在服务器上先建目录：`mkdir -p ~/soul-spike/log-spool && chmod 700 ~/soul-spike/log-spool`。

- [ ] **Step 1: 停下，问主人可不可以部署。** 把三边的 `--dry-run` 输出和本次改动清单给主人看。主人点头后才继续。
- [ ] **Step 2: 部署**：依次执行 `sh server/ear/deploy.sh`、`sh server/mind/deploy.sh`、`sh server/tts/deploy.sh`。
  Expected: 6 个 unit 都是 `active`；每个服务的第一条流水是 `svc.start`，`code_version` 不为空。
- [ ] **Step 3: 端到端验证**（hello 在线、主人说一句话后）：用 `hello_log_query.chain` 找到那句的 trace，能看到 `heard.asr → gate.realtime → call.realtime.turn`，网页上能点开看全文。
- [ ] **Step 4: 杀进程验证**：`sudo kill -9 <soul-notes 的 MainPID>`，等 systemd 拉起来；用 `gaps()` 应该看到一条 `seq_jump` 或 `no_beat`；落盘文件里没入库的行在重启后补写入库，不重复。
- [ ] **Step 5: 停服务验证**：`sudo systemctl restart soul-eye`，查到 `svc.stop`（`reason=sigterm`）和 `svc.start`，`gaps()` 不报断档。
- [ ] **Step 6: 量一下大小**：上线满 1 小时后，统计 `log_YYYYMM` 和 `logbody_YYYYMM` 的条数、`size`、`storageSize`、`totalIndexSize`，换算成一天的量，和 spec §7.8（每天 40–60 MB 原始、索引常驻几十 MB 一月）对比，结果报给主人。
- [ ] **Step 7: 收尾**：在 `docs/session-handoff.md` 顶部写上这次上线（做了什么、验证结果、量出来的大小），列出没提交的文件，等主人决定什么时候提交。
