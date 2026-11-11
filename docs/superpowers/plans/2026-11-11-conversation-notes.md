# 对话纪要第一步：切段、边听边写、当天接续、交给 mind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新单元 `soul-notes` 在每分钟结束后 10 秒，把还没处理过的"别人的话"（SenseVoice 和听悟都算，hello 自己的台词去掉）按规则切成对话（没人说话满 60 秒，对话就结束）。正在进行的对话，用 luna 边听边写约 10 字的标题和约 50 字的纪要。新对话开始时，同一次调用里顺便判断它是不是接着今天前面某一段说的。mind 把当前对话的纪要当作 `c1` 来读，情景页显示"今天的对话"。

**Architecture:** 代码放在 `server/mind/` 包里：`notes.py` 负责一次更新，`notes_runner.py` 负责循环；新建运行单元 `soul-notes`，和 eye 一样由 `remote_setup.sh` 建立。数据放在 `hello.conversations`（每段对话一条，带最新版本）和 `hello.conversation_versions`（每次纪要更新的历史）。开关是 `hello.switches.notes`。

**Tech Stack:** Python 3.11 + pymongo；luna 走 aihubmix 的 `/chat/completions`，用严格 `json_schema`（luna 一直守格式），`reasoning_effort: low`，`max_tokens` 4000。

**Spec:** `docs/superpowers/specs/2026-11-11-conversation-notes-design.md`（第 2、3.1–3.3、3.5、3.6、4、5 节中的"第一步"）。

## Global Constraints

- 切段：一段对话里两句之间相隔不超过 60 秒；最后一句之后满 60 秒没有新话，这段就结束（`end_ms` 记为最后一句的时间）。
- 句子来源：`hello.speech`（SenseVoice）和 `hello.tingwu`（kind 为 sentence）。时间用 `Records.window` 的 `_wall`。`selfvoice.is_hello(text, 是否和 hello 出声重叠)` 为真的不要。已经处理过的句子用 `(集合, session, id 或 n)` 记录，不重复处理。
- 什么时候运行：每分钟结束后 10 秒，只要开关开着、并且有新句子，或者有正在进行、需要结束的对话。没有新句子就不调用模型。
- 一次调用的输入：
  - **新对话**：候选旧对话（今天结束的；上一段如果结束不到 10 分钟一定在内；再按字面二元组重合度选前 4 段），附上它们的时间、标题、纪要；加上新句子（标明说话人和时间）。
  - **进行中的对话**：当前的标题和纪要、这次的新句子、这段对话最近 2 分钟的原话（最多 20 句）。
- 输出（严格 JSON）：`{"continues": "<旧对话编号>" | "", "title": "", "notes": "", "confidence": "high|mid|low"}`。约 10 字的标题、约 50 字的纪要，归纳不出主题时都留空，不要硬凑。
- 续写：如果判断是接续，新对话记下 `continues`，纪要以那段的纪要为起点往下写。
- 失败重试 1 次；再失败就按 1、2、5、10 分钟退避，这批句子保留到下次再处理。和 mind、eye 共用每天的预算上限。运行记录写进 `mind_runs`，`step: "notes"`。
- 状态文档 `switches.notes_status {running, available, at}`，每 30 秒写一次。开关默认关。
- mind：分钟包的 `conv` 取"开始时间不晚于这一分钟结束、还在进行或结束不到 5 分钟"的对话中最近的一段，代号 `c1`，`BUILDER_VERSION = 5`。v3 提示词加入【对话】这一行，并规定话题条目要引用 `c1`。content_fp 加上 `(对话编号, 版本)`。
- 部署要等用户明确说"部署"。

## Review Focus

1. **两个人隔 90 秒各说一句**：期望切成两段，第二段开始时调用一次（这一次同时判断接续）。测试在 Task 1。
2. **迟到的句子**（下一分钟才写进库）：期望下一次更新时补进同一段，不重复。测试在 Task 1。
3. **只有 hello 自己的台词**：期望不开对话，也不调用。测试在 Task 1。
4. **模型说接续了一个根本不在候选里的编号**：期望当作不是接续，`continues` 留空。测试在 Task 2。
5. **开关在两次更新之间关掉**：期望不再调用；对话照常按规则结束，结束不花钱。测试在 Task 3。

---

### Task 1: 切段与句子收集（`notes.py` 的规则部分，不调用模型）

**Files:** Create `server/mind/notes.py`；Test `tests/test_mind_notes.py`

**Interfaces（Produces）：**
- `new_sentences(db, records, since_ms, until_ms, seen: set) -> list[dict]`：返回 `{"ref": (coll, session, key), "at": wall_ms, "text", "who"}`，按时间排序。去掉 hello 自己的话，也去掉 `seen` 里已有的。
- `segment(open_conv: dict | None, sentences, now_ms) -> (to_close: dict | None, groups: list[list[sentence]])`：按 60 秒的间隔切。第一组接在 `open_conv` 后面（如果间隔不超过 60 秒）。另外，最后一句之后满 60 秒没有新话时，返回需要结束的对话。
- 常量 `GAP_MS = 60_000`、`CONTINUE_WINDOW_MS = 10 * 60_000`、`CANDIDATES = 4`。

- [ ] Step 1 写失败的测试：
  - `test_sentences_exclude_hello_and_seen`
  - `test_late_sentence_joins_the_open_conversation`
  - `test_90_s_gap_starts_a_new_conversation`
  - `test_conversation_closes_60_s_after_its_last_sentence`
  - `test_only_hello_clips_open_nothing`
- [ ] Step 2 运行，确认失败 → Step 3 实现 → Step 4 运行，确认通过 → Step 5 提交 `feat(mind): conversation segmenting for notes (rules, no model)`

### Task 2: 候选与一次更新（调用模型）

**Files:** Modify `server/mind/notes.py`；Modify `server/mind/llm.py`（不用改：luna 已经有价格）；Test `tests/test_mind_notes.py`

**Interfaces（Produces）：**
- `candidates(db, day_start_ms, before_ms, text) -> list[conv]`：上一段如果结束不到 10 分钟一定在内，再加上按字面二元组重合度排的前 4 段。
- `NOTES_SCHEMA`、`NOTES_SYSTEM`（提示词：约 10 字标题、约 50 字纪要、写谁在谈、谈什么、要点和结论、不要硬凑、两种转写只取一致的部分、接续怎么判断）。
- `update(env, store, s, conv | None, sentences, now_ms) -> conv`：新对话（`conv is None`）时带上候选；进行中的对话带上当前纪要和最近原话。写 `conversations` 和 `conversation_versions`，在 `mind_runs` 记一条 `step: "notes"`。失败时退避，句子不算处理过。
- [ ] Step 1 写失败的测试：
  - `test_first_update_offers_candidates_and_links_a_continuation`
  - `test_unknown_continues_id_is_ignored`
  - `test_running_update_sends_current_notes_new_and_recent_sentences`
  - `test_empty_notes_allowed`
  - `test_failure_backs_off_and_keeps_sentences_unseen`
  - `test_budget_reached_no_call`
- [ ] Step 2–4，然后 Step 5 提交 `feat(mind): notes — luna writes title and ~50-character notes, links same-day continuations`

### Task 3: `notes_runner.py` 与 `soul-notes` 单元

**Files:** Create `server/mind/notes_runner.py`；Modify `server/mind/remote_setup.sh`（第三个单元）；Test `tests/test_mind_notes.py`

**Interfaces（Produces）：**
- `tick(db, store, records, s, env, now_ms) -> int`（这次调用了几次）。到"分钟结束后 10 秒"才处理；先结束到期的对话，再把每组新句子交给 `update`。游标和 `seen` 存在 `notes_state`。
- 开关关着时：游标照样往前走（不回补），到期的对话照样结束，不调用模型。
- `main()`：参数和 eye_runner 一样，另加 `--clip-texts`。
- [ ] Step 1 写失败的测试：
  - `test_tick_waits_10_s_after_the_minute`
  - `test_switch_off_no_calls_but_conversations_still_close`
  - `test_one_call_per_tick_for_a_running_conversation`
- [ ] Step 2–4（另外 `bash -n remote_setup.sh` 通过），Step 5 提交 `feat(mind): soul-notes unit`

### Task 4: mind 读取 `c1`，接口与开关

**Files:** Modify `server/mind/records.py`（window 里加上 `conversations`）、`package.py`（`conv`，builder 5）、`fusion.py`（content_fp）、`prompts.py`（【对话】这一行和规则）、`validate.py`（`c` 代号算作非语言证据）、`api.py`（SWITCHES 加 `notes`；`GET /notes?from&to`）；`web/monitor/mind.mjs`、`server.mjs`（放行 `/notes`）

- [ ] Step 1 写失败的测试：
  - `test_package_carries_the_current_conversation_as_c1`
  - `test_closed_more_than_5_minutes_ago_is_not_c1`
  - `test_prompt_shows_c1_and_asks_topic_to_cite_it`
  - `test_notes_switch_and_list_api`
  - Node 端：代理放行 `/notes`
- [ ] Step 2–4，Step 5 提交 `feat(mind): mind reads the current conversation notes as c1; notes switch and list API`

### Task 5: 页面

**Files:** Modify `web/monitor/public/switches-ui.js`（新增"纪要"开关，花费那一行算上它；状态过期时显示"纪要状态未知"）、`mind-ui.js`、`mind.html`（"今天的对话"列表；此刻卡片里显示当前对话的标题和纪要）

- [ ] Step 1 写失败的测试：
  - `notes switch toggles and counts in the cost line`
  - `stale notes status says unknown`
  - `conversation list html`（接续的那几段连在一起显示，不写 `style=`）
  - `now card shows c1`
- [ ] Step 2–4，Step 5 提交 `feat(monitor): notes switch, today's conversations, 此刻 shows the current notes`

### Task 6: 部署与现场验证（要等用户说"部署"）

- [ ] 部署 mind 包（这一步会新建 `soul-notes`），重启观察台。
- [ ] 打开"纪要"开关，观察 20 分钟。记录：切出了几段、各段的标题和纪要、接续判断对不对、每次调用多久、花多少钱、"此刻"里的话题有没有比"在讨论工作"具体。
- [ ] 更新交接记录。
