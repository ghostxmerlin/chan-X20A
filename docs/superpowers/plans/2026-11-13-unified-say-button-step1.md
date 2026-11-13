# 统一按钮 第一步：说一轮、提前写、能打分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 页面上一个"说一轮"按钮：五个方向各写一句、评委排序、最高分那句用该方向的嗓子说出来；"提前写"开着时开口只需 1.2–2.5 秒；每句可重播、换嗓子、打"好/坏"，"这一轮都不行"，全部记进 `owner_signals`。

**Architecture:** 全部在 `server/mind/`（Python，mind 进程内）和 `web/monitor/`（Node 代理 + 浏览器页）。新模块各管一件事：提示词、时刻判断、一轮的生成与选句、提前写后台线程、主人信号与统计；`speak.py` 只加一个入口把它们串起来并交给 soul-tts。模型调用沿用 `model_calls.run_json`，但 HTTP 请求移出全局锁以便并行。

**Tech Stack:** Python 3.11 标准库 + pymongo（服务器 venv）；Node 24（观察台）；aihubmix 上的 `gpt-6.1-terra`（reasoning_effort none）；soul-tts。

**Spec:** `docs/superpowers/specs/2026-11-13-unified-say-button-design.md`（第 6、6A、7、8、9、10、11、12、13、14、15 节第一步）。原型：`.private/say-round-spike/gen.py`（第三版提示词）、`index.html`（页面布局）、`bench.py`（并行写法）。

## Global Constraints

- 模型：写句子、兜底句、评委、核对都用 `gpt-6.1-terra`，`reasoning_effort: "none"`；写句子温度 0.65，评委与核对温度 0。
- 五个方向的 id 与现有角色一致：`comfort`(鼓励) `deadpan`(呆呆) `roast`(吐槽) `sad`(伤心) `worst`(最坏)；兜底句 id `huyu`。
- 嗓子和方言由服务端定：方向 i 用 `speech_settings.role_config(roles[i], s)` 的 voice / instruction / rate；`huyu` 用 `comfort` 的 voice、方言固定上海话。模型不配嗓子。
- 句子检查沿用 `scene_speech.generate` 的规则：长度 2..`speech.max_chars`，不含 `\r\n[]【】（）()“”「」<>`，与最近说过的话相似度 < 0.78。单句不合格只丢该句；合格少于 2 句才算本次失败，不自动重试。
- 提前写：next 开 / previous 关（第一步只有页面开关，ASR 接入见末尾"不在本计划内"）；一段话说完 5 s 无新的清楚原话才写；两轮至少隔 30 s；同一时刻不重写；每小时花费上限 0.60 美元；重启、关闭、场景切换、`speech.profile` 改变时存货作废。
- 冷场规则：同一时刻第一次触发说兜底句；之后在这一时刻再触发不出声，页面显示"这一刻先不说了"，触发照样记录。
- 同一时刻：间隔 ≤ 90 s，且两次各自"前 60 s 内听到的句子"（不论清不清楚）规范化后交集 ≥ 较小一方的一半；两边都没有句子时比较情景分钟是否相同。
- 信号：页面"好/坏"连点在页面侧合并（最后一下后 600 ms 收尾，最多 5 下）为一条 `owner_signals`；`kind` ∈ `rating` / `round_none`；同句以最后一条为准，旧记录不删。
- 效果数：好评率 = 好的强度之和 /（好 + 坏的强度之和）；评委猜中率 = 时刻第一轮的评委第一名被打了"好"的比例；分"调试（page）/日常（其他设备）"两栏；均按时刻折叠；按 `scope_id` 分开。
- 公开仓库：测试和文档里不放现场原话、人名、服务器地址。
- 没人触发、提前写关着时，不产生任何模型调用。

## Review Focus

- 提前写线程与点击同时写同一时刻 → 只能有一轮生效，点击用已有存货或等待，不得出现两轮重复写同一时刻（Task 6 测试 `test_click_during_prefetch_reuses_one_round`）。
- soul-tts 未连或嘴不在 TTS 模式时点击 → 立刻返回 409，不调模型（Task 7 测试 `test_round_refused_without_mouth_costs_nothing`）。
- 五个并行调用里有的超时/返回坏 JSON → 用剩下的合格句继续；全坏才失败（Task 4 测试 `test_round_survives_two_failed_directions`）。
- 时钟跨小时时的预算窗口 → 新的一小时恢复提前写（Task 6 测试 `test_budget_window_resets_next_hour`）。
- 用户在一轮还在写时点"别说了"或改设置 → 这轮作废、不出声（Task 7 测试 `test_cancel_while_writing_never_speaks`）。

---

### Task 1: 模型调用可以并行

**Files:**
- Modify: `server/mind/model_calls.py`（`run_json`）
- Test: `tests/test_mind_model_calls.py`（新）

**Interfaces:**
- Produces: `run_json(...)` 签名不变；预算检查和 `store.put_run` 仍在 `_LOCK` 里，`chat(...)` 调用移到锁外。

- [ ] **Step 1: Write the failing test** `test_parallel_calls_overlap`：一个假 chat 每次 `time.sleep(0.3)` 后返回合法 JSON；用 `ThreadPoolExecutor(5)` 同时跑 5 次 `run_json`；断言总耗时 < 0.9 s，且 `mind_runs` 里有 5 条。另写 `test_budget_checked_before_each_call`：预算已满时 `run_json` 抛 `BudgetExceeded` 且假 chat 未被调用。
- [ ] **Step 2: Run** `cd tests && python3 -B -m unittest test_mind_model_calls -v` → 第一项 FAIL（约 1.5 s）。
- [ ] **Step 3: Implement**：锁内做预算检查；锁外 `chat(...)`；锁内解析与 `put_run`。
- [ ] **Step 4: Run** 同上 → PASS；再跑 `python3 -B -m unittest discover -s tests -p 'test_mind*'` → 全部 PASS。
- [ ] **Step 5: Commit** `git commit -m "fix(mind): model calls run outside the global lock so a round can write in parallel"`

### Task 2: 提示词与结构（一句、兜底、评委、核对）

**Files:**
- Create: `server/mind/say_prompts.py`
- Test: `tests/test_mind_say_prompts.py`

**Interfaces:**
- Produces:
  - `DIRECTIONS: dict[str, str]`（五个方向的说明，取自 `.private/say-round-spike/gen.py` 第三版 `DIRECTIONS`，`worst` 用"直面现实"那版）
  - `line_messages(direction: str, role: dict, ctx: dict, now_ms: int, history: dict[str, list[dict]]) -> list[dict]`；`LINE_SCHEMA`（`point`,`device`,`text`）
  - `huyu_messages(ctx, now_ms, history) -> list[dict]`；`HUYU_SCHEMA`（`text`,`gloss`）
  - `judge_messages(ctx, now_ms, lines: list[dict]) -> list[dict]`（lines 已打乱，每项含 `text`,`voice_label`,`dialect`）；`JUDGE_SCHEMA`（`scores: list[int]`,`best: int`,`weak: bool`）
  - `check_messages(ctx_now, now_ms, ranked: list[dict]) -> list[dict]`；`CHECK_SCHEMA`（`pick: int`，0 表示都不行）
- 情绪：spec 第 7、9 节要把 hello 当下的情绪交给写手和评委；情绪接口（`emotion.current()`）还不存在，本步按"平静"处理，不写进提示词。
- 提示词正文取自 `gen.py` 第三版 `WRITE`/`JUDGE`，改成"本次只写「方向」这一句"；`history` 是每个方向最近 4 句（含没播出的），`overused` 按方向用 `scene_speech.overused`。兜底句要求：地道上海话、沪语用字、不要求普通话听众听懂、不编事实，`gloss` 为普通话意思。核对：给出排好序的句子和"这轮写完之后新听到的话"，选仍然接得上的一句或 0。

- [ ] **Step 1: Write the failing tests**：`test_line_prompt_names_only_its_direction`（system 含该方向说明与"只写"、不含其他四个方向的说明）；`test_line_prompt_carries_direction_history`（user JSON 的 `该方向最近写过` 等于传入 history 的最后 4 句）；`test_judge_listing_shuffled_with_voice_and_dialect`；`test_check_prompt_lists_new_speech_only_after_round`（只含 `at` > round.written_at 的句子）；四个 schema 都是 `additionalProperties: False` 且 required 完整。
- [ ] **Step 2: Run** `python3 -B -m unittest test_mind_say_prompts -v` → FAIL。
- [ ] **Step 3: Implement** `say_prompts.py`。
- [ ] **Step 4: Run** → PASS。
- [ ] **Step 5: Commit** `feat(mind): say-round prompts — one line per direction, Shanghainese fallback, judge, check`

### Task 3: 同一时刻

**Files:**
- Create: `server/mind/say_moment.py`
- Test: `tests/test_mind_say_moment.py`
- Create (private, not committed): `.private/say-round-spike/moment_accept.py`

**Interfaces:**
- Produces:
  - `signature(ctx: dict, now_ms: int) -> dict`：`{"at": now_ms, "texts": sorted set of speech_context.norm(text) for speech evidence with at >= now_ms-60000, "scene_minute": ctx.scene.minute or None}`
  - `same_moment(prev: dict | None, cur: dict) -> bool`（规则见 Global Constraints）

- [ ] **Step 1: Write the failing tests**：90 s 内一半相同 → True；91 s → False；交集不足一半 → False；两边都无句子且情景分钟相同 → True、不同 → False；`prev is None` → False。
- [ ] **Step 2: Run** → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** → PASS。再写私有验收脚本 `moment_accept.py`：在服务器上读 11-12 的 28 次点击上下文，按时间顺序套 `same_moment`，打印分组，期望等于 spec 6 节的 10 组（{0,1,2}{3,4}{5}{6}{7}{8}{9–13}{14–18}{19}{20}，编号为试看页 21 个场景）。对不上就调规则，结果贴到交接记录。
- [ ] **Step 5: Commit** `feat(mind): same-moment rule for talk-show rounds`

### Task 4: 写一轮、选句、核对、存 `say_rounds`

**Files:**
- Create: `server/mind/say_round.py`
- Modify: `server/mind/store.py`（`ensure_indexes`：`say_rounds` 的 `rid` 唯一、`(scope_id, at)`；`owner_signals` 的 `sid` 唯一、`at`、`refers_to.rid`；`conversations` 的 `start_ms`）
- Test: `tests/test_mind_say_round.py`

**Interfaces:**
- Consumes: Task 1 `run_json`；Task 2 全部；Task 3 `signature`。
- Produces:
  - `write_round(env, store, s, roles, ctx, now_ms, *, chat=None, trigger=None, moment_id=None) -> dict`：并行 5 个方向 + 兜底句（6 个调用，`ThreadPoolExecutor(6)`，每个 `timeout=12`），合格句打乱后一次评委调用；写入并返回 `say_rounds` 文档（字段见 spec 第 11 节；`lines[i]` 另含 `voice/dialect/instruction/rate`，`written_at`，`signature`）。评委失败时用随机打乱前的第一个合格句并记 `judge.error`。
  - `ranked_keys(round: dict) -> list[str]`：按评委分数降序，`huyu` 不参加。
  - `is_thin(round: dict, ctx: dict, now_ms: int) -> bool`：`no_clear_speech`（60 s 内无 `quality=="usable"` 的原话）或 `round.judge.weak`；两项分别受 `speech.round.thin_no_speech` / `thin_judge_weak` 开关。
  - `check_round(env, store, s, round, ctx_now, now_ms, *, chat=None) -> str | None`：返回仍可用的 line key 或 None；写入 `round.checks` 追加 `{at, pick, run_id, ms}`。

- [ ] **Step 1: Write the failing tests**（假 chat 按 `body["messages"][0]["content"]` 判断是哪类调用并返回对应 JSON；需线程安全）：
  - `test_round_writes_five_plus_huyu_and_ranks`：6 次写调用 + 1 次评委；`ranked_keys` 顺序与评委分数一致；文档进 `say_rounds`。
  - `test_round_survives_two_failed_directions`：两个方向返回坏 JSON → 其余 3 句 + 兜底照常，`lines` 中两项 `dropped: "invalid"`。
  - `test_round_fails_with_fewer_than_two_lines`：抛 `RuntimeError`，不写评委调用。
  - `test_casting_from_role_config`：`roast` 的 voice/instruction 等于 `role_config(roles["roast"], s)`；`huyu` 的 instruction 以"请讲上海话。"开头。
  - `test_check_returns_none_when_model_says_zero`。
  - `test_is_thin_flags`。
- [ ] **Step 2: Run** `python3 -B -m unittest test_mind_say_round -v` → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** → PASS。
- [ ] **Step 5: Commit** `feat(mind): write a talk-show round in parallel, judge it, check it against new speech`

### Task 5: 主人信号与效果数

**Files:**
- Create: `server/mind/owner_signals.py`
- Test: `tests/test_mind_owner_signals.py`

**Interfaces:**
- Produces:
  - `put_signal(db, body: dict, now_ms: int) -> dict`：校验 `kind` ∈ `rating`/`round_none`；`rating` 要 `value.polarity ∈ {1,-1}`、`value.count ∈ 1..5`、`refers_to = {rid, line}` 或 `{uid}`；`round_none` 要 `refers_to = {rid}`；`source.device` 缺省 `"page"`；补 `sid, at, who:"owner", state:"applied"`；只插入。非法抛 `ValueError`。
  - `fold_moments(db, scope_id: str, from_ms: int, to_ms: int) -> list[dict]`：每个 `moment_id` 一项 `{moment_id, first_rid, judge_first, ratings: {line: (polarity*count, device)}, none: bool}`，同句取最后一条；`{uid}` 形式经 `utterances.rid/line` 换算。
  - `stats(db, scope_id, from_ms, to_ms) -> dict`：`{"page": {...}, "daily": {...}}`，每栏 `good_rate`、`judge_hit_rate`、`rated_lines`、`unrated_lines`、`none_rate`、`huyu_rate`、`moments`。

- [ ] **Step 1: Write the failing tests**：`test_rating_validation`；`test_last_rating_wins_per_line`；`test_good_rate_weighted_by_strength`（+3、−2 → 0.6）；`test_judge_hit_uses_first_round_of_moment`（同一时刻两轮，第二轮第一名被点好不算）；`test_columns_split_by_source`（page 与 `dualkey-x` 分栏）；`test_scopes_kept_apart`。
- [ ] **Step 2: Run** → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** → PASS。
- [ ] **Step 5: Commit** `feat(mind): owner_signals — page ratings, moment folding, two-column stats`

### Task 6: 提前写后台线程

**Files:**
- Create: `server/mind/say_prefetch.py`
- Modify: `server/mind/runner.py`（API 线程旁再起一个 `say_prefetch.Worker(...).run` 守护线程）
- Test: `tests/test_mind_say_prefetch.py`

**Interfaces:**
- Consumes: Task 3、Task 4。
- Produces:
  - `class Worker(store, settings_store, roles_dir, env, *, chat=None, clock=time.time)`：`set_enabled(on: bool, now_ms) -> None`（开关存 `mind_state.say_prefetch = {on, since}`；关闭时作废存货）、`step(now_ms) -> str`（单步，返回动作名便于测试：`off`/`waiting`/`wrote`/`same_moment`/`spacing`/`budget`/`no_speech`）、`run()`（每 1 s 调一次 `step`）、`stock(scope_id) -> dict | None`（最新未作废的一轮）、`claim(scope_id, now_ms) -> tuple[dict | None, str]`（给点击用：返回存货与锁，同一时刻同时只能有一方在写，见 Review Focus 1）。
  - 触发：开着、嘴为 TTS、`speech.pipeline == "round"`；最近一条清楚原话的 `at` 早于 `now-5000` 且晚于上一轮 `written_at`；距上一轮 ≥ 30 s；不是同一时刻；本小时 `mind_runs`（step 前缀 `say_`）花费 < 0.60。
  - 作废：`speech.profile` 或 `scope_since` 改变、关闭、重启（启动时把 `mind_state.say_prefetch.on` 置 false）。

- [ ] **Step 1: Write the failing tests**（假时钟、假 chat）：`test_writes_after_speech_ends`（最后一句后 4 s → `waiting`，6 s → `wrote`）；`test_spacing_30s`；`test_same_moment_not_rewritten`；`test_budget_window_resets_next_hour`；`test_off_costs_nothing`（关着时 100 步零调用）；`test_disable_invalidates_stock`；`test_click_during_prefetch_reuses_one_round`（写入进行中时 `claim` 等到写完并返回这一轮，`say_rounds` 只有 1 条）。
- [ ] **Step 2: Run** → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** → PASS。
- [ ] **Step 5: Commit** `feat(mind): pre-write talk-show rounds while people are talking (on/off, 30 s spacing, hourly cap)`

### Task 7: 开口入口与播放记录

**Files:**
- Modify: `server/mind/speak.py`（新增 `say_round`、`play_line`；`_finish` 补播放时间）
- Modify: `server/mind/speech_settings.py`（`pipeline` 允许 `"round"`；新增 `speech.round` 默认值与校验：`thin_no_speech: True`、`thin_judge_weak: True`、`huyu: True`、`moment_gap_s: 90`、`moment_window_s: 60`、`prefetch_quiet_s: 5`、`prefetch_spacing_s: 30`、`prefetch_usd_per_hour: 0.6`）
- Test: `tests/test_mind_say_entry.py`

**Interfaces:**
- Consumes: Task 3–6；现有 `speech_context.build`、`TtsHttp`、`cancel_pending`/`_check_pending`。
- Produces:
  - `say_round(env, store, s, roles, tts, now_ms, *, worker, force_new=False, trigger=None, is_current=None, settings_version=None) -> dict`：返回 `{"rid", "played": line_key | None, "why": "stock"|"checked"|"fresh"|"next"|"huyu"|"silent", "round": compact_round}`。流程按 spec 6、6A：同一时刻且有未播句 → 下一句（冷场时刻第二次起 → silent）；有存货 → 无新原话直接播 / 有则 `check_round`；否则现写。
  - `play_line(store, s, roles, tts, rid, line, now_ms, *, voice=None, dialect=None) -> dict`：重播或换嗓子重播；`casting` 记入新 utterance。
  - 每次播放写一条 `utterances`：现有字段 + `rid, line, moment_id, casting, tts_submit_at, play_start_at, play_end_at`（后两者由 `_finish` 根据 soul-tts `timings_s` 算出），`pipeline: "round"`；并 `push` 到 `say_rounds.plays`。

- [ ] **Step 1: Write the failing tests**：`test_round_refused_without_mouth_costs_nothing`；`test_stock_without_new_speech_plays_without_model_call`；`test_stock_with_new_speech_checks_once`；`test_same_moment_plays_next_line`；`test_thin_moment_huyu_then_silent`（第一次 `why=="huyu"`，第二次 `why=="silent"` 且 TTS 未调用）；`test_cancel_while_writing_never_speaks`；`test_utterance_has_play_times_and_casting`；`test_settings_round_defaults_validate`。
- [ ] **Step 2: Run** → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** `python3 -B -m unittest discover -s tests -p 'test_mind*'` → 全部 PASS。
- [ ] **Step 5: Commit** `feat(mind): one-button talk show — stock, check, fresh write, silence rule, play records`

### Task 8: API 与观察台代理

**Files:**
- Modify: `server/mind/api.py`（POST `/say/round {force_new}`、`/say/play {rid,line,voice?,dialect?}`、`/signal`、`/say/prefetch {enabled}`；GET `/say/rounds?from&to`、`/say/stats?days`、`/say/explain?rid`；POST 允许列表；`/say/round` 在 `mouth_mode()!="tts"` 时 409）
- Modify: `web/monitor/mind.mjs`（`PATHS`、`POST_PATHS` 加入上述路径，`PARAMS` 加 `rid`；`/say/round` POST 超时与 `/say` 相同）
- Modify: `web/monitor/server.mjs`（POST 允许列表同步）
- Test: `tests/test_mind_speech_api.py`（加用例）、`web/monitor/mind.test.mjs`（加用例）

**Interfaces:**
- Consumes: Task 5 `put_signal`/`stats`、Task 6 `Worker.set_enabled`、Task 7 `say_round`/`play_line`。
- Produces: 上述 HTTP 接口；`/say/rounds` 每项为 compact round（不含提示词全文与克隆音色 id）加折叠后的本轮标记。

- [ ] **Step 1: Write the failing tests**：Python 端每个新路由一条（含 409、非法信号 400、`/say/rounds` 不泄漏 `voice` 原始 id）；Node 端 `mind.test.mjs`：新路径被放行、未知路径仍 400。
- [ ] **Step 2: Run** `python3 -B -m unittest test_mind_speech_api -v` 与 `cd web/monitor && node --test mind.test.mjs` → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** → PASS；再跑 `cd web/monitor && node --test` 全部 PASS。
- [ ] **Step 5: Commit** `feat(mind,monitor): talk-show round, play, signal and prefetch endpoints`

### Task 9: 页面

**Files:**
- Modify: `web/monitor/public/say-ui.js`、`web/monitor/public/index.html`（台词面板）、`web/monitor/public/style.css`
- Test: `web/monitor/say-ui.test.mjs`

**Interfaces:**
- Consumes: Task 8 接口。
- Produces（导出供测试）：`roundCardHtml(round, marks) -> string`、`class RatingBuffer(send, {settleMs=600, max=5})`（`press(rid, line, polarity)` 合并连点；同句换极性时先结算前一组）、`sayRoundMessage(result) -> string`（`silent` → "这一刻先不说了"，`fresh` → 附"现写中"已用时）。
- 布局取 `.private/say-round-spike/index.html`：播出的那句最大；其余按评委分数；兜底句单列带普通话意思；每句 重播 / 换嗓子（下拉：五副嗓子 × 四种方言）/ 好 / 坏；卡片底部"这一轮都不行""重新写五句"、用时、评委是否觉得冷场；"提前写"开关和本小时花费在按钮旁。`speech.pipeline=="round"` 时五个角色按钮收进设置。

- [ ] **Step 1: Write the failing tests**：`roundCardHtml` 播出句在首位、`huyu` 显示 gloss；`RatingBuffer` 三连点 600 ms 后只发一条 `count:3`、第六下封顶 5、换极性先结算；`sayRoundMessage({why:"silent"})` 返回"这一刻先不说了"；HTML 转义。
- [ ] **Step 2: Run** `cd web/monitor && node --test say-ui.test.mjs` → FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** `node --test` → 全部 PASS；本机起观察台，用 Playwright（`.private/speech-fast/browser.mjs` 的写法）打开页面检查卡片渲染与窄屏不溢出。
- [ ] **Step 5: Commit** `feat(monitor): one-button talk-show card with ratings, recast and pre-write switch`

### Task 10: 上线前回放、部署、现场验收

**Files:**
- Modify: `docs/memory-speech.md`（新增"统一按钮第一步"一节：开关、流程、花费、回退方法 `speech.pipeline` 改回 `v2`）、`docs/session-handoff.md`
- Create (private): `.private/say-round-spike/replay_step1.py`

- [ ] **Step 1: 不出声回放（只跑几个典型，用户 2026-11-13 要求，免得太慢）**：从 11-12 的上下文挑 5 个典型时刻（有清楚原话的、转写很乱的、同一时刻连点的、安静的）调用 `write_round`（不接 TTS），并模拟一次提前写和点击；输出：时刻分组（应为 10 组）、写一轮服务端用时中位与最大（并行写目标中位 ≤ 2.2 s，加评委 ≤ 3.5 s）、模拟点击时"有存货无新原话"路径的服务端用时（目标 ≤ 0.2 s）与"需核对"路径（目标 ≤ 1.8 s）、每小时花费估计。花费上限 0.30 美元。结果给用户看。
- [ ] **Step 2: Deploy**：`server/mind/deploy.sh`；`node web/monitor/service.mjs stop && node web/monitor/service.mjs start`；设置里 `speech.pipeline` 改为 `round` 存新版本。核对服务器文件哈希与本地一致。
- [ ] **Step 3: 现场验收（用户在场）**：开提前写，讨论一段后点"说一轮"，记录点击到 `first_sound` 的时间；连点验证同一时刻下一句与冷场沉默；打几次好/坏，确认 `/say/stats` 两栏数字。失败条件：有存货时服务端 > 1.5 s（无新内容）或 > 2 s（需核对）；现写 > 4 s；同一时刻重写五句；提前写超过每小时预算；hello 自己的话出现在情景里。
- [ ] **Step 4: Commit** docs 与交接记录 `docs: talk-show one button, step 1 live`

## 不在本计划内

- ASR `forward`（开口）、`next`/`previous`（提前写开关）、`backward`：机器人现在没有把 ASR 词送到服务器的通道（见 `docs/superpowers/specs/2026-11-13-hello-modes-design.md` 的上行/下行阶段）。本计划把开口和开关都做成服务端函数，上行通道做好后直接调用。
- 第二步（记账、例子、效果页、试看页标记导入）与第三步（口味说明与上线闸门）各自另写计划。
- 遥控器 / DualKey 打分：`owner_signals` 已按 `source.device` 分栏，接入时只需写信号。
