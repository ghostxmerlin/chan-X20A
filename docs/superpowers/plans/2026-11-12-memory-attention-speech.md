# Memory, Attention and Grounded Speech Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地已批准的日常记忆、时段规律、关注判断、短口语角色与方言配置。

**Architecture:** Python/MongoDB 服务端保存版本与证据；手动说话先选题后写句；独立 worker 处理记忆，网页只读取分页结果。复用现有听觉、视觉和 TTS，不改机器人固件。

**Tech Stack:** Python 标准库、pymongo、现有 Luna/百炼接口、原生 JavaScript、unittest、node:test。

**Spec:** `docs/superpowers/specs/2026-11-12-memory-attention-speech-design.md`（用户已回复「实现」）。

## Global Constraints

- 手动点按钮才开口；无设备不调用模型；保留 v1 回退。
- 默认最近 30 秒即时原话、120 秒衔接，最多 12 组；视觉实际拍摄时间不超过 120 秒。
- 日常认识至少 3 个本地日期和 6 个独立十分钟窗；规律最近 28 天、至少 5 天且每天相应小时观察至少 15 分钟。
- 角色通常 8–30 字、最多 40 字，最多三候选、一次修复，不截断；鼓励上海话、伤心四川话、me01 武汉话待试听。
- 记忆按 scope 分开，稳定 owner ID；自声、试听、台词不学习；历史和 expected_version 冲突保护。
- 保留私有数据、现有环境/身份/凭据与固定录音；无新依赖、无固件构建刷机。

## Review Focus

- 历史旧设置缺新增字段、空或畸形嵌套字段：补默认且验证拒绝非法值，不能覆盖旧环境（Task 1）。
- 同一分钟迟到修订与 worker 在写后崩溃：证据替代、幂等、重试不多算一天/一窗（Task 2、4）。
- 听悟迟到、跨分钟或多句对一句：同段录音不重复加权，识别冲突保持未知（Task 3）。
- 页面保存/切场景与后台同时更新：CAS 冲突明确；旧场景纪要、缓存和记忆不泄漏到新场景（Task 1、3、5）。
- 模型失败、超长、重复、引用不存在、预算不足与播放器中途断开：有限降级/修复，错误与费用可见，不能播幻觉兜底（Task 3、6）。

---

### Task 1: 角色、配置与版本边界

**Files:** Create `server/mind/speech_settings.py`, `tests/test_mind_speech_settings.py`; modify `settings.py`, `settings_store.py`, `config/roles/*.json`.

**Interfaces:** `speech_settings.defaults() -> dict`; `validate(speech, memory) -> list[str]`; `effective(s:dict) -> dict`; `role_config(role:dict,s:dict) -> dict`; `scope(s) -> {id,since,profile}`. SettingsStore.save(s,note='',expected_version=None) returns int, raises SettingsConflict on stale version. New settings include speech and memory. Source settings keep old environment; effective settings substitute selected profile environment.

- [ ] Write tests: legacy defaults keep office environment; home environment is empty; malformed types/NaN/unknown role overrides refused; stale save changes nothing; profile switches stamp boundary; actual role_config resolves 四川/武汉 instruction while preserving voice.
- [ ] Run `python -B -m unittest discover -s tests -p 'test_mind_speech_settings.py' -v`, observe missing behavior.
- [ ] Implement bounded validation, migration defaults, CAS saves using unique per-revision key compatible with legacy records, profile boundary and role settings; rewrite five persona files from approved spec.
- [ ] Run settings and role tests; existing legacy speak tests explicitly use v1 so rollback remains exercised.
- [ ] Commit task; complete with `python -B -m unittest discover -s tests -p 'test_mind_*settings.py' -v`.

### Task 2: 可追溯记忆存储与规律

**Files:** Create `server/mind/memory.py`, `tests/test_mind_memory.py`; extend test Mongo helpers only as required for real query semantics.

**Interfaces:** `MemoryStore(db,lane='live')`; `ensure_indexes()`; `observe(scope_id,window_ms,observations,coverage,source_version,now_ms,s)`; `reconcile(scope_id,now_ms,s)`; `list(scope_id,offset=0,limit=20,status=None)`; `history(memory_id,offset=0,limit=20)`; `edit(memory_id,expected_version,action,statement=None,restore_version=None,now_ms=None)`; `recall(scope_id,now_ms,limit=12)`. Entries are plain JSON with evidence refs and immutable versions; worker consumes observe/reconcile, API consumes list/history/edit, speech consumes recall.

- [ ] Write tests: six windows over three days promote, same-day repetition does not; revisions replace evidence; offline not absence; five covered days form routine; stale automatic memory demotes; manual disabled/corrected entry resists learning; restore creates version; stale edit conflicts; scope/lane isolation.
- [ ] Run `python -B -m unittest discover -s tests -p 'test_mind_memory.py' -v` red.
- [ ] Implement evidence upserts by window/source, latest-version ledger, deterministic statistics and CAS edits; use bounded/paginated Mongo queries and deterministic memory keys.
- [ ] Run new tests plus existing mind tests.
- [ ] Commit; complete using Task 2 test command.

### Task 3: 输入整理、关注判断与 say-v2

**Files:** Create `server/mind/speech_context.py`, `attention.py`, `speech_generation.py`, `tests/test_mind_speech_v2.py`; modify `speak.py` and legacy tests.

**Interfaces:** `speech_context.build(db,store,s,now_ms) -> dict` with evidence IDs, source metadata, scope, memories, recent spoken lines; `attention.choose(env,store,s,ctx,now_ms,chat=None) -> dict`; `speech_generation.generate(env,store,s,role,ctx,focus,now_ms,chat=None) -> dict` containing text, candidate metadata and run_id. `speak.say` dispatches by s.speech.pipeline; role_config applied to both say and new role audition. NoSituation / BudgetExceeded errors handled by API in Task 5.

- [ ] Write tests for fresh speech vs conflicting notes, aligned ASR groups, source disable/self filtering, stale vision and old open conversation, scope boundary/cache invalidation, unreliable emotion and supported repeats, real played-only history.
- [ ] Run new test file red; implement context using raw records and existing overlap logic, fresh source timestamps and stable identities; query memory read-only.
- [ ] Write tests for model invalid refs→reliable speech fallback, no evidence→no TTS, candidate rejection/one repair/no trimming, de-duplication, budget/error records, role instruction propagation and actual result state.
- [ ] Run red; implement two structured calls with bounded candidates/cache and explicit source guardrails, reuse existing cost bookkeeping and TTS completion; retain v1 function and tests.
- [ ] Run `python -B -m unittest discover -s tests -p 'test_mind_speak.py' -v` and new v2 tests.
- [ ] Commit; complete using `python -B -m unittest discover -s tests -p 'test_mind_speech*.py' -v`.

### Task 4: 增量学习服务

**Files:** Create `server/mind/memory_worker.py`, `tests/test_mind_memory_worker.py`; modify deploy/remote_setup, runner/eye_runner/notes_runner as needed to use effective scene settings without changing ingestion.

**Interfaces:** `memory_worker.tick(db,store,settings_store,env,now_ms,chat=None) -> dict`; independently deployed `python -m mind.memory_worker`, existing env/token/settings CLI conventions; uses MemoryStore and settings scope boundaries. Worker status in mind_memory_jobs and costs in mind_runs.

- [ ] Write tests for initial deployment cursor/no backfill; ten-minute aligned processing and switches; sources off/no valid data→no call; extracted assertions require valid refs; canonical owner; window revision/restart idempotence; 48h catchup cap, failed call retry/budget and settings switch boundaries.
- [ ] Run red; implement strict observation schema, grouped batch evidence/coverage, bounded retries, change fingerprint, lease ownership and cursor only after successful persistence. Do not derive facts from generated speech or unsupported summaries.
- [ ] Add deployment unit using same venv and existing private configuration; preserve old services and server settings.
- [ ] Run worker tests and shell syntax checks.
- [ ] Commit; complete using `python -B -m unittest discover -s tests -p 'test_mind_memory*.py' -v`.

### Task 5: 设置、记忆与诊断界面

**Files:** Modify `server/mind/api.py`, `web/monitor/mind.mjs`, `public/mind-ui.js`, `public/say-ui.js`, `public/mind.html/css`; create focused `public/speech-settings.js` and tests as appropriate; extend API and proxy tests.

**Interfaces:** GET `/memories?offset&limit&status`, `/memory/history?id&offset&limit`, `/speech/settings`, `/say/explain?uid`; POST `/memory/edit`, `/speech/settings` (expected_version), `/role/try`. All behind existing token/proxy allowlist; no secret voice IDs. Role audition uses saved role configuration and fixed text, never enters role history/memory.

- [ ] Write API tests for page limits, invalid IDs/actions, stale edits, role audition with TTS mode/device gate, no secret IDs, current settings history.
- [ ] Run red; implement API endpoints and allowlist query validation; preserve existing /settings clients with CAS support and useful conflict responses.
- [ ] Add scene/role settings, learning/apply toggles, memory list/history/correct/confirm/disable/restore and speech explanation with escaped text and no default expanded details; follow existing black/green layout.
- [ ] Verify UI behavioral tests: failed save preserves form, scene changes preserve other profiles, old async response cannot overwrite newer selection, audition sends selected role. Verify browser real page after deploy.
- [ ] Commit; complete with Python mind API tests and Node monitor tests.

### Task 6: 回放、部署与完整验证

**Files:** Add replay tool/fixtures and `docs/memory-speech.md`; update `server/mind/README.md`, session handoff; no private captured data committed.

- [ ] Run complete Python suite with project venv and complete Node suite; syntax-check deploy scripts. Fix all relevant regressions and inspect diff for secrets.
- [ ] Build deterministic replay cases covering spec acceptance; run actual model on bounded sanitized/private historical samples with no live writes or playback, record output, attention choices, costs and latency separately from unit results.
- [ ] Generate same-text 普通话/四川话/武汉话 samples with current aliases through synthesis-only isolated test harness; save private WAVs and metadata. Do not claim accent authenticity without user listening.
- [ ] Deploy only server/web within existing device-development authorization, verify health/settings/history/role instructions; monitor current hearing/preview counters while memory work executes. Do not change sensor or speaker mode merely to make a test pass; explicit audition is separate.
- [ ] Fresh whole-change review per executing-plans; fix important findings using regression tests and rerun affected/full suites.
- [ ] Commit verified implementation, document actual deployment and remaining subjective listening checks; do not push without current requested scope.

## Self-review

Spec sections 5/6/7/8/9/10 map to Tasks 2+4/3/1+3/1+5/1+3+6/5; history, scope, resource controls, rollback and all acceptance cases have owning tasks. Native execution follows user approval to implement; no additional approval checkpoint is needed for this plan.
