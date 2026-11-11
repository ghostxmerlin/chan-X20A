# 听觉两档：本地（免费）/ 本地+听悟 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 听悟不再是每次连接都自动开，而是由服务器上的开关 `switches.tingwu` 决定。耳朵在连接进行中就能开关它。开关条上的听觉按钮改成"关 / 本地 / 听悟"三段，花费那一行按听悟实际有没有在跑来显示。

**Architecture:**
- 开关的判断写成一个不依赖 numpy 的小模块 `server/ear/tingwu_switch.py`，包括读开关、决定开或停、换算时间位置。
- `HelloDB` 增加两件事：读开关（用自己的连接，带超时），以及把运行状态写进写队列。
- 耳朵用一个全局任务每 5 秒读一次开关，写运行状态。每条连接在 0.1 秒一次的 ticker 里，按开关开启或收尾听悟。
- mind 接口增加 `GET/POST /switches`，Mac 代理放行，开关条改成三段。
- 固件不改。

**Tech Stack:** Python 3.11 + pymongo（服务器 `~/soul-spike/.venv`）；Node 24 标准库。

**Spec:** `docs/superpowers/specs/2026-11-11-senses-split-design.md`（第 2 节第 1、2 条，第 3 节，第 4 节第 1 步）。

## Global Constraints

- 开关文档 `hello.switches {_id:"tingwu", on, at, by}`；状态文档 `{_id:"tingwu_status", running:[session…], available:bool, at}`，至少每 30 秒刷新一次，超过 90 秒算过期。
- 没有开关文档，或读数据库失败超过 60 秒，一律按**关**处理。
- `--tingwu off`，或环境文件里缺 DashScope 密钥时，开关开了也不启动听悟，状态里 `available:false`。
- 同一条连接任何时候最多只有一个听悟流。停掉的流照常取回最终结果；后开的流不能覆盖先前流的段落和统计。
- 听悟句子的 `pos` 用 16 kHz 采样位置表示：开启时已经喂过的采样数，加上 `begin_ms × 16`。
- 听悟启动出错后，60 秒内不再重试（不要每次 tick 都去调付费接口）。
- 页面遵守 CSP（不内联脚本、不写 `style=`）。部署要用户明确说"部署"。

## Review Focus

1. **一会儿开、一会儿关、一会儿又开**（几秒之内来回切）：期望任何时候最多一个流，每个停掉的流都写出最终结果，段落编号连续不重复。测试在 Task 3。
2. **本体断线重连时开关是开的**：期望新连接收到 audio 消息后 0.1 秒内开启听悟。测试在 Task 3。
3. **数据库连不上**：期望 60 秒后按关处理，正在跑的流收尾。测试在 Task 1。
4. **服务器没有配置听悟**，页面却选了"听悟"：期望页面显示"服务器未配置听悟"，花费行不显示正在花钱。测试在 Task 5。
5. **耳朵停了**，状态不再刷新：期望页面显示"听悟状态未知"，不能显示成"没在跑"。测试在 Task 5。

---

### Task 1: 开关判断模块

**Files:**
- Create: `server/ear/tingwu_switch.py`
- Test: `tests/test_tingwu_switch.py`（只用标准库，本机 `python3` 就能跑）

**Interfaces:**
- Produces:
  - `class SwitchReader(read, now=time.monotonic, stale_s=60)`。`read()` 返回文档字典、`None` 或抛异常。
  - `SwitchReader.update() -> None`：读一次，异常不往外抛，只记下失败。
  - `SwitchReader.on -> bool`：最近一次读成功的值是 `doc.get("on") is True`；没有文档算 False；连续读失败超过 `stale_s` 秒也算 False。
  - `decide(want_on: bool, running: bool, available: bool, ready: bool, retry_ok: bool) -> "start" | "stop" | None`。`ready` 表示 audio 消息已到、是本体来源。
  - `tingwu_pos(begin_ms, end_ms, offset_samples, sr=16000) -> list[int] | None`。

- [ ] **Step 1: 写失败的测试**

```python
def test_missing_doc_is_off(self):
    r = SwitchReader(lambda: None); r.update(); self.assertFalse(r.on)
def test_on_doc_turns_on(self):
    r = SwitchReader(lambda: {"_id": "tingwu", "on": True}); r.update(); self.assertTrue(r.on)
def test_read_errors_keep_last_value_for_60_s_then_off(self):
    t = [0.0]; docs = [{"on": True}]
    def read():
        if not docs: raise RuntimeError("db down")
        return docs[0]
    r = SwitchReader(read, now=lambda: t[0]); r.update(); docs.clear()
    t[0] = 59; r.update(); self.assertTrue(r.on)
    t[0] = 61; r.update(); self.assertFalse(r.on)
def test_decide(self):
    self.assertEqual(decide(True, False, True, True, True), "start")
    self.assertIsNone(decide(True, False, True, True, False))      # backing off after an error
    self.assertIsNone(decide(True, False, False, True, True))      # no keys on this server
    self.assertIsNone(decide(True, False, True, False, True))      # audio message not seen yet
    self.assertEqual(decide(False, True, True, True, True), "stop")
    self.assertIsNone(decide(True, True, True, True, True))
def test_pos_adds_the_offset(self):
    self.assertEqual(tingwu_pos(1000, 2500, 48000), [64000, 88000])
    self.assertIsNone(tingwu_pos(None, 2500, 0))
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd server/ear && python3 -m unittest discover -s ../../tests -p "test_tingwu_switch.py"`
Expected: ERROR，`No module named 'tingwu_switch'`

- [ ] **Step 3: 在 `server/ear/tingwu_switch.py` 实现 `SwitchReader`、`decide`、`tingwu_pos`**

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2。Expected: OK（5 个测试）

- [ ] **Step 5: 提交**

```bash
git add server/ear/tingwu_switch.py tests/test_tingwu_switch.py
git commit -m "feat(ear): tingwu switch logic — read the server switch, decide start/stop, offset positions"
```

### Task 2: HelloDB 读开关、写状态

**Files:**
- Modify: `server/ear/hello_db.py`
- Test: `tests/test_hello_db.py`（用文件里已有的假客户端）

**Interfaces:**
- Consumes: 现有的 `HelloDB._put(op, collection, key, doc)`，以及 `client_factory`。
- Produces:
  - `HelloDB.read_switch(name: str) -> dict | None`：同步调用，用一个单独、懒创建的连接（`client_factory` 或 `open_db(uri)`），服务器选择超时 3 秒。异常往外抛，交给 `SwitchReader` 处理。
  - `HelloDB.switch_status(name: str, fields: dict) -> None`：放进写队列，`update` 到 `switches` 集合，键是 `{"_id": name + "_status"}`。

- [ ] **Step 1: 写失败的测试**：`test_read_switch_reads_the_switches_collection`（假库里有 `{_id:"tingwu", on:True}` 时返回它，没有时返回 `None`）；`test_switch_status_is_queued_as_an_update`（`flush()` 之后，存储里出现 `("update","switches",{"_id":"tingwu_status"},{...})`）。
- [ ] **Step 2: 运行，确认失败**。Run: `cd server/ear && python3 -m unittest discover -s ../../tests -p "test_hello_db.py"`。Expected: 两个 AttributeError。
- [ ] **Step 3: 实现上面两个方法**
- [ ] **Step 4: 运行，确认通过**（整个文件全部 OK）
- [ ] **Step 5: 提交** `feat(ear): HelloDB reads switches and queues switch status`

### Task 3: 耳朵按开关开关听悟

**Files:**
- Modify: `server/ear/ear_server.py`：`Ear.__init__`（约 285 行）、`close`（336）、`start_tingwu`（349–383）、audio 消息里自动开启听悟的那两行（760–761）、`ticker`（785）、`main`（845–911）
- Test: `tests/test_ear_protocol.py`，需要 numpy、websockets、opuslib_next。本机先建一次环境：`python3 -m venv .private/venv-ear && .private/venv-ear/bin/pip install numpy "websockets>=13" "opuslib_next>=1.3.1"`（`.private/` 不进仓库）

**Interfaces:**
- Consumes: Task 1 的 `SwitchReader`、`decide`、`tingwu_pos`；Task 2 的 `read_switch`、`switch_status`。
- Produces（Task 4、5 依赖它写出的数据）：
  - 状态文档 `tingwu_status`：`{"running": [正在跑听悟的 session…], "available": bool, "at": epoch_ms}`
  - 会话文档 `tingwu`（最近一段的统计，兼容旧的读法），加上 `tingwu_streams.<k>`。每段统计包括 `{sentences, paragraphs, audio_s, data_id, started_ms, stopped_ms, offset_samples}`。

做法：
- 删掉 audio 消息里那段无条件开启听悟的代码。
- `Ear` 增加这些字段：`tingwu_switch`（由 `main` 注入；没有数据库时注入一个 `read` 总返回 `None` 的 `SwitchReader`）、`tw_streams`、`tw_paragraphs`、`tw_retry_at`。
- `ticker` 每次 tick 调一次 `decide(self.tingwu_switch.on, self.tingwu is not None, self.tingwu_keys is not None, self.device_profile and self.session is not None, time.monotonic() >= self.tw_retry_at)`，按结果调用 `start_tingwu()` 或 `stop_tingwu()`。
- `start_tingwu()`：段号 `k = tw_streams + 1`，`offset = self.ring.end`。闭包里抓住**这一个** `stream` 对象，不要再引用 `self.tingwu`。句子的 `pos` 用 `tingwu_pos(b, e, offset)`。段落编号接着 `self.tw_paragraphs` 往下数。最终结果写进 `tingwu_streams.<k>`，同时写一份到 `tingwu`。出错回调把 `tw_retry_at` 设为当前时间加 60 秒。
- `stop_tingwu()`：先调用 `stream.close()`（它会取回最终结果），再把 `self.tingwu` 设为 `None`。`close()` 时也走这一步。
- `main`：
  - 一个后台任务每 5 秒执行一次 `await asyncio.to_thread(reader.update)`，不要占用模型线程池。
  - 每 30 秒，以及任何流开启或停止时，写一次 `switch_status("tingwu", {...})`。`running` 从活着的 `Ear` 里收集。
  - 打印一行 `tingwu switch: on|off`，只在值变化时打印。

- [ ] **Step 1: 写失败的测试**，用打了补丁的假 `TingwuStream`（记录 `start`、`feed`、`close`，暴露 sentence 和 final 回调）：
  - `test_tingwu_waits_for_the_switch`：开关关着，送完 audio 消息并 tick 之后，没有创建流；把开关改成开，再 tick 一次，就创建了 1 个流。
  - `test_switch_off_closes_the_stream_mid_session`：开着创建了流；改成关，tick 之后 `close()` 被调用一次，`ear.tingwu is None`。
  - `test_second_stream_positions_and_paragraphs_continue`：先送 3 秒音频（48000 个采样），再开启。第二个流里一句 `begin_ms=1000`，它的 `pos[0] == 48000 + 16000`。第一个流写出 2 个段落、第二个流写出 1 个段落，最后存下的段落编号是 1、2、3。
  - `test_start_error_backs_off_60_s`：出错回调之后连续 tick，60 秒内不再创建新流（用补丁替换 `time.monotonic`）。
  - `test_rapid_toggle_keeps_one_stream`：开、关、开分别发生在相邻的 tick，全程最多只有一个没关闭的流，两个流都调用过 `close()` 或者还在运行。
- [ ] **Step 2: 运行，确认失败**。Run: `cd server/ear && ../../.private/venv-ear/bin/python -m unittest discover -s ../../tests -p "test_ear_protocol.py"`。Expected: 新增的 5 个测试失败，旧测试全部通过。
- [ ] **Step 3: 按上面的做法实现**
- [ ] **Step 4: 运行，确认通过**：同一条命令，整个文件全部 OK。另外跑 `tests/test_tingwu_switch.py` 和 `tests/test_hello_db.py`，也要全部 OK。
- [ ] **Step 5: 提交** `feat(ear): 听悟 follows the server switch live; positions offset by samples already heard; each stream keeps its own result`

### Task 4: mind 接口 `/switches`，代理放行

**Files:**
- Modify: `server/mind/api.py`、`web/monitor/mind.mjs`（`PATHS`、`POST_PATHS`）、`web/monitor/server.mjs`（GET 和 POST 的放行列表）
- Test: `tests/test_mind_api.py`、`web/monitor/mind.test.mjs`

**Interfaces:**
- Produces:
  - `GET /switches` 返回 `{"tingwu": {"on": bool, "at": ms|None}, "tingwu_status": {"running": [...], "available": bool|None, "at": ms|None, "stale": bool}}`。没有文档时 `on` 为 False、`stale` 为 True。`stale` 的意思是状态的 `at` 早于当前时间 90 秒，或者根本没有状态。
  - `POST /switches {"name": "tingwu", "on": bool}` 写入 `switches`（`by:"page"`），返回同 `GET`。`name` 不认识时返回 400，`on` 不是布尔值时也返回 400。

- [ ] **Step 1: 写失败的测试**：
  - Python：`test_switches_default_off_and_stale`、`test_post_switch_tingwu`（之后 `GET` 显示 `on: True`）、`test_switch_rejects_unknown_name_and_non_bool`。
  - Node：在 `proxy forwards whitelisted POSTs…` 里加 `assert.equal((await proxy.post('/switches','{"name":"tingwu","on":true}')).status,200)`，并检查 `proxy.get('/switches')` 返回 200；在 server 的测试里，`POST /api/mind/switches` 能转发到代理。
- [ ] **Step 2: 运行，确认失败**。Run: `cd server && python3 -m unittest discover -s ../tests -p "test_mind_*.py"`；`cd web/monitor && node --test mind.test.mjs switches.test.mjs`。
- [ ] **Step 3: 实现**（在 mind 接口的 `make_server` 里直接读写 `db["switches"]`）
- [ ] **Step 4: 运行，确认通过**（mind 测试全部 OK，Node 测试全部通过）
- [ ] **Step 5: 提交** `feat(mind): GET/POST /switches for the 听悟 tier; proxy allows it`

### Task 5: 开关条的听觉按钮改成三段

**Files:**
- Modify: `web/monitor/public/switches-ui.js`、`web/monitor/public/switches.css`
- Test: `web/monitor/switches.test.mjs`

**Interfaces:**
- Consumes: Task 4 的 `GET /switches` 和 `POST /switches`。
- Produces:
  - `switchView(robot, fresh, mind, switches)`，其中 `hearing` 增加字段：
    - `level`：`"off" | "local" | "tingwu" | "unknown"`
    - `tingwuRunning`：`true | false | null`，`null` 表示状态过期
    - `tingwuAvailable`
  - `levelRequests(level, robot) -> [{kind, body}, …]`，按顺序发送：
    - `off`：先 `{kind:'control', body:{action:'hearing_listen', enabled:false, boot}}`，再 `{kind:'switch', body:{name:'tingwu', on:false}}`
    - `local`：先 `{kind:'switch', on:false}`，再 `hearing_listen true`
    - `tingwu`：先 `{kind:'switch', on:true}`，再 `hearing_listen true`。先设好档位，连接一建立就是对的档位。
  - 听觉区域显示三个按钮 `#sw-hearing-off`、`#sw-hearing-local`、`#sw-hearing-tingwu`，当前档位的按钮 `aria-pressed="true"`。
  - 花费行的规则：
    - 状态没有过期且 `running` 不为空：显示"正在花钱：听悟…"。
    - 状态过期，但选中的是"听悟"档：显示"听悟状态未知"，不能当作免费。
    - `available === false` 时："听悟"按钮禁用，详情写"服务器未配置听悟"。

- [ ] **Step 1: 写失败的测试**：
  - `test level is local when listening with the tingwu switch off`
  - `level tingwu and cost only when the ear reports a running stream`
  - `stale status with tingwu selected says unknown, never free`
  - `unavailable tingwu disables the button`
  - `level requests set the tier before turning hearing on`（就是上面三种档位的请求顺序）
  - 原来那条测试 `toggle requests: hearing_listen…` 改成断言 `levelRequests('off', robot)`。
- [ ] **Step 2: 运行，确认失败**。Run: `cd web/monitor && node --test switches.test.mjs`
- [ ] **Step 3: 实现**：`SwitchBar` 每 5 秒额外拉一次 `/api/mind/switches`。`kind:'switch'` 发到 `POST /api/mind/switches`。按顺序发送，任何一步出错就停下，并显示是哪一步出的错。
- [ ] **Step 4: 运行，确认通过**：`node --test *.test.mjs` 全部通过。
- [ ] **Step 5: 提交** `feat(monitor): hearing switch has three tiers — 关 / 本地 / 听悟; cost line follows the ear's real 听悟 state`

### Task 6: 部署与现场验证（要等用户说"部署"）

- [ ] **Step 1**：`server/ear/deploy.sh`（耳朵会重启，本体断线后几秒内自动重连），`server/mind/deploy.sh`，然后重启本机观察台。
- [ ] **Step 2**：部署后没有开关文档，所以听悟默认**关**。页面选"本地"之后，日志里出现 `tingwu switch: off`；1 分钟后 `hello.tingwu` 没有新的行，SenseVoice 的句子照常出现。
- [ ] **Step 3**：选"听悟"，10 秒内日志里出现开启听悟的记录；对着本体说几句话，`hello.tingwu` 出现句子，而且它们的 `pos` 和同一时间 SenseVoice 句子的 `pos` 有重叠。
- [ ] **Step 4**：切回"本地"，日志里出现 `tingwu final … paragraphs`；会话文档里有 `tingwu_streams.1`；之后不再有新的听悟句子。花费行显示"现在不花钱"，前提是情景是关着的，或者视觉也关着。
- [ ] **Step 5**：在记录（ledger）里写下验证结果；更新 `docs/session-handoff.md` 最上面那条交接记录。
