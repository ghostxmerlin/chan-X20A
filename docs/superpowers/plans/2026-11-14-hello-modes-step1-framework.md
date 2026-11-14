# hello 模式 第一步：切换框架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"模式"成为一个真实的、到处一致的状态：mind 存当前模式并负责切换；机器人、DualKey、页面上报输入，并显示 mind 确认过的模式（眼睛、角标、触摸含义、摇杆灯、页面模式条）；加计费总闸（关掉 + 禁止再开）和 0–9 预设的服务器那一半。脱口秀 = 今天的行为，IDLE 有自己的屏幕，其余六个模式是空壳。

**Architecture:** 先把 ear 改成自己终结 `/mouth`、行为不变，单独上线验证（A0、D0）。然后加 `server/mind/mode_table.py`（模式表、预设表）、`modes.py`（纯状态机）、`mode_store.py`（持久化、去重、日志、开机默认、设置超时、前置检查、离开钩子）、`billing.py`（总闸）。ear 在 `/mouth` 上推模式快照、转模式输入，给 DualKey 推摇杆灯。固件新增三个纯逻辑头文件（消息、口令、手势）和屏幕上的 ASCII 角标、每模式外观、上滑模式页、下滑快捷设置页。页面新增 `mode-ui.js` 模式条和快捷设置面板。

**Tech Stack:** Python 3.11 标准库 + pymongo（服务器 venv），测试用 `tests/mind_fixtures.py` 的 `FakeDB` 和 `unittest`；ear 测试要 `websockets`（在 `.private/venv-ear` 里跑，见 `tests/test_mouth_link.py:1`）；Node 24 `node --test`；固件 ESP-IDF 5.5.4，主机测试 `c++ -std=c++17`（写法照 `tests/test_mouth_liveness.py`、`tests/test_monitor_protocol.py:12`）。

**Spec:** `docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md`（第 1.2 节主人的回答、第 3–7 节、第 10 节）。主人 2026-11-14 两轮回答已经定下全部问题（spec 第 1.2 节第 1–13 条），spec 第 12 节没有待定项。听觉先手动、预设 5 空着。模式和预设的定义都在 `server/mind/mode_table.py` 的两张表里。

## Global Constraints

- **第一步进入任何模式都不开始花钱；IDLE 和开机不动付费感官**（主人 2026-11-14）。唯一的花钱行为是脱口秀今天已有的：说一轮（按了才写）、提前写、连续说（默认关，重启后关，`server/mind/say_prefetch.py:36`、`say_auto.py:22`）。不新增任何付费调用、不新增模型提示词。
- **离开脱口秀**：提前写、连续说关掉，存货丢掉；**任何模式切换**：正在说的停（`speak.cancel_pending`）。
- **计费总闸（第一步）= 关掉 + 禁止再开 + 拦说话**：关时把听悟、识别、纪要、情景、提前写、连续说关掉、停正在说的；关着时打开这些开关、调说话类接口一律 409"计费关着"。**不**改 `llm.chat`、不改 ear 的听悟逻辑（进程级闸门是后一步，见"不在本计划内"）。
- **mind 是唯一改模式的地方。** 设备输入按 `device + boot + seq` 去重；`age_ms > 3000` 的设备输入（含 DualKey 的方向和点击）丢掉，结果记 `stale`。模式变了或总闸变了，`seq` 都加 1。
- **模式表和预设表都在 `server/mind/mode_table.py`（`MODES`、`PRESETS` 两个字典），是唯一定义。** 放成 Python 模块，是因为 `server/mind/deploy.sh:15` 只部署 `server/mind/*.py`。固件不写死"哪个模式用哪款眼睛"，只认快照里的 `look` 词表。
- **严格互斥。** 设置是临时层（记 `return_to`，close / 5 分钟无动作回去）；事件驱动是外层（第一步不会自己进入任何客人模式）。
- **第一步不下发本体控制**：`/mouth` 上没有 `control` 消息；预设的机器人那一半只由页面执行（和开关条同一组请求）。
- 每个新的 mind GET/POST 路径要加在三处：`server/mind/api.py`（`get`/`post` 分支和 `do_POST` 的白名单元组 `:632-633`）、`web/monitor/server.mjs`（GET `:84`、POST `:54`）、`web/monitor/mind.mjs`（`PATHS` / `POST_PATHS` `:10-11`，新查询参数加进 `PARAMS` `:12` 和 `check()`）。新页面模块加进 `server.mjs` 的 `assets`（`:17`）和 `web/monitor/static-files.test.mjs`。ear 新文件加进 `server/ear/deploy.sh:11` 的 `FILES`（11-13 漏过一次）。
- **固件（C 部分）**：按分工归 ESP32 一侧。PSRAM 优先：新状态和队列放 PSRAM；LVGL 小对象落在内部 RAM（`firmware/sdkconfig:1879-1881`、`:3430`），新页面用时才建、关了就删；上线前后记内部 RAM 剩余、最大连续块、DMA 剩余。`/mouth` 任务是 PSRAM 栈，不能写 NVS（`fw/psram_task.h:8-15`）；第一步 `/mouth` 上的新消息都不写 NVS。
- **刷机：停下来问主人。** 任何写设备的步骤之前停下，等主人当次同意。刷机照现有规矩：身份核对、分区与活动槽核对、当前应用与存档一致、整槽备份并独立 verify、只写 `ota_1 / 0x510000`、写后独立 verify。参照 `.private/wifi-factory-v0491/preflight.py`、`deploy.py`（只读参考，不修改）。
- **部署也要先问主人**，每次部署前在服务器上留一份旧文件（D0、D1 有具体命令）。ear 重启会断一次听觉，挑没人用的时候。
- **模式版本的服务器（A1–A6）和 0.4.92 同一次上线**（D1、D2 连着做）：服务器上了模式而机器人还是 0.4.91 时，DualKey 一推就离开脱口秀、页面"说一轮"变 409，0.4.91 的屏幕却什么都不显示。A0 是行为不变的，可以早上。
- 公开仓库：测试和文档只用"主人""人物甲"；不放服务器地址、令牌、口令。日志不打句子文字。
- 提交只在主人授权本次执行可以提交时做；否则跳过各任务的 Commit 步骤。

## Review Focus

- soul-tts 在一句话中途断开：机器人收到 `tts.stop`，`/mouth` 不断；soul-tts 重启后再说一段（文本 + 二进制帧）能到机器人（Task A0 `test_upstream_restart_mid_stream`）。
- 机器人断开：上游立刻关（Task A0 `test_robot_close_closes_upstream`），soul-tts 不会给不在线的机器人合成。
- 计费总闸关掉：`/mode/seq` 变大，快照 `billing = false`，付费开关不能再开（Task A4 `test_billing_change_bumps_seq`、`test_billing_off_blocks_paid_switches_and_speech`）。
- 离开脱口秀后提前写、连续说都是关的，存货清空；在 IDLE 里点"说一轮"返回 409（Task A4）。
- 同一设备输入重发两次只切一次；DualKey 重连后重发的旧方向不切模式（Task A2 `test_device_input_dedup`，A4 `test_dualkey_stale_direction_ignored`）。
- 机器人收到坏的快照不改屏幕；相同 `seq` 不算坏消息；握手后第一条快照总收下（Task C1）。
- 手势分类器按真机数据（多次按下采样，松手 `(-1,-1)`）测，含途中抬起、松手抖动（Task C3）。
- 旧的 `/mouth` 连接晚到的"断开"不会把新连接标成离线（Task A2 `test_old_connection_close_ignored`）。

---

## 接口（各任务共用，以这里为准）

### 模式和输入的名字

- 模式 `id`：`talk`、`chat`、`settings`、`event`、`subtitle`（`dir` 为 `zh_en` 或 `en_zh`）、`crowd`、`companion`、`idle`。
- 输入 `input`（`value` 见括号）：
  - 口令：`forward`、`backward`、`play`、`pause`、`open`、`close`、`start`、`stop`、`next`、`previous`；
  - `digit`（0–9）；
  - `tap`、`double_tap`、`long`；
  - `direction`（`up`/`down`/`left`/`right`，DualKey）；
  - `select`（`{mode, dir?}`，上滑页和网页；选当前模式 = 离开）；
  - `billing`（`true`/`false`）；
  - `boot`、`timeout`（只 mind 内部）。
- 来源 `source`：`asr`、`touch`、`select`、`quick`、`dualkey`、`page`、`boot`、`timeout`。
- 结果 `code`：`ok`、`already`、`not_in_mode`、`only_talk`、`billing_off`、`busy`、`too_soon`、`mouth_not_tts`、`not_implemented`、`undefined_preset`、`stale`、`unknown_input`、`senses_on_page`（机器人发的预设 1 / 9：服务器那一半做了，机器人那一半要在页面上做）。

### 快照（mind 产生，ear 原样转给机器人；页面也读它）

```json
{"seq": 12, "mode": "talk", "dir": null, "host": null, "return_to": null, "billing": true,
 "since": 1763100000000, "by": "asr",
 "look": {"eye": "laughing_man", "pool": null, "label": "TALK", "color": "E040A0"},
 "touch": {"tap": "server", "double": "server"}}
```

- `host`：`"event"` 表示正被事件驱动托管（第一步永远是 `null`）。
- `look.eye` 词表：`laughing_man`、`v05`、`v14`、`v18`、`page_subtitle`、`page_settings`、`page_idle`。`look.pool`：`null`（该款默认自然循环）、`lively`。`look.label`：ASCII，≤ 10 字符（字幕带方向，例如 `SUB ZH>EN`）。
- `touch.tap` / `touch.double`：`server`、`badge`（只 IDLE 单击）、`none`。

### mind 接口（本机回环，鉴权照旧）

| 方法 路径 | 正文 / 参数 | 返回 |
|---|---|---|
| `GET /mode` | — | `{snapshot, robot: {connected, boot, conn, applied_seq, at}, log: [最近 10 条]}` |
| `GET /mode/seq` | — | `{seq}`（ear 每秒问一次，只读内存） |
| `POST /mode` | `{input, value?}`（页面，`source = page`） | `{snapshot, result: {code, toast}}` |
| `POST /mode/input` | `{device, boot, seq, age_ms, source, input, value?}`（ear 转机器人的） | `{snapshot, result}` |
| `POST /mode/robot` | `{boot, conn, event: hello/applied/closed, applied_seq?}` | `{snapshot}` |
| `POST /preset` | `{n, source}` | `{snapshot, result, robot: {hearing?: "local", vision?: "upload"}}`（`robot` 是机器人那一半，只有页面执行） |
| `POST /switches` | 新增 `{name: "billing", on}` | 同今天 `switches()`，多一项 `billing` |

### `/mouth` 上的新消息（第二步才加，A5；机器人不认识的类型照旧忽略，`fw/mouth_link.cpp:57-73`）

- 下行：`{"type":"mode", ...快照}`；`{"type":"mode_result","seq":N,"code":"ok","toast":"SAYING"}`（`seq` 是机器人那次输入的编号，`toast` ASCII ≤ 32 字符）。
- 上行：`{"type":"mode_input","seq":N,"age_ms":A,"source":"asr","input":"forward","value":null}`；`{"type":"mode_applied","seq":S}`。
- 单条 ≤ 1 KB（机器人收的上限 1600 B，`fw/mouth_stream.h:19`）。

### ear → DualKey

快照 `seq` 变了，给每个连着的 DualKey 发 `{"type":"mode","current":"<mode id>"}`；`idle` 关灯（`dualkey/README.md:63`）。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| Modify `server/ear/mouth_link.py` | A0：`MouthLink` 类，ear 终结 `/mouth`（行为不变）；A5：加模式消息 |
| Modify `server/ear/ear_server.py:964-976`、`:977-989` | `/mouth` 用 `MouthLink`、连接编号；每秒问 mind 的 `seq`；DualKey 推灯 |
| Modify `server/ear/signal_link.py` | 向 DualKey 推 `mode` |
| Create `server/mind/mode_table.py` | `MODES`：八个模式的中文名、`look`、`touch`、`inputs`、`paid`、`kind`；`PRESETS`：0–9 预设 |
| Create `server/mind/modes.py` | 纯函数 `transition(state, input, value, *, source, speaking) -> (state, result, effects)`；`snapshot()` |
| Create `server/mind/mode_store.py` | `hello.mode`、`hello.mode_log`；去重、陈旧丢弃、开机默认、设置超时、连接编号、`guard`、`on_change`、总闸经它加 `seq` |
| Create `server/mind/billing.py` | 总闸读写；关时的一组动作；预设的服务器那一半 |
| Modify `server/mind/api.py` | 新路径；`billing` 开关；说话类接口按模式和总闸拦；付费开关在总闸关时拒绝；`/signal` 之后把 DualKey 的方向和点击交给模式 |
| Modify `server/mind/runner.py:125-200` | 建 `ModeStore`、`Billing`，接钩子和 `guard`；主循环每轮 `mode_store.tick(now)` |
| Modify `server/mind/say_prefetch.py`、`say_auto.py` | 多一个 `mode_fn`，不在脱口秀就不写、不播（防御） |
| Modify `server/mind/README.md` | 加"模式（第一步）"一节，含"一次性命令行工具不受总闸约束" |
| Create `tests/test_mind_modes.py`、`tests/test_mind_mode_store.py`、`tests/test_mind_billing.py`、`tests/test_mind_mode_api.py` | mind 侧测试 |
| Modify `tests/test_mouth_link.py`、`tests/test_signal_link.py` | ear 侧测试 |
| Create `web/monitor/public/mode-ui.js`、`web/monitor/public/mode.css` | 模式条和快捷设置面板 |
| Modify `web/monitor/public/index.html:6`、`:14`、`mind.html` | 开关条下面加 `<nav id="modebar">` 和脚本 |
| Modify `web/monitor/public/say-ui.js` | 不在脱口秀或总闸关时台词按钮禁用并写原因 |
| Modify `web/monitor/mind.mjs`、`server.mjs`、`static-files.test.mjs` | 白名单和静态文件 |
| Create `web/monitor/mode-ui.test.mjs` | 页面模块测试 |
| Create `fw/mode_wire.h` + `tests/mode_wire_test.cpp` + `tests/test_mode_wire.py` | 快照解析、输入和回执组装、输入队列 |
| Create `fw/asr_mode_words.h` + `tests/asr_mode_words_test.cpp` + `tests/test_asr_mode_words.py` | 口令 → 模式输入 |
| Create `fw/touch_gesture.h` + `tests/touch_gesture_test.cpp` + `tests/test_touch_gesture.py` | 单击 / 双击 / 上下左右滑分类 |
| Create `fw/mode_look.h` + `tests/mode_look_test.cpp` + `tests/test_mode_look.py` | 快照 → 首页外观 |
| Modify `fw/mouth_link.cpp`、`fw/mouth_link.h` | 收 `mode`/`mode_result`，发 `mode_input`/`mode_applied` |
| Modify `fw/asr_unit.cpp:127-133`、`fw/interaction_policy.h:8-12`、`fw/hearing_policy.h:10`、`fw/voice_policy.h:25` | 口令改绑，去掉旧用途 |
| Modify `fw/screen.cpp`、`fw/screen.h`、`fw/eye_mode.h` | 角标、切换闪屏、每模式外观、占位页、模式选择页、快捷设置页、摘要页两行、首页手势改走分类器 |
| Modify `upstream/StackChan/firmware/CMakeLists.txt:5` | 版本 `collector-0.4.92` |
| Modify `docs/collector-protocol.md` | 新的口令含义、`/mouth` 新消息、首页手势；更正 `:165` |

（`fw/` = `upstream/StackChan/firmware/main/collector/`。）

---

## A. 服务器

### Task A0: ear 自己终结 `/mouth`（行为不变，单独上线）

**Files:**
- Modify: `server/ear/mouth_link.py`、`server/ear/ear_server.py:964-976`
- Test: `tests/test_mouth_link.py`

**Interfaces:**
- Produces: `class MouthLink(client, *, upstream_url, token, connect=None, retry_s=5)`，`await link.run() -> why`：
  - 等机器人第一条 `hello`（`fw/mouth_link.cpp:116-119`：带 `device_id`、`boot_id`），记下原文和 `boot_id`；ear 自己回 `{"type":"hello","codec":"opus","frame_ms":60,"sample_rate":24000}`（机器人只查这三项，`fw/mouth_link.cpp:60-61`）。
  - 然后连 soul-tts：把机器人的原始 `hello` 转过去，吞掉 soul-tts 回的 `hello`；之后双向原样转发（二进制帧、`tts.*` 文本、机器人的播放回报）。
  - 记着"正在播的流"：看下行的 `tts.start {stream}`、`tts.end`、`tts.stop`，和上行的 `tts.done/stopped/error`。
  - **上游断开或连不上**：对每个正在播的流给机器人发 `{"type":"tts.stop","stream":N}`；机器人这一侧不关；每 `retry_s` 秒重连，重连后重放机器人的 `hello`。
  - **机器人断开**：立刻关上游，`run()` 返回 `device_closed`。
  - 属性 `boot`、`upstream_up`（A5 用）。
  - `ear_server.py`：`/mouth` 分支改用 `MouthLink`，上限照旧 2 条；每条连接一个递增编号 `conn`（A5 用）。旧的 `relay()` 删掉。
- 这一任务**不**加任何模式消息。

- [ ] **Step 1: 写失败的测试**（加到 `tests/test_mouth_link.py`，用文件里已有的本地 websockets 服务器做假的 soul-tts，加一个能停、能重启的包装）

```python
    async def test_ear_answers_hello_itself(self):
        async with self.robot() as ws:
            await ws.send(json.dumps({"type": "hello", "device_id": "d", "boot_id": "b1", "codecs": ["opus"], "sample_rate": 24000}))
            first = json.loads(await ws.recv())
            self.assertEqual((first["type"], first["codec"], first["frame_ms"], first["sample_rate"]), ("hello", "opus", 60, 24000))
        self.assertEqual(json.loads(self.upstream_text[0])["boot_id"], "b1")      # the robot's hello reached soul-tts

    async def test_upstream_restart_mid_stream(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 7}))
            await self.recv_type(ws, "tts.start")
            await self.stop_upstream()
            stop = await self.recv_type(ws, "tts.stop", timeout=2)
            self.assertEqual(stop["stream"], 7)
            await self.start_upstream()
            await self.wait_upstream_hello(timeout=8)                              # hello replayed after reconnect
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 8}))
            await self.upstream_send(b"\x08\x00\x00\x00" + b"\x01" * 40)           # one binary audio frame
            self.assertEqual((await self.recv_type(ws, "tts.start"))["stream"], 8)
            self.assertIsInstance(await self.recv_binary(ws), bytes)
            self.assertIsNone(ws.close_code)

    async def test_no_upstream_keeps_robot(self):
        await self.stop_upstream()
        async with self.robot() as ws:
            await self.handshake(ws)
            await asyncio.sleep(1.5)
            self.assertIsNone(ws.close_code)

    async def test_robot_close_closes_upstream(self):
        async with self.robot() as ws:
            await self.handshake(ws)
        await asyncio.wait_for(self.up_closed.wait(), 2)

    async def test_reports_still_relayed(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "tts.done", "stream": 3}))
        self.assertIn('"tts.done"', "".join(self.upstream_text))
```

- [ ] **Step 2: 运行，确认失败**

Run: `.private/venv-ear/bin/python -B -m unittest tests.test_mouth_link -v`
Expected: FAIL（ear 不回 `hello`；上游断开时机器人这侧被关）

- [ ] **Step 3: 实现 `MouthLink`，改 `ear_server.py`**

- [ ] **Step 4: 运行，确认通过；跑 ear 全部测试**

Run: `.private/venv-ear/bin/python -B -m unittest tests.test_mouth_link -v && .private/venv-ear/bin/python -B -m unittest discover -s tests -v`
Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add server/ear/mouth_link.py server/ear/ear_server.py tests/test_mouth_link.py
git commit -m "ear: terminate /mouth itself; tts.stop on upstream loss, upstream closed with the robot"
```

上线和验证见 Task D0。D0 通过之前不做 A5。

---

### Task A1: 模式表和纯状态机

**Files:**
- Create: `server/mind/mode_table.py`（这一步只写 `MODES`）、`server/mind/modes.py`
- Test: `tests/test_mind_modes.py`

**Interfaces:**
- Produces:
  - `mode_table.MODES: dict[str, dict]`，导入时校验：八个 id 都在，`look.eye` 在词表里，`touch` 取值合法，`label` 是 ≤ 10 字符的 ASCII。
  - `State = {mode, dir, host, return_to}`（字典；`billing` 不在状态里，由 ModeStore 放进快照）。
  - `transition(state, input, value, *, source, speaking=False) -> (new_state, result, effects)`：纯函数，不碰库、不碰时间。`result = {code, toast}`；`effects` 是有序的动作名列表：`stop_speech`、`talk_leave`（关提前写、连续说）、`say_round`、`prefetch_on`、`prefetch_off`。
  - `snapshot(state, *, seq, since, by, billing) -> dict`（接口一节的快照，`look`、`touch` 从模式表取）。
- 规则（照 spec 3.5、第 1.2 节主人的回答）：
  - 进入词、`select` → 那个模式（直接进，不经 IDLE）；`select` 当前模式 → 离开。
  - 离开词：在那个模式里 → `idle`（被托管时回 `event`）；不在 → `not_in_mode`，状态不变。
  - 已在那个模式的进入词 → `already`；例外：`talk` 里 `forward` → `[say_round]`。
  - `talk` 里：`tap` → `speaking` 时 `[stop_speech]`，否则 `[say_round]`；`double_tap` → `[prefetch_on]`；`next` → `[prefetch_on]`；`previous` → `[prefetch_off]`。别的模式里 `next`/`previous` → `only_talk`。
  - 空壳模式里 `tap`/`double_tap` → `not_implemented`；`idle` 里 `double_tap` → `not_implemented`。
  - `direction`：`up` → `subtitle/zh_en`，`down` → `subtitle/en_zh`，`left` → `crowd`，`right` → `companion`；已在该模式且同方向 → 离开；在字幕里推另一个上下方向 → 换 `dir`。
  - `long` → `talk`，`effects` 含 `stop_speech`。
  - `open` 进入 `settings` 时 `return_to = 旧模式`；`close`、`timeout` → `return_to`（没有就 `idle`）；在设置里 `select` 别的模式 → 那个模式，`return_to` 清掉。
  - `start` → `event`；在 `event` 里 `stop` → `idle`；`event` 里手动选别的模式 → 事件驱动停，直接进那个模式（`host` 不设，主人 2026-11-14）。`event` 的 `look` = `page_idle`、`label = AUTO`（还没进入任何模式时显示 IDLE 屏幕，【我定】）；以后托管客人模式时用客人模式的 `look`、`host = 'event'`。
  - `boot` → `talk`。
  - 只要 `mode` 变了，`effects` 以 `stop_speech` 开头；从 `talk` 变走时再加 `talk_leave`。
  - `digit`、`billing` 不在这里处理（Task A3、A2）。

- [ ] **Step 1: 写失败的测试** `tests/test_mind_modes.py`

```python
class TransitionTest(unittest.TestCase):
    def go(self, mode, inp, value=None, speaking=False, **st):
        s = {'mode': mode, 'dir': None, 'host': None, 'return_to': None, **st}
        return modes.transition(s, inp, value, source='asr', speaking=speaking)

    def test_pairs_enter_and_close_to_idle(self):
        for enter, leave, m in (('forward', 'backward', 'talk'), ('play', 'pause', 'chat'), ('start', 'stop', 'event')):
            s, r, e = self.go('idle', enter)
            self.assertEqual((s['mode'], r['code']), (m, 'ok'))
            s, r, e = self.go(m, leave)
            self.assertEqual(s['mode'], 'idle')
            self.assertEqual(e[0], 'stop_speech')

    def test_opening_another_mode_goes_straight_there(self):
        self.assertEqual(self.go('chat', 'open')[0]['mode'], 'settings')
        self.assertEqual(self.go('crowd', 'play')[0]['mode'], 'chat')
        self.assertEqual(self.go('chat', 'select', {'mode': 'chat'})[0]['mode'], 'idle')    # picking the current one closes it

    def test_leave_word_outside_mode_changes_nothing(self):
        s, r, e = self.go('talk', 'pause')
        self.assertEqual((s['mode'], r['code'], e), ('talk', 'not_in_mode', []))

    def test_talk_inputs(self):
        self.assertEqual(self.go('talk', 'forward')[2], ['say_round'])
        self.assertEqual(self.go('talk', 'tap')[2], ['say_round'])
        self.assertEqual(self.go('talk', 'tap', speaking=True)[2], ['stop_speech'])
        self.assertEqual(self.go('talk', 'double_tap')[2], ['prefetch_on'])
        self.assertEqual(self.go('talk', 'previous')[2], ['prefetch_off'])
        self.assertEqual(self.go('idle', 'next')[1]['code'], 'only_talk')
        self.assertEqual(self.go('idle', 'forward')[2], ['stop_speech'])      # outside talk, forward only enters

    def test_leaving_talk_adds_talk_leave(self):
        self.assertEqual(self.go('talk', 'play')[2], ['stop_speech', 'talk_leave'])

    def test_dualkey_directions(self):
        s, _, _ = self.go('talk', 'direction', 'up')
        self.assertEqual((s['mode'], s['dir']), ('subtitle', 'zh_en'))
        s, _, _ = self.go('subtitle', 'direction', 'down', dir='zh_en')
        self.assertEqual((s['mode'], s['dir']), ('subtitle', 'en_zh'))
        self.assertEqual(self.go('crowd', 'direction', 'left')[0]['mode'], 'idle')
        self.assertEqual(self.go('crowd', 'direction', 'right')[0]['mode'], 'companion')

    def test_settings_is_a_temporary_layer(self):
        s, _, _ = self.go('crowd', 'open')
        self.assertEqual((s['mode'], s['return_to']), ('settings', 'crowd'))
        self.assertEqual(self.go('settings', 'close', return_to='crowd')[0]['mode'], 'crowd')
        self.assertEqual(self.go('settings', 'timeout', return_to='talk')[0]['mode'], 'talk')
        s, _, _ = self.go('settings', 'select', {'mode': 'chat'}, return_to='talk')
        self.assertEqual((s['mode'], s['return_to']), ('chat', None))

    def test_shell_taps_and_long_press(self):
        for m in ('chat', 'crowd', 'companion', 'subtitle', 'event', 'settings'):
            self.assertEqual(self.go(m, 'tap')[1]['code'], 'not_implemented')
        s, r, e = self.go('crowd', 'long')
        self.assertEqual((s['mode'], e[0]), ('talk', 'stop_speech'))
        self.assertEqual(self.go('idle', 'boot')[0]['mode'], 'talk')

    def test_manual_pick_stops_event_driven(self):
        s, _, e = self.go('event', 'play')
        self.assertEqual((s['mode'], s['host']), ('chat', None))
        self.assertEqual(self.go('chat', 'pause')[0]['mode'], 'idle')       # not back to event-driven

    def test_event_waiting_shows_idle_screen(self):
        snap = modes.snapshot({'mode': 'event', 'dir': None, 'host': None, 'return_to': None}, seq=1, since=1, by='asr', billing=True)
        self.assertEqual((snap['look']['eye'], snap['look']['label']), ('page_idle', 'AUTO'))

    def test_snapshot_uses_table(self):
        st = {'mode': 'chat', 'dir': None, 'host': None, 'return_to': None}
        snap = modes.snapshot(st, seq=7, since=1, by='page', billing=False)
        self.assertEqual((snap['look']['eye'], snap['touch']['tap'], snap['billing']), ('v05', 'server', False))
        idle = modes.snapshot({**st, 'mode': 'idle'}, seq=8, since=1, by='page', billing=True)
        self.assertEqual(idle['touch'], {'tap': 'badge', 'double': 'server'})
        self.assertLessEqual(len(json.dumps(snap).encode()), 1024)

    def test_table_is_valid(self):
        self.assertEqual(set(mode_table.MODES), {'talk', 'chat', 'settings', 'event', 'subtitle', 'crowd', 'companion', 'idle'})
        self.assertEqual(mode_table.MODES['talk']['look']['eye'], 'laughing_man')
        self.assertEqual(mode_table.MODES['crowd']['look']['eye'], 'v14')
        self.assertEqual(mode_table.MODES['companion']['look']['eye'], 'v18')
        for m in mode_table.MODES.values():
            self.assertTrue(m['look']['label'].isascii() and len(m['look']['label']) <= 10)
```

（导入照 `tests/test_mind_speech_api.py` 开头的 `sys.path` 写法：`from mind import modes, mode_table`。）

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_modes -v`
Expected: FAIL，`ImportError: cannot import name 'modes'`

- [ ] **Step 3: 写 `mode_table.MODES` 和 `server/mind/modes.py`**：表里每行的字段照 spec 3.1、3.2；`transition` 写成一张"(模式, 输入) → 处理函数"的表，先查模式自己的 `inputs`，再查通用规则（进入词、`select`、`direction`、`long`、`boot`）。

- [ ] **Step 4: 运行，确认通过**

Run: `python3 -B -m unittest tests.test_mind_modes -v`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add server/mind/mode_table.py server/mind/modes.py tests/test_mind_modes.py
git commit -m "mind: mode table and pure mode transitions (modes step 1)"
```

---

### Task A2: 模式的存储、去重、前置检查、开机默认、设置超时

**Files:**
- Create: `server/mind/mode_store.py`
- Modify: `server/mind/store.py`（`ensure_indexes()` 加 `mode_log` 的 `(at)` 和 `(device, boot, dev_seq)` 索引，函数内导入）
- Test: `tests/test_mind_mode_store.py`

**Interfaces:**
- Consumes: Task A1。
- Produces: `class ModeStore(db, *, guard=None, speaking_fn=lambda: False, clock_ms=lambda: int(time.time() * 1000))`：
  - 启动时读 `hello.mode {_id: 'current'}`（含 `seq`、`billing`）；没有就写 `talk`、`seq = 1`、`billing = True`、`by = 'boot'`。
  - `apply(input, value, *, source, device=None, boot=None, dev_seq=None, age_ms=0, sid=None, now_ms=None) -> {snapshot, result, effects}`：模块级锁内：
    1. 设备输入按 `(device, boot, dev_seq)` 去重：重复的返回第一次的结果，不再执行；
    2. `age_ms > 3000` → `stale`；
    3. `transition(..., speaking=speaking_fn())`；
    4. **前置检查**：对 `say_round`、`prefetch_on` 这两种要花钱的效果调 `guard(effect, meta) -> code | None`（`meta = {source, input, device, sid, mode}`）；返回了代码（`busy`、`too_soon`、`mouth_not_tts`、`billing_off`）就把这个效果去掉、`result.code` 换成它；模式变化不受影响；
    5. 状态变了才 `seq += 1` 并写 `hello.mode`；每次（含没变的）追加一条 `hello.mode_log {at, source, device, input, value, from, to, code, seq, effects}`，同时存去重结果。
    锁外按顺序调 `on_change` 回调 `f(old_state, new_state, effects, meta, now_ms)`：只负责**执行**（停说话、关提前写、开始一轮），不再决定结果。
  - `set_billing(on, *, source, now_ms) -> snapshot`：值变了才 `seq += 1`、写库、记日志（`input = 'billing'`）。总闸的读写只经过这里，所以 ear 和机器人总能看到变化。
  - `robot_event(boot, conn, event, applied_seq=None, now_ms)`：`hello` 记下 `conn` 为当前连接；`boot` 和库里记的不同 → `apply('boot', source='boot')` 并记新 `boot`；`applied` 和 `closed` 只在 `conn` 等于当前连接时生效（旧连接晚到的 `closed` 忽略）。
  - `tick(now_ms)`：`settings` 里最后一次输入超过 300 秒 → `apply('timeout', source='timeout')`。
  - `current() -> snapshot`、`seq() -> int`、`billing() -> bool`（只读内存）、`log(limit=10)`、`robot()`。

- [ ] **Step 1: 写失败的测试**

```python
class ModeStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(); self.ms = ModeStore(self.db, clock_ms=lambda: 0)

    def test_starts_in_talk_and_persists(self):
        self.assertEqual((self.ms.current()['mode'], self.ms.current()['billing']), ('talk', True))
        self.ms.apply('play', None, source='page', now_ms=10)
        self.assertEqual(ModeStore(self.db).current()['mode'], 'chat')        # a mind restart keeps the mode and seq

    def test_device_input_dedup(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=5, now_ms=10)
        a = self.ms.apply('play', None, **kw)
        b = self.ms.apply('play', None, **kw)
        self.assertEqual(a['snapshot']['seq'], b['snapshot']['seq'])
        self.assertEqual(self.db.count('mode_log'), 1)

    def test_stale_input_dropped(self):
        r = self.ms.apply('play', None, source='dualkey', device='dualkey-aa', boot='x', dev_seq=6, age_ms=4000, now_ms=10)
        self.assertEqual((r['result']['code'], self.ms.current()['mode']), ('stale', 'talk'))

    def test_guard_decides_paid_effects(self):
        ms = ModeStore(FakeDB(), guard=lambda effect, meta: 'too_soon' if effect == 'say_round' else None)
        r = ms.apply('forward', None, source='asr', device='robot', boot='b', dev_seq=1, now_ms=1)
        self.assertEqual((r['result']['code'], r['effects']), ('too_soon', []))

    def test_billing_change_bumps_seq(self):
        s0 = self.ms.seq()
        snap = self.ms.set_billing(False, source='quick', now_ms=5)
        self.assertEqual((snap['seq'], snap['billing']), (s0 + 1, False))
        self.assertEqual(self.ms.set_billing(False, source='page', now_ms=6)['seq'], s0 + 1)   # no change, same seq

    def test_new_robot_boot_returns_to_talk(self):
        self.ms.robot_event('b1', 1, 'hello', now_ms=1)
        self.ms.apply('start', None, source='page', now_ms=2)
        self.ms.robot_event('b1', 2, 'hello', now_ms=3)                      # reconnect, same boot
        self.assertEqual(self.ms.current()['mode'], 'event')
        self.ms.robot_event('b2', 3, 'hello', now_ms=4)                      # rebooted
        self.assertEqual((self.ms.current()['mode'], self.ms.current()['by']), ('talk', 'boot'))

    def test_old_connection_close_ignored(self):
        self.ms.robot_event('b1', 1, 'hello', now_ms=1)
        self.ms.robot_event('b1', 2, 'hello', now_ms=2)
        self.ms.robot_event('b1', 1, 'closed', now_ms=3)
        self.assertTrue(self.ms.robot()['connected'])

    def test_settings_timeout(self):
        self.ms.apply('open', None, source='page', now_ms=1000)
        self.ms.tick(1000 + 299_000); self.assertEqual(self.ms.current()['mode'], 'settings')
        self.ms.tick(1000 + 301_000); self.assertEqual(self.ms.current()['mode'], 'talk')

    def test_change_hooks_get_effects_and_meta(self):
        seen = []; self.ms.on_change.append(lambda o, n, e, m, t: seen.append((o['mode'], n['mode'], e, m['source'])))
        self.ms.apply('backward', None, source='page', now_ms=1)
        self.assertEqual(seen, [('talk', 'idle', ['stop_speech', 'talk_leave'], 'page')])

    def test_seq_only_grows_on_change(self):
        s0 = self.ms.seq(); self.ms.apply('pause', None, source='page', now_ms=1)
        self.assertEqual(self.ms.seq(), s0)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_mode_store -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mind.mode_store'`

- [ ] **Step 3: 实现 `server/mind/mode_store.py`，改 `store.py`**

- [ ] **Step 4: 运行，确认通过；跑全部 mind 测试**

Run: `python3 -B -m unittest tests.test_mind_mode_store -v && python3 -B -m unittest discover -s tests -p 'test_mind_*.py'`
Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add server/mind/mode_store.py server/mind/store.py tests/test_mind_mode_store.py
git commit -m "mind: mode store with log, dedup, guard, billing seq, boot default and settings timeout"
```

---

### Task A3: 计费总闸和预设（服务器那一半）

**Files:**
- Create: `server/mind/billing.py`
- Modify: `server/mind/mode_table.py`（加 `PRESETS`）
- Test: `tests/test_mind_billing.py`

**Interfaces:**
- Produces:
  - `mode_table.PRESETS`：`{n: {name, server: [...], robot: {hearing?, vision?}}}`；`server` 的动作名：`billing_off`（含关付费开关、关提前写和连续说、停说话）、`billing_on`、`paid_senses_on`（听悟、识别、纪要、情景）、`mouth_tts`、`talk_on`（提前写、连续说；不在脱口秀时跳过并在 `toast` 里说）、`toggle_tingwu`、`toggle_prefetch`、`mode_idle`。按 spec 第 7 节表：0 = `[billing_off]`；1 = `[billing_off]` + `robot {hearing:'local', vision:'upload'}`；2 = `[toggle_tingwu]`；3 = `[toggle_prefetch]`；4 = `[mode_idle]`；9 = `[billing_on, paid_senses_on, mouth_tts, talk_on]` + 同样的 `robot`；5–8 空。
  - `class Billing(ctx)`：`ctx` 提供 `mode_store`、`set_switch(name, on)`、`set_mind(on)`、`set_mouth(mode)`、`prefetch`、`auto`、`cancel()`、`mode()`。
    - `off(source, now_ms)`：`mode_store.set_billing(False, ...)`，然后关听悟、识别、纪要、情景、提前写、连续说，停说话。
    - `on(source, now_ms)`：只 `mode_store.set_billing(True, ...)`。
    - `allowed() -> bool` = `mode_store.billing()`。
  - `apply_preset(n, billing, *, source, now_ms) -> {result, robot}`：`toggle_tingwu` 在总闸关时返回 `billing_off`；`toggle_prefetch` 不在脱口秀返回 `only_talk`；5–8 返回 `undefined_preset`；`source` 不是 `page` 且预设带 `robot` 时，`result.code = 'senses_on_page'`（服务器那一半照做）。

- [ ] **Step 1: 写失败的测试**

```python
class BillingTest(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx(tingwu=True, eye=True, notes=True, mind=True, prefetch=True, auto=True, mode='talk')
        self.b = Billing(self.ctx)

    def test_off_turns_every_paid_switch_off_and_bumps_seq(self):
        s0 = self.ctx.mode_store.seq()
        self.b.off('quick', 1)
        self.assertEqual(self.ctx.state(), dict(tingwu=False, eye=False, notes=False, mind=False,
                                                prefetch=False, auto=False, cancelled=True))
        self.assertEqual((self.ctx.mode_store.seq(), self.b.allowed()), (s0 + 1, False))

    def test_on_restores_nothing(self):
        self.b.off('page', 1); self.b.on('page', 2)
        self.assertEqual((self.b.allowed(), self.ctx.tingwu), (True, False))

    def test_preset_one_from_robot_says_use_page(self):
        r = apply_preset(1, self.b, source='asr', now_ms=1)
        self.assertEqual((r['result']['code'], r['robot']), ('senses_on_page', {'hearing': 'local', 'vision': 'upload'}))
        self.assertFalse(self.b.allowed())

    def test_toggle_tingwu_needs_billing(self):
        self.b.off('page', 1)
        self.assertEqual(apply_preset(2, self.b, source='asr', now_ms=2)['result']['code'], 'billing_off')

    def test_toggle_prefetch_only_in_talk(self):
        self.ctx.mode_value = 'idle'
        self.assertEqual(apply_preset(3, self.b, source='asr', now_ms=1)['result']['code'], 'only_talk')

    def test_nine_is_everything_paid(self):
        self.b.off('page', 1)
        apply_preset(9, self.b, source='page', now_ms=2)
        self.assertEqual((self.b.allowed(), self.ctx.tingwu, self.ctx.eye, self.ctx.notes, self.ctx.mind,
                          self.ctx.mouth, self.ctx.prefetch, self.ctx.auto), (True, True, True, True, True, 'tts', True, True))

    def test_nine_outside_talk_skips_talk_behaviors(self):
        self.ctx.mode_value = 'idle'
        r = apply_preset(9, self.b, source='page', now_ms=1)
        self.assertEqual((self.ctx.prefetch, 'TALK' in r['result']['toast']), (False, True))

    def test_undefined(self):
        self.assertEqual(apply_preset(6, self.b, source='page', now_ms=1)['result']['code'], 'undefined_preset')
```

（`FakeCtx` 在测试文件里定义：记录每个开关，内含一个用 `FakeDB` 的真 `ModeStore`；`ctx.prefetch.set_enabled(True)` 在 `mode_value != 'talk'` 时由 Billing 自己跳过。）

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_billing -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mind.billing'`

- [ ] **Step 3: 实现 `billing.py` 和 `mode_table.PRESETS`**

- [ ] **Step 4: 运行，确认通过；跑全部 mind 测试**

Run: `python3 -B -m unittest tests.test_mind_billing -v && python3 -B -m unittest discover -s tests -p 'test_mind_*.py'`
Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add server/mind/billing.py server/mind/mode_table.py tests/test_mind_billing.py
git commit -m "mind: billing master switch (off + no re-enable) and 0-9 presets, server half"
```

---

### Task A4: mind 接口、前置检查和钩子、按模式拦说话、DualKey 接入

**Files:**
- Modify: `server/mind/api.py`（文件头接口列表；`get()`/`post()`；`do_POST` 白名单 `:632-633`；`/switches` `:519-534`；`/switch` `:535-539`；说话类分支 `:430-516`；`/signal` 分支）
- Modify: `server/mind/runner.py:125-200`、`say_prefetch.py`、`say_auto.py`、`server/mind/README.md`
- Test: `tests/test_mind_mode_api.py`

**Interfaces:**
- Consumes: A1–A3。
- Produces: 接口一节的 mind 路径。另外：
  - `runner.py` 建 `ModeStore(db, guard=..., speaking_fn=...)` 和 `Billing(...)`，放进 `ctx['mode_store']`、`ctx['billing']`；主循环每轮 `mode_store.tick(now)`。
  - `guard(effect, meta)`：`say_round` → 总闸关 `billing_off`；嘴不在 TTS `mouth_not_tts`；已有一轮在跑 `busy`；离上一轮开始不到 10 秒 `too_soon`。`prefetch_on` → 总闸关 `billing_off`。`speaking_fn` = mind 有没有正在说的一句（`speak` 模块已经记着待播的那句，`cancel_pending` 用的就是它，`speak.py:41-46`）。
  - 钩子（只执行）：`stop_speech` → `speak.cancel_pending(tts)`；`talk_leave` → `say_worker.set_enabled(False)`、`say_auto.set_enabled(False)`；`say_round` → 后台线程跑 `speak.say_round(..., trigger={'source': meta.source, 'input': meta.input, 'sid': meta.sid})`；`prefetch_on/off` → `say_worker.set_enabled(...)`。
  - 拦截：`/say`、`/say/round`、`/say/play` 不在脱口秀 → 409 `只在脱口秀模式`；`/say/prefetch`、`/say/auto` 打开时同上；总闸关时以上和 `/voice/try`、`/role/try` → 409 `计费关着`；`/switches` 打开 `tingwu`/`eye`/`notes`、`/switch` 打开情景，总闸关时 → 409 `计费关着`；`/say/stop` 永远放行。
  - `/switches {name: 'billing', on}`：关 = `billing.off`；开 = `billing.on`。`switches()` 输出多 `billing: {on, at}`。
  - `/signal`：`put_signal` 之后，`kind == 'mode'` → `mode_store.apply('direction', value.direction, source='dualkey', device, boot, dev_seq=seq, age_ms=body.age_ms, sid)`；`kind == 'click'` → `single`/`double`/`long` 对应 `tap`/`double_tap`/`long`；结果写进那条信号的 `result`（`update_one` 只加 `result` 字段）。打分（`rating`）不受 3 秒规则影响。
  - `say_prefetch.Worker`、`say_auto.Worker` 加 `mode_fn=lambda: 'talk'`，运行循环里 `mode_fn() != 'talk'` 就跳过并记 `last = {reason: 'not_talk'}`。
  - README 加"模式（第一步）"：集合、接口、"进入模式不花钱"、总闸的做法；写明 `probe.py`、`runner.py --rebuild --fusion` 这类一次性命令行不受总闸约束。

- [ ] **Step 1: 写失败的测试** `tests/test_mind_mode_api.py`（结构照 `tests/test_mind_speech_api.py`：起 `api.make_server(..., ctx={...})`，`call(path, body=None) -> (status, json)`；`tts`、`say_worker`、`say_auto`、`say_round` 用测试替身记录调用；时钟可注入）

```python
    def test_page_switches_mode_and_reads_back(self):
        s, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'crowd'}})
        self.assertEqual((s, r['snapshot']['mode'], r['snapshot']['look']['eye']), (200, 'crowd', 'v14'))
        self.assertEqual(self.call('/mode')[1]['log'][0]['to'], 'crowd')
        self.assertEqual(self.call('/mode/seq')[1]['seq'], r['snapshot']['seq'])

    def test_leaving_talk_stops_paid_behaviors(self):
        self.worker.set_enabled(True, 1); self.auto.set_enabled(True, 1)
        self.call('/mode', {'input': 'backward'})
        self.assertEqual((self.worker.enabled(), self.auto.enabled(), self.tts.stops >= 1), (False, False, True))

    def test_say_round_only_in_talk(self):
        self.call('/mode', {'input': 'backward'})
        self.assertEqual(self.call('/say/round', {})[0], 409)
        self.assertEqual(self.call('/say/prefetch', {'enabled': True})[0], 409)
        self.assertEqual(self.call('/say/stop', {})[0], 200)

    def test_billing_change_bumps_seq(self):
        s0 = self.call('/mode/seq')[1]['seq']
        self.call('/switches', {'name': 'billing', 'on': False})
        self.assertEqual(self.call('/mode/seq')[1]['seq'], s0 + 1)
        self.assertFalse(self.call('/mode')[1]['snapshot']['billing'])

    def test_billing_off_blocks_paid_switches_and_speech(self):
        self.call('/switches', {'name': 'tingwu', 'on': True})
        self.call('/switches', {'name': 'billing', 'on': False})
        sw = self.call('/switches')[1]
        self.assertEqual((sw['billing']['on'], sw['tingwu']['on']), (False, False))
        self.assertEqual(self.call('/switches', {'name': 'eye', 'on': True})[0], 409)
        self.assertEqual(self.call('/switch', {'enabled': True})[0], 409)
        self.assertEqual(self.call('/voice/try', {'voice': 'v1'})[0], 409)

    def test_robot_forward_in_talk_starts_one_round_then_too_soon(self):
        body = {'device': 'robot', 'boot': 'b1', 'seq': 1, 'age_ms': 100, 'source': 'asr', 'input': 'forward'}
        self.assertEqual(self.call('/mode/input', body)[1]['result']['code'], 'ok')
        self.wait_rounds(1)
        self.assertEqual(self.say_calls[0]['trigger']['source'], 'asr')
        body['seq'] = 2
        self.assertEqual(self.call('/mode/input', body)[1]['result']['code'], 'too_soon')

    def test_tap_while_speaking_stops(self):
        self.speaking = True
        body = {'device': 'robot', 'boot': 'b1', 'seq': 3, 'age_ms': 0, 'source': 'touch', 'input': 'tap'}
        self.call('/mode/input', body)
        self.assertEqual((self.tts.stops >= 1, len(self.say_calls)), (True, 0))

    def test_preset_returns_robot_half_only_for_page(self):
        self.assertEqual(self.call('/preset', {'n': 1, 'source': 'page'})[1]['robot'], {'hearing': 'local', 'vision': 'upload'})

    def test_dualkey_direction_signal_switches_mode(self):
        self.call('/signal', {'kind': 'mode', 'value': {'direction': 'left'}, 'age_ms': 120,
                              'source': {'device': 'dualkey-aa', 'boot': 'x', 'seq': 3}})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'crowd')
        self.assertEqual(self.db['owner_signals'].docs[-1]['result']['code'], 'ok')

    def test_dualkey_stale_direction_ignored(self):
        self.call('/signal', {'kind': 'mode', 'value': {'direction': 'left'}, 'age_ms': 45_000,
                              'source': {'device': 'dualkey-aa', 'boot': 'x', 'seq': 4}})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')
        self.assertEqual(self.db['owner_signals'].docs[-1]['result']['code'], 'stale')

    def test_robot_boot_event(self):
        self.call('/mode/robot', {'boot': 'b1', 'conn': 1, 'event': 'hello'})
        self.call('/mode', {'input': 'play'})
        self.call('/mode/robot', {'boot': 'b2', 'conn': 2, 'event': 'hello'})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')

    def test_bad_input(self):
        self.assertEqual(self.call('/mode', {'input': 'fly'})[0], 400)
        self.assertEqual(self.call('/mode/input', {'device': 'robot', 'seq': 'x', 'input': 'play'})[0], 400)
        self.assertEqual(self.call('/preset', {'n': 10})[0], 400)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_mode_api -v`
Expected: FAIL，新路径 404，`/switches` 的 `billing` 400

- [ ] **Step 3: 实现**：api 分支、白名单、`guard` 和钩子、拦截、`/signal` 接入；`runner.py` 接线；两个 worker 加 `mode_fn`；README。

- [ ] **Step 4: 运行，确认通过；跑全部 Python 测试**

Run: `python3 -B -m unittest tests.test_mind_mode_api -v && python3 -B -m unittest discover -s tests -v`
Expected: 全部 OK（已有的旧依赖跳过项照旧跳过）

- [ ] **Step 5: Commit**

```bash
git add server/mind/api.py server/mind/runner.py server/mind/say_prefetch.py server/mind/say_auto.py server/mind/README.md tests/test_mind_mode_api.py
git commit -m "mind: mode endpoints, guard and hooks, talk-only speech, billing switch, DualKey mode signals"
```

---

### Task A5: ear 在 `/mouth` 上推快照、转模式输入（D0 通过之后）

**Files:**
- Modify: `server/ear/mouth_link.py`（A0 的 `MouthLink`）、`server/ear/ear_server.py`
- Test: `tests/test_mouth_link.py`

**Interfaces:**
- Consumes: mind 的 `/mode/input`、`/mode/robot`、`/mode/seq`、`/mode`（POST 用 `signal_link.mind_poster` 的写法，`server/ear/signal_link.py:20-36`；GET 另写一个同样"不抛异常"的小函数）。
- Produces（在 A0 上加）：
  - 握手后：`/mode/robot {boot, conn, event:'hello'}`，把返回的快照作为 `{"type":"mode",...}` 发给机器人。
  - 机器人的 `mode_input` → `/mode/input`（补上 `device='robot'`、`boot`）→ 回 `{"type":"mode_result","seq":N,"code":...,"toast":...}`，再发新快照。`mode_applied` → `/mode/robot {conn, event:'applied'}`。这两种都不转给 soul-tts。
  - 一个全局轮询任务：每 1 秒 `GET /mode/seq`，变了就 `GET /mode` 并推给所有 `/mouth` 链接；每 60 秒无论如何推一次。mind 不在：不推，不断机器人。
  - 机器人断开：`/mode/robot {conn, event:'closed'}`。

- [ ] **Step 1: 写失败的测试**（假的 mind 用一个按路径记录调用的函数；断言按路径筛，不靠调用顺序）

```python
    async def test_pushes_mode_after_hello(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            mode = await self.recv_type(ws, "mode")
            self.assertEqual(mode["mode"], "talk")
        self.assertEqual(self.mind_calls_to("/mode/robot")[0]["event"], "hello")

    async def test_mode_input_goes_to_mind_not_soul_tts(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 4, "age_ms": 50, "source": "asr", "input": "play"}))
            res = await self.recv_type(ws, "mode_result")
            self.assertEqual((res["seq"], res["code"]), (4, "ok"))
        self.assertEqual(self.mind_calls_to("/mode/input")[0]["boot"], "b1")
        self.assertNotIn("mode_input", "".join(self.upstream_text))

    async def test_page_change_reaches_robot_with_upstream_down(self):
        await self.stop_upstream()
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
            self.mind_set(mode="chat", seq=self.mind_seq + 1)               # changed on the page
            self.assertEqual((await self.recv_type(ws, "mode", timeout=3))["mode"], "chat")
            self.assertIsNone(ws.close_code)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.private/venv-ear/bin/python -B -m unittest tests.test_mouth_link -v`
Expected: FAIL（没有 `mode` 消息）

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行，确认通过；跑 ear 全部测试**

Run: `.private/venv-ear/bin/python -B -m unittest discover -s tests -v`
Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add server/ear/mouth_link.py server/ear/ear_server.py tests/test_mouth_link.py
git commit -m "ear: push mode snapshots on /mouth and relay mode inputs to mind"
```

---

### Task A6: ear 给 DualKey 推摇杆灯

**Files:**
- Modify: `server/ear/signal_link.py`、`server/ear/ear_server.py:977-989`
- Test: `tests/test_signal_link.py`

**Interfaces:**
- Consumes: A5 的全局轮询（取到新快照后也通知所有 `/signal` 链接）。
- Produces: `SignalLink.mode_message(snapshot) -> {"type":"mode","current":<mode>}`；快照变化和 DualKey `hello` 之后各发一次。

- [ ] **Step 1: 写失败的测试**

```python
    def test_mode_message(self):
        self.assertEqual(SignalLink.mode_message({'mode': 'crowd'}), {'type': 'mode', 'current': 'crowd'})
        self.assertEqual(SignalLink.mode_message({'mode': 'idle'}), {'type': 'mode', 'current': 'idle'})
```

另加一条异步测试：DualKey 连上并 `hello` 后，收到一条 `mode`。

- [ ] **Step 2: 运行，确认失败**

Run: `.private/venv-ear/bin/python -B -m unittest tests.test_signal_link -v`
Expected: FAIL，`AttributeError: ... 'mode_message'`

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行，确认通过**

Run: `.private/venv-ear/bin/python -B -m unittest tests.test_signal_link -v`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add server/ear/signal_link.py server/ear/ear_server.py tests/test_signal_link.py
git commit -m "ear: push the current mode to DualKey for its joystick light"
```

---

## B. 页面

### Task B1: 模式条和快捷设置面板

**Files:**
- Create: `web/monitor/public/mode-ui.js`、`web/monitor/public/mode.css`
- Modify: `web/monitor/public/index.html:6`、`:14`，`web/monitor/public/mind.html`（开关条下面加 `<nav id="modebar" class="modebar" aria-label="模式"></nav>`，`<head>` 引 `/mode.css` 和 `<script type="module" src="/mode-ui.js">`）
- Modify: `web/monitor/mind.mjs:10-12`、`web/monitor/server.mjs:17`、`:54`、`:84`、`web/monitor/static-files.test.mjs`
- Test: `web/monitor/mode-ui.test.mjs`、`web/monitor/mind.test.mjs`

**Interfaces:**
- Consumes: `GET /api/mind/mode`、`POST /api/mind/mode`、`POST /api/mind/preset`、`POST /api/mind/switches`（billing）；预设的机器人那一半**直接复用** `switches-ui.js` 的 `runLevel('local', robot, post)` 和 `runVision('upload', robot, post)`（`web/monitor/public/switches-ui.js:106-111`），和开关条的"本地""上传"档是同一组请求、同一个执行者。
- Produces（`mode-ui.js` 导出）：
  - `modeView(state) -> {known, mode, name, label, color, since, by, synced: 'yes'|'waiting'|'offline', billing, event}`：`synced` 由 `robot.connected` 和 `robot.applied_seq === snapshot.seq` 决定。
  - `modeBarHtml(view, log)`：当前模式、"机器人已显示 / 等机器人 / 机器人离线（连上后照这个显示）"、九个按钮（当前高亮，再点 = 离开）、最近 10 次切换。所有文字经转义。
  - `quickHtml(view)`：计费总闸开关（关着时写"现在不花钱"）、0–9 十个键（5–8 灰）。
  - `presetRobotSteps(robot) -> [['hearing','local'] | ['vision','upload']]`。
  - `class ModeBar(doc)`：每 2 秒读 `/api/mind/mode`；点按钮发请求；按预设时先发服务器那一半，再按 `robot` 跑 `runLevel` / `runVision`，任何一步失败就照实写哪一步没成；409 时把 mind 的错误文字写在条上；广播 `modechange` 事件给台词面板。
  - `mind.mjs`：`PATHS` 加 `/mode`、`/mode/seq`；`POST_PATHS` 加 `/mode`、`/preset`（**不**放行 `/mode/input`、`/mode/robot`：只有 ear 用）。`server.mjs` 两个列表同样。

- [ ] **Step 1: 写失败的测试** `web/monitor/mode-ui.test.mjs`（加载方式照 `say-ui.test.mjs`）

```js
test('synced state follows robot ack',()=>{
 const st={snapshot:{seq:5,mode:'crowd',look:{label:'MEET',color:'30D060'},billing:true,since:1,by:'dualkey'},robot:{connected:true,applied_seq:5},log:[]};
 assert.equal(ui.modeView(st).synced,'yes');
 assert.equal(ui.modeView({...st,robot:{connected:true,applied_seq:4}}).synced,'waiting');
 assert.equal(ui.modeView({...st,robot:{connected:false,applied_seq:5}}).synced,'offline');
});
test('bar shows nine buttons and escapes log text',()=>{
 const h=ui.modeBarHtml(ui.modeView({snapshot:{seq:1,mode:'talk',look:{label:'TALK',color:'E040A0'},billing:true},robot:{connected:true,applied_seq:1}}),
  [{at:1,source:'page',from:'talk',to:'<img src=x>',code:'ok'}]);
 assert.equal((h.match(/data-mode=/g)||[]).length,9);
 assert.doesNotMatch(h,/<img/);
});
test('quick panel greys undefined presets and says when nothing costs',()=>{
 const h=ui.quickHtml({billing:false});
 assert.match(h,/现在不花钱/);
 for(const n of [5,6,7,8])assert.match(h,new RegExp(`data-preset="${n}"[^>]*disabled`));
});
test('robot half of a preset uses the switch bar tiers',()=>{
 assert.deepEqual(ui.presetRobotSteps({hearing:'local',vision:'upload'}),[['hearing','local'],['vision','upload']]);
 assert.deepEqual(ui.presetRobotSteps(undefined),[]);
});
```

`mind.test.mjs` 加：`MindProxy.check('/mode')`、`check('/mode/seq')` 放行；`post('/mode/input')`、`post('/mode/robot')` 返回 400。

- [ ] **Step 2: 运行，确认失败**

Run: `node --test web/monitor/mode-ui.test.mjs web/monitor/mind.test.mjs web/monitor/static-files.test.mjs`
Expected: FAIL，`mode-ui.js` 不存在

- [ ] **Step 3: 实现**，改两个 html、`mind.mjs`、`server.mjs`、`static-files.test.mjs`

- [ ] **Step 4: 运行，确认通过；跑全部观察台测试**

Run: `node --test web/monitor/*.test.mjs`
Expected: 全部 pass

- [ ] **Step 5: 本机看一眼页面**：照 `web/monitor/README.md` 启动观察台，首页和情景页都有模式条；连不上 mind 时写"模式未知"而不是白屏；窄屏 400 px 不横向滚动。

- [ ] **Step 6: Commit**

```bash
git add web/monitor/public/mode-ui.js web/monitor/public/mode.css web/monitor/public/index.html web/monitor/public/mind.html web/monitor/mind.mjs web/monitor/server.mjs web/monitor/static-files.test.mjs web/monitor/mode-ui.test.mjs web/monitor/mind.test.mjs
git commit -m "monitor: mode bar and quick settings (billing, presets 0-9)"
```

---

### Task B2: 台词面板跟着模式

**Files:**
- Modify: `web/monitor/public/say-ui.js:7-12`、`:146`
- Test: `web/monitor/say-ui.test.mjs`

**Interfaces:**
- Produces: `roundControlsHtml(prefetchOn, familyOn, autoOn, gate)`：`gate = {talk: bool, billing: bool}`；不在脱口秀时"说一轮"、提前写、连续说禁用，旁边写"只在脱口秀模式"；总闸关时写"计费关着"。"别说了"永远可点。台词面板听 `ModeBar` 的 `modechange` 事件刷新。

- [ ] **Step 1: 写失败的测试**

```js
test('round controls follow the mode and billing',()=>{
 assert.match(ui.roundControlsHtml(false,false,false,{talk:false,billing:true}),/data-say-round="1"[^>]*disabled/);
 assert.match(ui.roundControlsHtml(false,false,false,{talk:false,billing:true}),/只在脱口秀模式/);
 assert.match(ui.roundControlsHtml(false,false,false,{talk:true,billing:false}),/计费关着/);
 assert.doesNotMatch(ui.roundControlsHtml(false,false,false,{talk:false,billing:false}),/data-say-stop="1"[^>]*disabled/);
});
```

- [ ] **Step 2: 运行，确认失败**

Run: `node --test web/monitor/say-ui.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现**（`gate` 缺省为 `{talk:true,billing:true}`，旧调用不变）

- [ ] **Step 4: 运行，确认通过**

Run: `node --test web/monitor/*.test.mjs`
Expected: 全部 pass

- [ ] **Step 5: Commit**

```bash
git add web/monitor/public/say-ui.js web/monitor/say-ui.test.mjs
git commit -m "monitor: talk-show controls only in talk mode and with billing on"
```

---

## C. 固件（ESP32 一侧；构建可以做，写设备前停下问主人）

所有主机测试的编译写法：

```
c++ -std=c++17 -Wall -Wextra -Werror -I upstream/StackChan/firmware/main/collector \
    -I upstream/StackChan/firmware/components/ArduinoJson/src tests/<name>_test.cpp -o <tmp>/<name>
```

每个 `tests/test_<name>.py` 照 `tests/test_mouth_liveness.py` 包一层，这样 `python3 -B -m unittest discover -s tests` 会跑它们。

### Task C1: `mode_wire.h`：快照、输入、回执、输入队列

**Files:**
- Create: `fw/mode_wire.h`、`tests/mode_wire_test.cpp`、`tests/test_mode_wire.py`

**Interfaces:**
- Produces（命名空间 `collector::modes`，只依赖 ArduinoJson 和标准库）：
  - `enum class Eye : uint8_t { LaughingMan, V05, V14, V18, PageSubtitle, PageSettings, PageIdle };`
  - `enum class Pool : uint8_t { Default, Lively };`、`enum class TouchAct : uint8_t { Server, Badge, None };`
  - `struct Snapshot { uint32_t seq; char mode[12]; char dir[8]; bool hosted, billing; Eye eye; Pool pool; char label[12]; uint32_t color; TouchAct tap, dbl; };`
  - `enum class Parse { Applied, Same, Bad };`
  - `Parse parse_snapshot(ArduinoJson::JsonObjectConst, Snapshot& cur, bool first_after_hello)`：字段缺失、类型不对、`look.eye` 不在词表、`label` 不是 ASCII → `Bad`，`cur` 不动；`seq` 等于当前 → `Same`（不算坏，不动）；`seq` 小于当前 → 只有 `first_after_hello` 时才 `Applied`，否则 `Same`。
  - `std::string mode_input(uint32_t seq, uint32_t age_ms, const char* source, const char* input, int value /* -1 = null */)`、`mode_input_select(...)`、`mode_applied(uint32_t seq)`。
  - `class InputQueue`：容量 8，满了丢最旧的并计数；存储由调用方给（PSRAM）。

- [ ] **Step 1: 写失败的测试** `tests/mode_wire_test.cpp`

```cpp
int main() {
    using namespace collector::modes;
    using namespace ArduinoJson;
    Snapshot s{};
    JsonDocument d;
    deserializeJson(d, R"({"seq":3,"mode":"chat","dir":null,"host":null,"billing":true,
      "look":{"eye":"v05","pool":null,"label":"CHAT","color":"F0B000"},"touch":{"tap":"server","double":"server"}})");
    assert(parse_snapshot(d.as<JsonObjectConst>(), s, false) == Parse::Applied && s.eye == Eye::V05 && s.seq == 3 && s.color == 0xF0B000);
    assert(parse_snapshot(d.as<JsonObjectConst>(), s, false) == Parse::Same);             // 60 s re-push: not bad
    deserializeJson(d, R"({"seq":4,"mode":"chat","look":{"eye":"v99","label":"X","color":"000000"},"touch":{"tap":"server","double":"server"}})");
    assert(parse_snapshot(d.as<JsonObjectConst>(), s, false) == Parse::Bad && s.seq == 3); // bad snapshot keeps last
    deserializeJson(d, R"({"seq":1,"mode":"idle","billing":true,"look":{"eye":"page_idle","label":"IDLE","color":"505050"},"touch":{"tap":"badge","double":"server"}})");
    assert(parse_snapshot(d.as<JsonObjectConst>(), s, false) == Parse::Same && s.seq == 3); // older seq ignored...
    assert(parse_snapshot(d.as<JsonObjectConst>(), s, true) == Parse::Applied && s.seq == 1); // ...unless first after hello
    assert(mode_input(7, 40, "asr", "play", -1) ==
           R"({"type":"mode_input","seq":7,"age_ms":40,"source":"asr","input":"play","value":null})");
    std::string slots[8]; InputQueue q(slots, 8);
    for (uint32_t i = 1; i <= 10; ++i) q.push(mode_input(i, 0, "asr", "play", -1));
    assert(q.size() == 8 && q.dropped() == 2 && q.front().find("\"seq\":3") != std::string::npos);
    return 0;
}
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mode_wire -v`
Expected: FAIL，`mode_wire.h: No such file or directory`

- [ ] **Step 3: 实现 `fw/mode_wire.h`**

- [ ] **Step 4: 运行，确认通过**

Run: `python3 -B -m unittest tests.test_mode_wire -v`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add upstream/StackChan/firmware/main/collector/mode_wire.h tests/mode_wire_test.cpp tests/test_mode_wire.py
git commit -m "collector: mode snapshot and input wire format (modes step 1)"
```

---

### Task C2: 口令改绑

**Files:**
- Create: `fw/asr_mode_words.h`、`tests/asr_mode_words_test.cpp`、`tests/test_asr_mode_words.py`
- Modify: `fw/asr_unit.cpp:127-133`、`:218`（`info.asr` 说明）、`fw/interaction_policy.h:8-12`、`fw/hearing_policy.h:10`、`fw/voice_policy.h:25`
- Modify tests: `tests/interaction_policy_test.cpp`、`tests/hearing_policy_test.cpp` 和断言 `next` 播台词的测试

**Interfaces:**
- Produces: `const char* asr_mode_input(uint8_t id, bool has_value, int* digit)`：`0x07` forward、`0x09` backward、`0x16` play、`0x17` pause、`0x10` open、`0x11` close、`0x12` start、`0x13` stop、`0x1B` next、`0x1A` previous → 同名输入；`0x20–0x29` → `"digit"` 且 `*digit = 0..9`；其余（含转头词、turn on/off、音量、`0x32` hello、`0x30` ok）→ `nullptr`（照旧本地处理）。编号以 `fw/asr_protocol.h:55-100` 为准。
- 改动：`asr_mode_action` 不再把 `0x10/0x11` 当成追踪开关（`0x14/0x15` 的 ReleaseModes 保留）；`hearing::asr_action` 对 `0x12/0x13` 返回 0（主人 2026-11-14）；`voice_asr_next` 返回 `false`；`asr_unit.cpp` 在分发后加 `if (auto in = asr_mode_input(...)) mode_link_send_input("asr", in, digit);`。开机自动追踪的"说了追踪词就取消"只留 up/down 和转头词（`asr_unit.cpp:127-129`）。

- [ ] **Step 1: 写失败的测试** `tests/asr_mode_words_test.cpp`

```cpp
int main() {
    using namespace collector;
    int d = -1;
    assert(!std::strcmp(asr_mode_input(0x07, false, &d), "forward"));
    assert(!std::strcmp(asr_mode_input(0x17, false, &d), "pause"));
    assert(!std::strcmp(asr_mode_input(0x1B, false, &d), "next"));
    assert(!std::strcmp(asr_mode_input(0x29, false, &d), "digit") && d == 9);
    assert(asr_mode_input(0x01, false, &d) == nullptr);       // up: tracking, local
    assert(asr_mode_input(0x14, false, &d) == nullptr);       // turn on: M12 view, local
    assert(asr_mode_input(0x40, false, &d) == nullptr);       // volume, local
    assert(asr_mode_input(0x32, false, &d) == nullptr);       // hello: unchanged in step 1
    assert(asr_mode_input(0x07, true, &d) == nullptr);        // frames with a value are not words
    assert(interaction::asr_mode_action(0x10, false) == interaction::AsrModeAction::None);
    assert(hearing::asr_action(0x12, false) == 0);
    assert(!voice_asr_next(0x1b, false));
    return 0;
}
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_asr_mode_words -v`
Expected: FAIL，头文件不存在

- [ ] **Step 3: 实现并改旧的三处；把旧测试里"open 开追踪""start 开听觉""next 播台词"的断言改成断言它们不再这样做**

- [ ] **Step 4: 运行，确认通过；跑全部 Python（含所有主机测试）**

Run: `python3 -B -m unittest discover -s tests -v`
Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add upstream/StackChan/firmware/main/collector/asr_mode_words.h upstream/StackChan/firmware/main/collector/asr_unit.cpp upstream/StackChan/firmware/main/collector/interaction_policy.h upstream/StackChan/firmware/main/collector/hearing_policy.h upstream/StackChan/firmware/main/collector/voice_policy.h tests/
git commit -m "collector: ASR mode words go to the server; open/close, start/stop, next lose their old local use"
```

---

### Task C3: 首页手势分类

**Files:**
- Create: `fw/touch_gesture.h`、`tests/touch_gesture_test.cpp`、`tests/test_touch_gesture.py`
- Modify: `fw/screen.cpp:849-897`

**Interfaces:**
- Produces: `class GestureClassifier`：`feed(bool down, uint32_t now_ms, int x, int y)`，输入和真机一样：按着时每帧一个真实坐标，**松手那一帧 `down = false`、坐标 `(-1,-1)`**（`fw/collector.cpp:1766-1769`）；`Gesture take(uint32_t now_ms)` 返回 `None / Tap / DoubleTap / SwipeUp / SwipeDown / SwipeLeft / SwipeRight`。规则：
  - 只用 `x >= 0` 的采样算起点、终点和最大位移（同 `fw/screen.cpp:868`）；松手时用最后一个有效采样。
  - 滑动：主方向 ≥ 55 px、主方向 > 另一方向 × 2、按住 30–1000 ms（同 `fw/screen.cpp:880-884`），加上下方向。
  - 点：按住 30–700 ms、最大位移 ≤ 18 px（同 `:889`）。第一下之后 300 ms 内第二下 = `DoubleTap`；300 ms 到了没有第二下 = `Tap`。
  - 距上次松手不到 180 ms 的按下忽略（同 `:879`）；按着途中有一两帧丢了触摸（`down = false` 后 60 ms 内又按下且位置连续）算同一下【我定】。
- `screen.cpp`：只在 `page == Page::Home` 时用分类器的结果：`Tap`/`DoubleTap` → 按快照的 `touch.tap`/`touch.double`（`Server` 发输入，`Badge` 切 IDLE 的 badge 页，`None` 显示一行提示）；`SwipeUp` → 模式选择页；`SwipeDown` → 快捷设置页；`SwipeLeft` → 摘要页。其余页面的处理照旧。`take()` 在 `screen_tick` 里每帧调一次。

- [ ] **Step 1: 写失败的测试** `tests/touch_gesture_test.cpp`

```cpp
// A finger stroke as the device reports it: one real sample per 20 ms while down, then a release frame at (-1,-1).
static void stroke(GestureClassifier& g, uint32_t t0, int x0, int y0, int x1, int y1, uint32_t held) {
    for (uint32_t t = 0; t <= held; t += 20) g.feed(true, t0 + t, x0 + (x1 - x0) * int(t) / int(held), y0 + (y1 - y0) * int(t) / int(held));
    g.feed(false, t0 + held + 20, -1, -1);
}
static Gesture wait(GestureClassifier& g, uint32_t from, uint32_t until) {
    for (uint32_t t = from; t <= until; t += 10) if (auto r = g.take(t); r != Gesture::None) return r;
    return Gesture::None;
}
int main() {
    { GestureClassifier g; stroke(g, 0, 100, 100, 103, 101, 120); assert(wait(g, 140, 700) == Gesture::Tap); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 100); assert(wait(g, 120, 250) == Gesture::None);   // still waiting
      stroke(g, 320, 101, 100, 101, 100, 80); assert(wait(g, 420, 900) == Gesture::DoubleTap); }
    { GestureClassifier g; stroke(g, 0, 160, 200, 162, 60, 240); assert(wait(g, 260, 700) == Gesture::SwipeUp); }
    { GestureClassifier g; stroke(g, 0, 160, 40, 158, 180, 240); assert(wait(g, 260, 700) == Gesture::SwipeDown); }
    { GestureClassifier g; stroke(g, 0, 250, 120, 100, 125, 240); assert(wait(g, 260, 700) == Gesture::SwipeLeft); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 900); assert(wait(g, 920, 1500) == Gesture::None); } // long hold
    { GestureClassifier g; stroke(g, 0, 100, 100, 170, 140, 240); assert(wait(g, 260, 700) == Gesture::None); }  // diagonal
    { GestureClassifier g;                                       // release jitter: last real sample moved 4 px, still a tap
      g.feed(true, 0, 100, 100); g.feed(true, 60, 101, 100); g.feed(true, 100, 104, 101); g.feed(false, 120, -1, -1);
      assert(wait(g, 130, 700) == Gesture::Tap); }
    { GestureClassifier g;                                       // touch lost for one frame mid-swipe: still one swipe up
      for (uint32_t t = 0; t <= 100; t += 20) g.feed(true, t, 160, 200 - int(t));
      g.feed(false, 120, -1, -1);
      for (uint32_t t = 140; t <= 240; t += 20) g.feed(true, t, 160, 200 - int(t) * 140 / 240);
      g.feed(false, 260, -1, -1);
      assert(wait(g, 270, 800) == Gesture::SwipeUp); }
    return 0;
}
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_touch_gesture -v`
Expected: FAIL，头文件不存在

- [ ] **Step 3: 实现分类器；`screen.cpp` 首页改用它**

- [ ] **Step 4: 运行，确认通过**

Run: `python3 -B -m unittest tests.test_touch_gesture -v`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add upstream/StackChan/firmware/main/collector/touch_gesture.h upstream/StackChan/firmware/main/collector/screen.cpp tests/touch_gesture_test.cpp tests/test_touch_gesture.py
git commit -m "collector: home-page tap, double tap and four swipes"
```

---

### Task C4: `/mouth` 上收发模式消息

**Files:**
- Modify: `fw/mouth_link.cpp:52-73`（`on_message`）、`:79-97`（上行发送、断线清理）、`fw/mouth_link.h`
- Test: C1 的主机测试覆盖解析和队列；本任务只构建

**Interfaces:**
- Consumes: C1。
- Produces:
  - `on_message`：`"mode"` → `parse_snapshot(..., first_after_hello)`；`Applied` 写进全局 `modes::current`（互斥锁保护，结构体用 `heap_caps_calloc(..., MALLOC_CAP_SPIRAM)` 放 PSRAM）并置"屏幕要更新"标志；`Same` 什么都不做；`Bad` 计数。`"mode_result"` → 存最近一条提示给屏幕。握手（收到 `hello`）后把 `first_after_hello` 置真，收到第一条快照后清掉。
  - `bool mode_link_send_input(const char* source, const char* input, int value)`、`mode_link_send_select(const char* mode, const char* dir)`：`/mouth` 已连上且已握手才入队，否则返回 `false`（屏幕显示 `OFFLINE`）；`age_ms` 按入队时间算；ASR 任务、主任务（触摸）都会调；队列在 PSRAM。断线时清空队列（`drop_stream_locked` 里一并清，`fw/mouth_link.cpp:93-97`）。
  - 屏幕应用完快照后调 `mode_link_ack(seq)` → 发 `mode_applied`。
  - 遥测 `mouth.mode = {seq, mode, applied_seq, inputs_sent, inputs_dropped, same_snapshots, bad_snapshots}`。

- [ ] **Step 1: 实现**

- [ ] **Step 2: 主机测试和构建**

Run: `python3 -B -m unittest tests.test_mode_wire -v && ./scripts/idf.sh build`
Expected: OK；构建成功，没有新警告

- [ ] **Step 3: Commit**

```bash
git add upstream/StackChan/firmware/main/collector/mouth_link.cpp upstream/StackChan/firmware/main/collector/mouth_link.h
git commit -m "collector: mode snapshots and inputs over /mouth"
```

---

### Task C5: 屏幕：角标、每模式外观、模式选择页、快捷设置页

**Files:**
- Modify: `fw/screen.cpp`、`fw/screen.h`、`fw/eye_mode.h`
- Create: `fw/mode_look.h`、`tests/mode_look_test.cpp`、`tests/test_mode_look.py`

**Interfaces:**
- Consumes: C1 的 `Snapshot`、C3 的手势、C4 的 `mode_link_send_*`。
- Produces:
  - `mode_look.h`（纯逻辑）：`HomeView home_view(const Snapshot&)` → `{show_laughing_man, eye_style, pool_mask, page}`；**取代 `eye_mode.h:8-12` 的"视觉开关决定首页"**（主人 2026-11-14）：`LaughingMan` 不论视觉都显示笑面男；`V05/V14/V18` 用该款自然循环（`Default` = `kNormalNaturalPool`，困倦、思考、好奇，`eye_mode.h:13-16`；`Lively` = 迎接、开心、好奇、搞怪、惊讶）；`Page*` 显示对应占位页。`home_view_initial(bool vision_on)`：开机到第一条快照之前沿用今天的规则。
  - 角标：右上角，底色 = `color`，字 = `label`（现有 ASCII 15 px 字体，`collector_mono_15.c`）；离线（`/mouth` 没连上）变灰并加 `OFF`；`hosted` 时加小 `A`。关屏时隐藏。放在情绪条以下（y ≥ 4）。
  - 切换闪屏：`mode` 变了，屏幕中间放大显示 `label` 约 1 秒。
  - 模式选择页（上滑）：3×3 九格（ASCII 名字）；当前模式高亮，再点当前格 = 离开（发 `select` 当前模式）；点一格调 `mode_link_send_select`，该格显示 `...` 直到新快照；离线时整页灰、写 `OFFLINE`、点了不发；点空白或下滑关闭。
  - 快捷设置页（下滑）：`VOL -` / `VOL +`（主任务里直接调现有的音量设置函数并存 NVS，和音量口令同一路，`fw/volume_setting.h:6-12`）；`BILLING ON/OFF`（发 `billing` 输入，显示以快照为准）；0–9 键（发 `digit`，5–8 灰）；上滑或点空白关闭。
  - 摘要页加两行：`MODE <label> #<seq>`、`BILLING ON/OFF`。
  - 占位页：字幕页（`SUBTITLE` + 方向）、参数页（音量、麦克风增益、俯仰、计费、模式，只读）、IDLE 页（黑底小字 `hello`）、badge 页（大字 `hello` + 固件版本；IDLE 单击切换）。
  - `mode_result` 的提示在底部功能条上方显示 2 秒。
  - 内存：模式页和快捷设置页**用时才建、关了就删**；`Snapshot` 和提示文字放 PSRAM；不新增任务；不新增字库。

- [ ] **Step 1: 写失败的测试** `tests/mode_look_test.cpp`

```cpp
int main() {
    using namespace collector::modes;
    Snapshot talk{}; talk.eye = Eye::LaughingMan;
    assert(home_view(talk).show_laughing_man);                    // talk show: laughing man even with vision on
    Snapshot crowd{}; crowd.eye = Eye::V14; crowd.pool = Pool::Lively;
    auto v = home_view(crowd);
    assert(!v.show_laughing_man && v.eye_style == eyes::Style::V14 && (v.pool_mask & eye_bit(eyes::State::Greeting)));
    Snapshot chat{}; chat.eye = Eye::V05;
    assert(home_view(chat).eye_style == eyes::Style::V05 && home_view(chat).pool_mask == kNormalNaturalPool);
    Snapshot ev{}; ev.eye = Eye::PageIdle; std::strcpy(ev.label, "AUTO");   // event-driven, nothing entered yet: IDLE screen
    assert(home_view(ev).page == HomePage::Idle);
    Snapshot idle{}; idle.eye = Eye::PageIdle;
    assert(home_view(idle).page == HomePage::Idle);
    assert(home_view_initial(false).show_laughing_man && !home_view_initial(true).show_laughing_man);  // before any snapshot
    return 0;
}
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mode_look -v`
Expected: FAIL，头文件不存在

- [ ] **Step 3: 实现 `mode_look.h`；改 `screen.cpp`**

- [ ] **Step 4: 主机测试、全部 Python、构建；记下大小**

Run: `python3 -B -m unittest discover -s tests -v && ./scripts/idf.sh build && ls -l upstream/StackChan/firmware/build/stack-chan.bin`
Expected: 全部 OK；构建成功；镜像小于应用槽 5,177,344 B（`firmware/partitions.csv`，第一版 2.4），记下比 0.4.91 大了多少

- [ ] **Step 5: Commit**

```bash
git add upstream/StackChan/firmware/main/collector/ tests/mode_look_test.cpp tests/test_mode_look.py
git commit -m "collector: mode badge, per-mode looks, mode picker and quick settings pages"
```

---

### Task C6: 版本、协议文档、构建产物、刷机脚本，然后停下问主人

**Files:**
- Modify: `upstream/StackChan/firmware/CMakeLists.txt:5`（`collector-0.4.92`）、`docs/collector-protocol.md`（新一节"0.4.92：模式"：口令新含义、首页手势、`/mouth` 新消息、遥测 `mouth.mode`；并更正 `:165` 那句"open/close 仍开关追踪……start/stop、next 都不变"）
- Create（本机，不进仓库）：`artifacts/collector-0.4.92-modes/`（`stack-chan.bin`、ELF、`source.patch`、`SHA256SUMS`）、`.private/modes-step1-v0492/preflight.py`、`deploy.py`、`rollback.py`

- [ ] **Step 1: 构建并存产物**

Run: `./scripts/idf.sh build`
Expected: 成功；把 `build/stack-chan.bin`、ELF 复制到 `artifacts/collector-0.4.92-modes/`，记 SHA256

- [ ] **Step 2: 写刷机脚本（照 `.private/wifi-factory-v0491/preflight.py`、`deploy.py` 的步骤，那两个文件只读）**
  - `preflight.py`：身份（`esptool` 读到的 MAC 和 `.private/stackchan-monitor.json` 的 `device_id` 一致；ESP32-S3、16 MB）；分区表读出并校验；otadata 有效项算出活动槽 = `ota_1`、`0x510000`、`0x4f0000`；当前应用头 4 KB 含 `collector-0.4.91`，并和 `artifacts/collector-0.4.91-wifi-factory/stack-chan.bin` 一致；整槽备份 `backups/ota_1-collector-0.4.91-before-modes-<日期>.bin`，对设备 `verify_flash` 通过。本次不改 NVS 结构，不备份 NVS（实现中如果新增了 NVS 键，就照 0.4.91 一并备份 NVS）。
  - `deploy.py`：只写 `0x510000`；写后独立 `verify_flash`。
  - `rollback.py`：同样的身份和活动槽核对，把上面的整槽备份写回 `0x510000`，独立 `verify_flash`，核对应用头含 `collector-0.4.91`。

- [ ] **Step 3: 停下。** 把产物大小、SHA256、主机测试结果、内存预估、三个脚本的路径报给主人，**等主人当次同意再刷**。刷机在 D2 里做，和 D1 同一次。

- [ ] **Step 4: Commit**（只提交源码和文档，不提交产物和 `.private/`）

```bash
git add upstream/StackChan/firmware/CMakeLists.txt docs/collector-protocol.md
git commit -m "collector-0.4.92: modes step 1"
```

---

## D. 部署与验收（每一步都要主人同意；挑没人用的时候）

### Task D0: 单独上线 A0（ear 终结 `/mouth`），在 0.4.91 上验证

- [ ] **Step 1: 测试**

Run: `.private/venv-ear/bin/python -B -m unittest discover -s tests -v`
Expected: 全部 OK

- [ ] **Step 2: 在服务器上留旧文件**（用 `.env` 里的登录和目录变量，和 `server/ear/deploy.sh` 一样；不要把地址写进命令历史以外的地方）：

```bash
. server/ear/_env.sh                       # same variables server/ear/deploy.sh:8-9 needs
STAMP=$(date +%Y%m%d-%H%M)
ssh "$SOUL_SERVER_SSH" "mkdir -p ~/ear-backup-$STAMP && cp $SOUL_SERVER_DIR/*.py ~/ear-backup-$STAMP/ && ls ~/ear-backup-$STAMP"
```

回滚命令（把 `STAMP` 写进交接，出问题时照抄）：`ssh "$SOUL_SERVER_SSH" "cp ~/ear-backup-$STAMP/*.py $SOUL_SERVER_DIR/ && sudo systemctl restart $SOUL_SERVER_UNIT"`（重启方式照 `server/ear/deploy.sh` 里的那一行；如果它不用 `sudo systemctl`，照它的写法）。

- [ ] **Step 3: 部署**：`server/ear/deploy.sh --dry-run` → `server/ear/deploy.sh`。

- [ ] **Step 4: 在 0.4.91 上核"行为不变"**：
  - `/mouth` 重新连上（ear 日志 `+ … mouth`）；页面点一次"说一轮"，hello 照常出声、`tts.done` 回来；
  - 让 hello 说一段较长的话，说到一半停 soul-tts：机器人在 1 秒内停声（收到 `tts.stop`），`/mouth` 不断；听觉在 hello 停声后照常收到声音（ear 不再一直喂静音）；
  - 30 秒后开 soul-tts，再说一轮能出声；
  - 拔掉机器人的网络（或让它重启）：ear 日志里上游跟着关了；soul-tts 的 `/health` 里没有设备；
  - 听觉断了一次后自己重连。
  任何一条不成立：按 Step 2 的命令回滚，再查。通过后才开始 A5。

### Task D1: 模式版本的服务器上线（和 D2 刷机同一次，紧挨着做）

- [ ] **Step 1: 全部测试**

Run: `python3 -B -m unittest discover -s tests -v && .private/venv-ear/bin/python -B -m unittest discover -s tests -v && node --test web/monitor/*.test.mjs`
Expected: 全部 OK / pass

- [ ] **Step 2: 留旧文件**：ear 照 D0 Step 2；mind 同样把服务器上 mind 目录的 `*.py` 复制到 `~/mind-backup-<时间>/`。回滚命令照 D0 的格式写进交接。

- [ ] **Step 3: 部署 mind**：`server/mind/deploy.sh --dry-run`，确认列出 `modes.py`、`mode_store.py`、`billing.py`、`mode_table.py`；然后 `server/mind/deploy.sh`。mind、eye、纪要、记忆四个单元重启后在跑；`GET /mode` 返回 `talk`；`GET /switches` 有 `billing.on = true`。

- [ ] **Step 4: 部署 ear**：`server/ear/deploy.sh --dry-run` → `server/ear/deploy.sh`。机器人还是 0.4.91：TTS 照常（0.4.91 忽略 `mode` 消息）。

- [ ] **Step 5: 重启本机观察台**，首页和情景页都有模式条。

- [ ] **Step 6: 马上进 D2。** 如果 D2 当次刷不了：把 mind 和 ear 都按备份回滚到 D0 的状态（否则 DualKey 一推就离开脱口秀、页面"说一轮"变 409，而 0.4.91 屏幕上什么都看不到）。

### Task D2: 刷 0.4.92、实机验收、需要时回滚

- [ ] **Step 1（主人同意后）：刷机**：`preflight.py` → `deploy.py`；USB 日志看到启动完成、`/mouth` 连上、收到 `mode` 快照；`GET /v1/telemetry` 的 `mouth.mode.seq` 和 mind 的 `GET /mode` 一致；内部 RAM 剩余、最大连续块、DMA 剩余和 0.4.91 比，记进交接。

- [ ] **Step 2: DualKey 在真服务器上推一遍**（交接里的待办）：上、下、左、右、单击、双击、长按 1 秒各一次；页面模式条、信号列表、机器人角标都对得上；摇杆灯随模式变色，IDLE 灭。

- [ ] **Step 3: 能试**：
  - 开机后角标 `TALK`、笑面男（开着视觉也是）。说 "hello" 唤醒后：forward → 说一轮（已在脱口秀）；backward → IDLE 屏；forward → 回 `TALK`、不说话；再 forward → 说一轮；play → 猫眼、`CHAT`；直接 open → 参数页（不经 IDLE）；close → 回 `CHAT`；pause → IDLE；start → IDLE 屏、角标 `AUTO`；在事件驱动里说 play → 猫眼、`CHAT`，事件驱动已停（之后 pause → IDLE，不回 `AUTO`）；再 start、stop → IDLE。DualKey 单击在 hello 说话时停声（和 ASR 改绑无关）。
  - next / previous 在脱口秀里开关提前写（页面台词面板跟着变）；在 IDLE 里说 next，屏幕提示 `ONLY IN TALK`。
  - 首页单击（脱口秀）说一轮；hello 正在说时单击 = 停；双击开提前写；IDLE 里单击切 badge。
  - 上滑出模式页，点 `MEET` → 机甲眼热闹表情；再点 `MEET` → IDLE。下滑出快捷设置页，音量 − / ＋ 立即生效；计费关 → 角标旁、摘要页、页面都显示计费关，台词按钮灰；说 "nine" → 总闸开、听悟、识别、情景、纪要开（在脱口秀里时提前写、连续说也开），屏幕提示 `SENSES: USE PAGE`；页面按 9 → 本体听觉和视觉上传也开；说 "zero" → 全关。
  - 左滑出状态摘要，有模式和计费两行。
  - 页面点模式按钮，机器人 2 秒内换角标，页面写"机器人已显示"。
  - hello 断电重启后回到脱口秀，付费开关没有被动过。

- [ ] **Step 4: 算失败**（任何一条成立就进 Step 5 回滚或修）：
  - 页面、机器人、DualKey 灯显示的模式或计费不一致超过 3 秒（网络正常时）。
  - 离开脱口秀后提前写或连续说还开着，或 `mind_runs` 里还在出现 `say` 调用。
  - 计费关着时出现任何新的付费调用（`mind_runs`、听悟在跑），或付费开关能被打开。
  - 进入任何模式后，`mind_runs` 在没人触发的情况下出现新的调用。
  - 首页单击被当成双击或滑动的次数，在 20 次里超过 1 次。
  - soul-tts 停止期间机器人的模式换不了。
  - 0.4.92 的内部 RAM 最大连续块比 0.4.91 少超过 2 KB，或听觉断线比改之前多（对照交接里的近期数）。
  - 开机后机器人不在脱口秀。

- [ ] **Step 5: 回滚（需要时）**：固件用 `rollback.py` 写回 0.4.91 备份并独立 verify；**固件和服务器成对回滚**：0.4.92 配旧 ear 时所有模式词无效，而 start/stop、open/close、next 的旧用途也已去掉，所以回滚 ear 时一定同时回滚固件；回滚固件后服务器退回 D0 的状态（D1 Step 6 的做法）。

- [ ] **Step 6: 把结果写进 `docs/session-handoff.md` 顶部**（含 D0、D1 的回滚命令和备份目录名）。

---

## 不在本计划内（移到后面的）

- **下行控制**（第二步）：`/mouth` 上的 `control` 消息、机器人控制 worker 改成能排队（今天一次一条，`fw/serial_monitor.cpp:130-132`）；有了它，机器人发的预设 1 / 9 才能执行机器人那一半，"听觉跟着模式"也靠它。
- **计费加固**（第二步）：`llm.chat` 一处的进程级闸门（mind、eye、纪要、记忆四个进程）；`TtsHttp.say`（`server/mind/speak.py:577`）也查；各后台循环开头先查总闸、`run_json` 在查预算处一并查，免得被拒的调用记成模型出错并触发退避（`model_calls.py:42`、`eye_runner.py:47-56`、`memory_worker.py`）；总闸读取"缓存过期先重读、重读失败才算关"；ear 的听悟再加一道"总闸也开"。
- **中文小字库**（和字幕一起，完整常用字库）：角标改成一个中文字，缺字退回 `label`。
- 进入模式自动打开它要的付费感官、听觉跟着模式（主人 2026-11-14：先手动）。
- 对话、字幕、围观、陪伴、设置的内容；事件驱动的事件规则和客人模式。
- ASR "hello" 打断；换 ASR 词表；中文语音口令进模式。
- IDLE 的音乐和视频。
- 官方遥控器第二层绑定模式；遥控器情绪层（另有计划）。
- 每个模式的每日花费上限（第一版 3.7）。
