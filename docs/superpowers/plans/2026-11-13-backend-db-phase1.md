# 后端库 第 1 期：只建库（人物、条目、记忆、知识、声纹标注）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `hello` 库里建好五个新集合（带字段校验、索引、只追加版本和来历字段），在 mind 上开一组 `/db/` 读写接口，在情景页加一块"库"视图，让主人能手工录入人物、性格条目（含一次贴多条的初始录入）、记忆和知识，并看到每条的历史。

**Architecture:** 新模块 `server/mind/library.py` 管全部校验、写版本和读取，接口层 `server/mind/api.py` 只做参数解析和错误映射。观察台 `web/monitor/` 的代理白名单放行新路径和参数；新页面模块 `web/monitor/public/db-ui.js` 自带请求和转义，挂在 `mind.html` 上。这一期没有任何提示、模式、耳朵或付费模型读写这些集合。

**Tech Stack:** Python 3.11 标准库 + pymongo（服务器 venv），测试用 `tests/mind_fixtures.py` 的 `FakeDB` 和 `unittest`；Node 24 `node --test`（观察台代理与页面模块）。

**Spec:** `docs/superpowers/specs/2026-11-13-backend-people-knowledge-memory-design.md`，第 3 节（3.1 集合、3.2 谁写谁读、3.3 来历、3.5 接口和页面、3.6 保留、3.7 隐私、3.8 能试和失败条件），以及"用户的回答"第 1–6 条。

## Global Constraints

- 这一期不改任何提示词、不接任何模式、不改耳朵和固件、不调用任何付费模型；`mind_runs` 不因本功能新增任何记录。
- 只写五个新集合：`people_cards`、`people_notes`、`memory_items`、`knowledge_items`、`voice_labels`。不写任何已有集合，不碰录音目录。
- 只追加：改一条 = 插入一个新版本文档，带 `version`（从 1 起）、`actor`、`reason`、`changed_at`、`supersedes`（上一版号或 `None`）；旧版本永不修改。
- 没有删除接口，没有 TTL 索引，没有自动清理（用户回答第 5 条："不行，现在这种操作要手动维护"）。
- 编号：主人卡固定 `owner`；其余为前缀 + `_` + 12 位小写十六进制：人物卡 `p_`、人物条目 `n_`、记忆 `e_`、知识 `k_`、声纹标注 `l_`。
- 页面写入一律 `origin = "owner"`、`actor = "owner"`、`fusion = None`、`owner_original = None`、`derived_from = []`；这些字段只由服务端设置，页面带来就拒绝（400）。
- 融合版以后写入即 `active`（用户回答第 6 条）；本期没有融合，但"回到我的版本"（复制某个 `origin = "owner"` 的旧版本为新版本）要能用。
- 版本冲突返回 409，错误信息"已被改过，请刷新后重试"。
- 公开仓库：测试、示例、文档里只用"人物甲""地点乙"这类占位，不放真实名字、现场原话、服务器地址；日志不打条目文字。
- 每个新的 mind GET/POST 路径要加在三处：`server/mind/api.py`（`get`/`post` 分支和 `do_POST` 的白名单元组）、`web/monitor/server.mjs`（GET 和 POST 两个列表）、`web/monitor/mind.mjs`（`PATHS` / `POST_PATHS`）；新查询参数还要加进 `mind.mjs` 的 `PARAMS` 和 `check()` 的校验。每个新页面模块要加进 `server.mjs` 的 `assets`，并加进 `web/monitor/static-files.test.mjs` 的检查列表。
- 提交只在主人授权本次执行可以提交时做；否则跳过各任务的 Commit 步骤。

## Review Focus

- 页面伪造服务端字段（`origin`、`fusion`、`version`、`actor`、`owner_original`）或带未知字段写入 → 400，库里不多任何文档（Task 1 测试 `test_server_fields_and_unknown_fields_rejected`）。
- 两个页面同时改同一条 → 后到的 409，两个版本都不丢、旧版本内容不变（Task 1 测试 `test_concurrent_edit_conflicts_and_keeps_old_version`）。
- 条目里存了 `<img onerror=…>` 一类文字 → 页面所有视图按纯文字显示（Task 5 测试 `test_all_views_escape_stored_text`）。
- 条目、记忆、知识指向不存在的人或知识编号 → 400，不写入（Task 1 测试 `test_references_must_exist`）。
- 搜索框里输入引号、`$`、`.*`、超过 40 字或控制字符 → 代理拒绝过长和控制字符；服务端按普通子串匹配，不当正则（Task 4 测试 `test_db_query_validation`，Task 2 测试 `test_q_is_plain_substring`）。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| Create `server/mind/library.py` | 五个集合的常量、校验、写版本、恢复、读取、索引；声纹标注的文档构造（第 4 期才有入口） |
| Modify `server/mind/store.py:42-61` | `ensure_indexes()` 末尾调用 `Library(db).ensure_indexes()` |
| Modify `server/mind/api.py` | 新增 `/db/*` 的 GET/POST 分支；`do_POST` 白名单；`_run` 把 `LibraryConflict` 映射为 409；文件头接口列表 |
| Modify `server/mind/README.md` | 加一小节"库（第 1 期）"：集合、接口、只追加、无删除 |
| Create `tests/test_mind_library.py` | `Library` 的单元测试（FakeDB） |
| Create `tests/test_mind_library_api.py` | 经 HTTP 的接口测试 |
| Modify `web/monitor/mind.mjs:10-12`、`check()` | 新路径、新参数及校验 |
| Modify `web/monitor/server.mjs:17-20`、`:53`、`:83` | 新路径、新静态文件 `db-ui.js` |
| Modify `web/monitor/mind.test.mjs` | 代理校验测试 |
| Create `web/monitor/public/db-ui.js` | "库"视图：模型、HTML 生成、挂载 |
| Modify `web/monitor/public/mind.html` | 加 `<section id="db">` 和 `<script type="module" src="/db-ui.js">` |
| Modify `web/monitor/public/mind.css` | "库"视图的少量样式 |
| Create `web/monitor/db-ui.test.mjs` | 页面模块测试 |
| Modify `web/monitor/static-files.test.mjs` | 检查列表加 `db-ui.js` |

---

### Task 1: `library.py` 的写入：校验、版本、恢复、索引

**Files:**
- Create: `server/mind/library.py`
- Modify: `server/mind/store.py:42-61`
- Test: `tests/test_mind_library.py`

**Interfaces:**
- Consumes: `tests/mind_fixtures.FakeDB`（`insert_one` 只对 `_id` 判重，所以并发冲突要靠模块锁加"写前比对当前版本"，真库再靠唯一索引的 11000 兜底，照 `server/mind/memory.py:162-173` 的 `_append`）。
- Produces:
  - `COLLECTIONS = {'people_cards': 'person_id', 'people_notes': 'note_id', 'memory_items': 'memory_id', 'knowledge_items': 'kid'}`
  - `ID_RE = r'^(owner|[pnek]_[a-f0-9]{12})$'`
  - `class LibraryConflict(Exception)`
  - `class Library(db)`，方法：
    - `ensure_indexes() -> None`
    - `head(c: str, item_id: str) -> dict | None`（最新版本，去掉 `_id`）
    - `put(c: str, fields: dict, *, item_id: str | None, expected_version: int | None, scope_id: str, now_ms: int, reason: str = '') -> dict`（返回写入的版本文档）
    - `restore(c: str, item_id: str, expected_version: int, restore_version: int, now_ms: int) -> dict`
  - `voice_label(body: dict, now_ms: int) -> dict`（只构造并校验文档，不写库；第 4 期接入口）
  - 校验失败抛 `ValueError`（中文信息），编号不存在抛 `KeyError`。

**字段和取值**（照 spec 3.1，页面可写的部分；未列出的字段一律拒绝）：

| 集合 | 页面可写字段 | 约束 | 服务端设置 |
| --- | --- | --- | --- |
| `people_cards` | `kind`、`display`、`aliases`、`links.voice_names`、`links.face_names`、`treatment.tease`、`treatment.quote`、`scope_ids`、`status` | `kind` ∈ owner/regular/visitor/family/unknown；`display` 1–40 字必填；各列表 ≤10 项、每项 1–40 字（名字 1–32 字）；`tease` ∈ ok/words_only/never，缺省 words_only；`quote` ∈ ok/gist/no，缺省 gist；`status` ∈ confirmed/retired，缺省 confirmed；`kind = owner` 时编号为 `owner`，已存在则 409 | `person_id`、`owner_rotation = False`、`links.legacy_ids = []`、`merged_into = None`、`source = {'kind': 'owner', 'ref': None}`、`scope_ids` 缺省 `[scope_id]` |
| `people_notes` | `person_id`、`kind`、`text`、`status`、`observed_at` | `person_id` 必须是已有人物卡；`kind` ∈ trait/fact/preference/boundary/relation；`text` 1–200 字；`status` ∈ active/disabled/retired，缺省 active；`observed_at` 为毫秒整数或缺省 | `note_id`、`scope_id`、来历四字段 |
| `memory_items` | `kind`、`title`、`statement`、`subjects`、`topics`、`when`、`status` | `kind` ∈ event/told/feeling；`title` 1–30 字；`statement` 1–300 字；`subjects` ≤10 项，每项是 `owner`/`hello`/`room` 或已有人物卡；`topics` ≤10 项，每项是已有知识编号；`when = {type: point|span, start_ms, end_ms}`，span 要 `end_ms ≥ start_ms`，point 时 `end_ms = None`；`status` ∈ active/retired/disabled，缺省 active | `memory_id`、`scope_id`、`source = {'kind': 'owner', 'refs': []}` |
| `knowledge_items` | `kind`、`name`、`aliases`、`heard_as`、`statement`、`subjects`、`when`、`scope_ids`、`status` | `kind` ∈ place/work/commitment/life/term/self/other；`name` 1–40 字；`aliases`、`heard_as` ≤10 项；`statement` 0–400 字；`subjects` 同上；`when = {valid_from, valid_to}`，毫秒整数或 `None`；`status` ∈ active/stale/disabled，缺省 active | `kid`、`scope_ids` 缺省 `[scope_id]`、来历四字段 |

来历四字段（`people_notes`、`knowledge_items`）：`origin = 'owner'`、`owner_original = None`、`derived_from = []`、`fusion = None`。`memory_items` 不带这四个字段，用 `source`。

`put` 对已有条目是"整条替换为新内容"：页面送来全部可写字段，服务端补上服务端字段后写成新版本；`kind` 和 `person_id` 不能改（改了就 400）。

索引（spec 3.1）：
- `people_cards`：`(person_id, version)` 唯一；`(status, changed_at)`；`links.voice_names`；`links.face_names`；`aliases`。
- `people_notes`：`(note_id, version)` 唯一；`(person_id, kind, changed_at)`；`(origin, changed_at)`。
- `memory_items`：`(memory_id, version)` 唯一；`(scope_id, kind, when.start_ms)`；`subjects`；`topics`。
- `knowledge_items`：`(kid, version)` 唯一；`(kind, changed_at)`；`name`；`aliases`；`heard_as`；`subjects`。
- `voice_labels`：`lid` 唯一；`(target.session, target.speech_id, at)`；`(person_id, at)`；`review_day`。

`voice_label(body, now_ms)` 的结果：`{lid, at: now_ms, actor: 'owner', target: {session, speech_id, pos: [start, end], tingwu_n}, person_id, verdict, via, group, review_day, page_version}`；`verdict` ∈ is/not/several/hello_self/noise/unsure；`via` ∈ single/group；`group` 为 `None` 或 `{kind: tingwu_speaker|run, key}`；`verdict = 'is'` 时 `person_id` 必填；`pos` 两个非负整数且 `end > start`；`review_day` 形如 `2026-11-12`。

- [ ] **Step 1: 写失败的测试** `tests/test_mind_library.py`

```python
class LibraryWriteTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(); self.lib = Library(self.db)

    def card(self, **kw):
        return self.lib.put('people_cards', {'kind': 'regular', 'display': '人物甲', **kw},
                            item_id=None, expected_version=None, scope_id='office', now_ms=1000)

    def test_create_and_append_versions(self):
        a = self.card()
        self.assertRegex(a['person_id'], r'^p_[a-f0-9]{12}$')
        self.assertEqual((a['version'], a['supersedes'], a['actor']), (1, None, 'owner'))
        self.assertEqual(a['treatment'], {'tease': 'words_only', 'quote': 'gist'})
        b = self.lib.put('people_cards', {'kind': 'regular', 'display': '人物乙'}, item_id=a['person_id'],
                         expected_version=1, scope_id='office', now_ms=2000)
        self.assertEqual((b['version'], b['supersedes']), (2, 1))
        self.assertEqual(self.db.count('people_cards'), 2)
        self.assertEqual(self.lib.head('people_cards', a['person_id'])['display'], '人物乙')

    def test_concurrent_edit_conflicts_and_keeps_old_version(self):
        a = self.card()
        upd = dict(item_id=a['person_id'], expected_version=1, scope_id='office', now_ms=2000)
        self.lib.put('people_cards', {'kind': 'regular', 'display': '甲一'}, **upd)
        with self.assertRaises(LibraryConflict):
            self.lib.put('people_cards', {'kind': 'regular', 'display': '甲二'}, **upd)
        self.assertEqual([d['display'] for d in self.db['people_cards'].docs], ['人物甲', '甲一'])

    def test_owner_card_is_unique(self):
        self.assertEqual(self.card(kind='owner')['person_id'], 'owner')
        with self.assertRaises(LibraryConflict):
            self.card(kind='owner')

    def test_server_fields_and_unknown_fields_rejected(self):
        self.card(kind='owner')          # the person exists, so only the extra field can be the reason
        for extra in ({'origin': 'fused'}, {'fusion': {}}, {'version': 9}, {'actor': 'worker'},
                      {'owner_original': {}}, {'evil': 1}):
            with self.assertRaises(ValueError):
                self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '爱笑', **extra},
                             item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.assertEqual(self.db.count('people_notes'), 0)

    def test_note_provenance_defaults(self):
        self.card(kind='owner')
        n = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '爱笑'},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.assertEqual((n['origin'], n['owner_original'], n['derived_from'], n['fusion'], n['status']),
                         ('owner', None, [], None, 'active'))

    def test_references_must_exist(self):
        bad = [('people_notes', {'person_id': 'p_' + 'a' * 12, 'kind': 'trait', 'text': 'x'}),
               ('memory_items', {'kind': 'event', 'title': 't', 'statement': 's', 'subjects': ['p_' + 'b' * 12],
                                 'when': {'type': 'point', 'start_ms': 1, 'end_ms': None}}),
               ('memory_items', {'kind': 'event', 'title': 't', 'statement': 's', 'topics': ['k_' + 'c' * 12],
                                 'when': {'type': 'point', 'start_ms': 1, 'end_ms': None}})]
        for c, f in bad:
            with self.assertRaises(ValueError):
                self.lib.put(c, f, item_id=None, expected_version=None, scope_id='office', now_ms=1)

    def test_limits_and_enums(self):
        self.card(kind='owner')
        for f in ({'text': ''}, {'text': '字' * 201}, {'kind': 'health'}):
            with self.assertRaises(ValueError):
                self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '爱笑', **f},
                             item_id=None, expected_version=None, scope_id='office', now_ms=1)
        with self.assertRaises(ValueError):   # span must not end before it starts
            self.lib.put('memory_items', {'kind': 'event', 'title': 't', 'statement': 's',
                                          'when': {'type': 'span', 'start_ms': 5, 'end_ms': 4}},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)

    def test_kind_and_person_cannot_change(self):
        a = self.card()
        with self.assertRaises(ValueError):
            self.lib.put('people_cards', {'kind': 'visitor', 'display': '人物甲'}, item_id=a['person_id'],
                         expected_version=1, scope_id='office', now_ms=2)

    def test_restore_owner_version(self):
        self.card(kind='owner')
        n = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '原版'},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '改过'},
                     item_id=n['note_id'], expected_version=1, scope_id='office', now_ms=2)
        r = self.lib.restore('people_notes', n['note_id'], expected_version=2, restore_version=1, now_ms=3)
        self.assertEqual((r['version'], r['text'], r['origin'], r['supersedes']), (3, '原版', 'owner', 2))
        with self.assertRaises(KeyError):
            self.lib.restore('people_notes', n['note_id'], expected_version=3, restore_version=9, now_ms=4)

    def test_indexes_unique_on_id_and_version(self):
        self.lib.ensure_indexes()
        self.assertIn(([('kid', 1), ('version', 1)], True), self.db['knowledge_items'].indexes)
        self.assertIn(([('lid', 1)], True), self.db['voice_labels'].indexes)

    def test_store_ensure_indexes_creates_library_indexes(self):
        Store(self.db).ensure_indexes()
        self.assertTrue(self.db['people_cards'].indexes)

    def test_voice_label_schema(self):
        lab = voice_label({'target': {'session': '20261112/x-c1', 'speech_id': 's-1', 'pos': [0, 16000]},
                           'person_id': 'owner', 'verdict': 'is', 'via': 'single', 'review_day': '2026-11-12',
                           'page_version': 1}, now_ms=5)
        self.assertRegex(lab['lid'], r'^l_[a-f0-9]{12}$')
        self.assertEqual((lab['actor'], lab['at'], lab['group'], lab['target']['tingwu_n']), ('owner', 5, None, None))
        for bad in ({'verdict': 'is', 'person_id': None}, {'target': {'session': 's', 'speech_id': 'x', 'pos': [5, 5]}},
                    {'review_day': '昨天'}, {'verdict': 'maybe'}):
            with self.assertRaises(ValueError):
                voice_label({**lab, **bad}, now_ms=5)
```

（导入：`from mind_fixtures import FakeDB`、`from mind.library import Library, LibraryConflict, voice_label`、`from mind.store import Store`，照 `tests/test_mind_speech_api.py` 开头的 `sys.path` 写法。）

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_library -v`（在仓库根目录）
Expected: FAIL，`ModuleNotFoundError: No module named 'mind.library'`

- [ ] **Step 3: 实现 `server/mind/library.py` 的写入部分**

每个集合一张"可写字段 → 校验函数"的表，`put` 先拒绝表外字段，再逐项校验、补缺省值、补服务端字段。写入照 `memory.py` 的 `_append`：模块级 `threading.RLock`；锁内取 `head`，新建时 `expected_version` 必须为 `None`/0，已有时必须等于当前版本，否则 `LibraryConflict('已被改过，请刷新后重试')`；`insert_one` 抛 `code == 11000` 时也转成 `LibraryConflict`。编号用 `uuid.uuid4().hex[:12]`。`restore` 只接受 `origin == 'owner'` 的旧版本（人物卡和记忆没有 `origin`，任意旧版本都可以），复制其可写字段后走 `put` 的写入路径，`reason = 'restore'`。`store.py` 的 `ensure_indexes()` 末尾加一行调用 `Library(self.db).ensure_indexes()`（函数内导入，避免循环导入）。

- [ ] **Step 4: 运行，确认通过；跑全部 mind 测试确认没有回归**

Run: `python3 -B -m unittest tests.test_mind_library -v && python3 -B -m unittest discover -s tests -p 'test_mind_*.py'`
Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add server/mind/library.py server/mind/store.py tests/test_mind_library.py
git commit -m "mind: library collections with append-only versions (backend phase 1)"
```

---

### Task 2: `library.py` 的读取：总览、人物、记忆、知识、单条历史

**Files:**
- Modify: `server/mind/library.py`
- Test: `tests/test_mind_library.py`

**Interfaces:**
- Consumes: Task 1 的 `Library`、`COLLECTIONS`。
- Produces（`Library` 的方法，返回值都可直接 JSON 化，不含 `_id`）：
  - `overview(s: dict) -> {'collections': {c: {'total': int, 'by_status': {str: int}, 'by_origin': {str: int}}}, 'voice_labels': {'total': int}, 'existing': {'owner_voice_names': int, 'owner_face_names': int, 'settings_people': int, 'mind_memories': int}}`；`c` 为 `COLLECTIONS` 的四个键；`total` 数的是不同编号（最新版本），不是版本数；`by_origin` 对没有 `origin` 的集合为 `{}`；`s` 是当前设置（`api.py` 的 `current_settings()[0]`）；`mind_memories` 数不同 `memory_id`。
  - `people() -> list[dict]`：每张卡的最新版本，加 `notes: int`（该人 `status == 'active'` 的条目数），主人卡在最前，其余按 `changed_at` 倒序。
  - `person(person_id: str) -> {'card': dict, 'versions': list[dict], 'notes': list[dict], 'memories': list[dict], 'knowledge': list[dict]}`；不存在抛 `KeyError`。`notes` 是该人全部条目的最新版本；`memories`/`knowledge` 是 `subjects` 含此人的最新版本。
  - `memories(kind=None, person=None, topic=None, q=None, from_ms=None, to_ms=None, offset=0, limit=20) -> {'items', 'total', 'offset', 'limit'}`：按 `when.start_ms` 倒序。
  - `knowledge(kind=None, person=None, q=None, offset=0, limit=20) -> 同上`：按 `changed_at` 倒序。
  - `item(c: str, item_id: str) -> {'versions': list[dict], 'owner_original': dict | None, 'children': list[dict]}`：`versions` 倒序最多 50 个；`owner_original` 是最新的 `origin == 'owner'` 版本（只对 `people_notes`、`knowledge_items`，其余为 `None`）；`children` 是 `topics` 含此知识编号的记忆最新版本（只对 `knowledge_items`）；不存在抛 `KeyError`。
  - 分页：`offset` 0–100000、`limit` 1–100，越界 `ValueError`（照 `memory.py` 的 `_page`）。

取最新版本用 `memory.py` 的 `_heads_pipeline` 同款聚合（`$match` → `$sort version -1` → `$group $first` → `$replaceRoot`，FakeDB 支持），只在 `$match` 里放标量条件（`kind`、`person_id`）；`subjects`/`topics` 含某编号、`q`、时间范围都在 Python 里过滤（FakeDB 不支持数组匹配和 `$regex`；本期量小）。每次最多取 5000 个最新版本。`q` 是普通子串，在 `title`、`statement`、`name`、`aliases`、`text` 里查，不编译正则。

- [ ] **Step 1: 写失败的测试**（加到 `tests/test_mind_library.py`，新类 `LibraryReadTest`）

`setUp` 用 Task 1 的 `put` 造数据，并把编号存成属性：
- 主人卡；人物甲（`self.jia`）；
- 甲的两条 `trait` 条目，其中一条再改一次（`self.edited_note`，共 2 个版本）；
- 知识"登山团体"（`kind='commitment'`，`subjects=['owner']`，`self.climb`）；
- 记忆"第一次登山"（`start_ms=1000`）、"最近一次登山"（`start_ms=3000`），都是 `topics=[self.climb]`；
- 记忆"和甲吃饭"（`start_ms=2000`，`subjects=[self.jia]`）。

```python
    def test_overview_counts_ids_not_versions(self):
        o = self.lib.overview(settings.load(None))
        self.assertEqual(o['collections']['people_notes']['total'], 2)
        self.assertEqual(o['collections']['people_notes']['by_origin'], {'owner': 2})
        self.assertEqual(o['voice_labels']['total'], 0)
        self.assertEqual(set(o['existing']), {'owner_voice_names', 'owner_face_names', 'settings_people', 'mind_memories'})

    def test_people_owner_first_with_note_counts(self):
        rows = self.lib.people()
        self.assertEqual(rows[0]['person_id'], 'owner')
        self.assertEqual(next(r for r in rows if r['person_id'] == self.jia)['notes'], 2)

    def test_person_view_gathers_notes_memories_knowledge(self):
        p = self.lib.person(self.jia)
        self.assertEqual(len(p['notes']), 2)
        self.assertEqual([m['title'] for m in p['memories']], ['和甲吃饭'])
        with self.assertRaises(KeyError):
            self.lib.person('p_' + '0' * 12)

    def test_memories_filters_and_paging(self):
        self.assertEqual(self.lib.memories(topic=self.climb)['total'], 2)
        self.assertEqual(self.lib.memories(person=self.jia)['total'], 1)
        self.assertEqual(self.lib.memories(limit=1)['items'][0]['title'], '最近一次登山')   # newest start_ms first
        with self.assertRaises(ValueError):
            self.lib.memories(limit=101)

    def test_q_is_plain_substring(self):
        self.assertEqual(self.lib.knowledge(q='登山')['total'], 1)
        self.assertEqual(self.lib.knowledge(q='.*')['total'], 0)
        self.assertEqual(self.lib.memories(q='$ne')['total'], 0)

    def test_item_history_owner_original_and_children(self):
        it = self.lib.item('people_notes', self.edited_note)
        self.assertEqual([v['version'] for v in it['versions']], [2, 1])
        self.assertEqual(it['owner_original']['version'], 2)
        self.assertEqual(len(self.lib.item('knowledge_items', self.climb)['children']), 2)
        with self.assertRaises(KeyError):
            self.lib.item('memory_items', 'e_' + '0' * 12)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_library -v`
Expected: FAIL，`AttributeError: 'Library' object has no attribute 'overview'`

- [ ] **Step 3: 实现上面 Produces 里的六个读取方法**

- [ ] **Step 4: 运行，确认通过**

Run: `python3 -B -m unittest tests.test_mind_library -v`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add server/mind/library.py tests/test_mind_library.py
git commit -m "mind: library read views (overview, people, memories, knowledge, history)"
```

---

### Task 3: mind 接口 `/db/*`

**Files:**
- Modify: `server/mind/api.py`（文件头接口列表；`get()`、`post()` 新分支；`do_POST` 的路径元组 `:557-558`；`_run` 的 409 映射 `:540`）
- Modify: `server/mind/README.md`
- Test: `tests/test_mind_library_api.py`

**Interfaces:**
- Consumes: Task 1、2 的 `Library` 方法和 `LibraryConflict`；`speech_settings.scope(current_settings()[0])['id']` 作为写入时的 `scope_id`。
- Produces（经 mind 接口，鉴权照旧）：

| 方法 路径 | 参数 / 正文 | 调用 |
| --- | --- | --- |
| `GET /db/overview` | — | `overview(current_settings()[0])` |
| `GET /db/people` | — | `{'items': people()}` |
| `GET /db/person` | `id` | `person(id)` |
| `GET /db/memories` | `kind`、`person`、`topic`、`q`、`from`、`to`、`offset`、`limit` | `memories(...)` |
| `GET /db/knowledge` | `kind`、`person`、`q`、`offset`、`limit` | `knowledge(...)` |
| `GET /db/item` | `c`、`id` | `item(c, id)` |
| `POST /db/person` | `{id?, expected_version?, fields, reason?}` 或 `{id, expected_version, restore_version}` | `put('people_cards', …)` 或 `restore(…)` |
| `POST /db/person/note` | 同上 | 集合 `people_notes` |
| `POST /db/memory` | 同上 | 集合 `memory_items` |
| `POST /db/knowledge` | 同上 | 集合 `knowledge_items` |

错误映射：`ValueError` → 400，`KeyError` → 404，`LibraryConflict` → 409（加进 `_run` 的 409 元组）。`id` 参数不合 `ID_RE`、`c` 不在 `COLLECTIONS` → 400。正文顶层只允许 `id`、`expected_version`、`fields`、`reason`、`restore_version`；`reason` 截到 200 字。

- [ ] **Step 1: 写失败的测试** `tests/test_mind_library_api.py`（结构照 `tests/test_mind_speech_api.py`：`setUp` 起 `api.make_server(Store(db), 'k', port=0, ctx={'settings_store': SettingsStore(db, '/nonexistent.json')})`，`call(path, body=None)` 返回 `(status, json)`）

```python
    def test_create_read_edit_conflict(self):
        s, card = self.call('/db/person', {'fields': {'kind': 'regular', 'display': '人物甲'}})
        self.assertEqual((s, card['version']), (200, 1))
        pid = card['person_id']
        body = {'id': pid, 'expected_version': 1, 'fields': {'kind': 'regular', 'display': '人物乙'}}
        self.assertEqual(self.call('/db/person', body)[0], 200)
        self.assertEqual(self.call('/db/person', body)[0], 409)
        self.assertEqual(self.call('/db/item?c=people_cards&id=' + pid)[1]['versions'][0]['display'], '人物乙')
        self.assertEqual(self.call('/db/people')[1]['items'][0]['person_id'], pid)

    def test_notes_bulk_entry_shape_and_restore(self):
        self.call('/db/person', {'fields': {'kind': 'owner', 'display': '主人'}})
        ids = [self.call('/db/person/note', {'fields': {'person_id': 'owner', 'kind': 'trait', 'text': t}})[1]['note_id']
               for t in ('爱笑', '怕冷')]
        self.assertEqual(len(self.call('/db/person?id=owner')[1]['notes']), 2)
        self.call('/db/person/note', {'id': ids[0], 'expected_version': 1,
                                      'fields': {'person_id': 'owner', 'kind': 'trait', 'text': '改过'}})
        s, r = self.call('/db/person/note', {'id': ids[0], 'expected_version': 2, 'restore_version': 1})
        self.assertEqual((s, r['text']), (200, '爱笑'))

    def test_bad_input_codes(self):
        self.assertEqual(self.call('/db/person?id=%24ne')[0], 400)
        self.assertEqual(self.call('/db/person?id=p_' + '0' * 12)[0], 404)
        self.assertEqual(self.call('/db/item?c=sessions&id=owner')[0], 400)
        self.assertEqual(self.call('/db/memories?limit=101')[0], 400)
        self.assertEqual(self.call('/db/memory', {'fields': {'kind': 'event'}, 'evil': 1})[0], 400)
        self.assertEqual(self.call('/db/knowledge', {'fields': {'kind': 'term', 'name': 'x', 'origin': 'fused'}})[0], 400)

    def test_writes_only_library_collections_and_no_model_calls(self):
        before = {k: len(v.docs) for k, v in self.db.collections.items()}
        self.call('/db/knowledge', {'fields': {'kind': 'work', 'name': '工作背景', 'statement': '占位'}})
        changed = {k for k, v in self.db.collections.items() if len(v.docs) != before.get(k, 0)}
        self.assertEqual(changed, {'knowledge_items'})
        self.assertEqual(self.db.count('mind_runs'), 0)

    def test_overview(self):
        o = self.call('/db/overview')[1]
        self.assertEqual(o['collections']['memory_items']['total'], 0)
        self.assertIn('voice_labels', o)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -B -m unittest tests.test_mind_library_api -v`
Expected: FAIL，新路径返回 404（GET）或 404 "not found"（POST 白名单拒绝）

- [ ] **Step 3: 在 `api.py` 加分支、白名单、409 映射；文件头的 GET/POST 列表补上 `/db/*`**

- [ ] **Step 4: 在 `server/mind/README.md` 加一小节"库（第 1 期）"**：五个集合各一句、`/db/*` 接口表、"只追加，没有删除接口，删除只由主人手动维护数据库"、"本期没有提示读取这些集合"、指向 spec 第 3 节。

- [ ] **Step 5: 运行，确认通过；跑全部 Python 测试**

Run: `python3 -B -m unittest tests.test_mind_library_api -v && python3 -B -m unittest discover -s tests -v`
Expected: 全部 OK（已有的旧依赖跳过项照旧跳过）

- [ ] **Step 6: Commit**

```bash
git add server/mind/api.py server/mind/README.md tests/test_mind_library_api.py
git commit -m "mind: /db endpoints for the people, memory and knowledge library"
```

---

### Task 4: 观察台代理放行 `/db/*`

**Files:**
- Modify: `web/monitor/mind.mjs:10-12` 和 `check()`（`:33-41`）
- Modify: `web/monitor/server.mjs:53`（POST 列表）、`:83`（GET 列表）
- Test: `web/monitor/mind.test.mjs`

**Interfaces:**
- Consumes: Task 3 的路径和参数。
- Produces:
  - `PATHS` 加 `/db/overview`、`/db/people`、`/db/person`、`/db/memories`、`/db/knowledge`、`/db/item`；`POST_PATHS` 和 `server.mjs` POST 列表加 `/db/person`、`/db/person/note`、`/db/memory`、`/db/knowledge`；`server.mjs` GET 列表加六个 GET 路径。
  - `PARAMS` 加 `kind`、`person`、`topic`、`q`、`c`。
  - 校验：
    - `id`：`/^(m[a-f0-9]{24}|owner|[pnek]_[a-f0-9]{12})$/`（保留旧的 `m…` 记忆编号）；
    - `person`、`topic`：`/^(owner|[pnek]_[a-f0-9]{12})$/`；
    - `kind`：两库种类的并集 `owner regular visitor family unknown trait fact preference boundary relation event told feeling place work commitment life term self other`；
    - `c`：`people_cards people_notes memory_items knowledge_items`；
    - `q`：解码后 1–40 个字符，不含 `\u0000-\u001f`；
    - 其余参数照旧。

- [ ] **Step 1: 写失败的测试**（加到 `web/monitor/mind.test.mjs`）

```js
test('db query validation',()=>{
 for(const p of ['/db/overview','/db/people','/db/person?id=owner','/db/person?id=p_0123456789ab',
   '/db/memories?kind=event&person=p_0123456789ab&topic=k_0123456789ab&offset=0&limit=20',
   '/db/knowledge?q='+encodeURIComponent('登山 "$ne" .*'),'/db/item?c=people_notes&id=n_0123456789ab',
   '/memory/history?id=m'+'a'.repeat(24)])
  assert.equal(MindProxy.check(p),p);
 for(const p of ['/db/person?id=%24ne','/db/item?c=sessions&id=owner','/db/memories?kind=health',
   '/db/knowledge?q='+'字'.repeat(41),'/db/knowledge?q=%00x','/db/knowledge?q=','/db/memories?person=../x'])
  assert.equal(MindProxy.check(p),null);
});
test('db writes are forwarded and other writes still refused',async()=>{
 const seen=[],proxy=new MindProxy({ssh:'h',token:'k',socketPath:SOCK,prepareFn:()=>{},spawnFn:fakeSsh(),requestFn:okFetch(seen)});
 for(const p of ['/db/person','/db/person/note','/db/memory','/db/knowledge'])assert.equal((await proxy.post(p,'{}')).status,200);
 assert.equal((await proxy.post('/db/delete','{}')).status,400);
});
```

另在同文件已有的 `createMonitorServer` 路由测试旁加一条：`GET /api/mind/db/overview` 和 `POST /api/mind/db/memory` 不返回"未找到页面"（照该文件现有的服务器路由测试写法）。

- [ ] **Step 2: 运行，确认失败**

Run: `node --test web/monitor/mind.test.mjs`
Expected: FAIL，`check()` 对 `/db/*` 返回 `null`

- [ ] **Step 3: 改 `mind.mjs` 和 `server.mjs` 的三个列表、`PARAMS` 和校验**

- [ ] **Step 4: 运行，确认通过；跑全部观察台测试**

Run: `node --test web/monitor/*.test.mjs`
Expected: 全部 pass

- [ ] **Step 5: Commit**

```bash
git add web/monitor/mind.mjs web/monitor/server.mjs web/monitor/mind.test.mjs
git commit -m "monitor: forward /db library endpoints"
```

---

### Task 5: 情景页的"库"视图和初始性格录入

**Files:**
- Create: `web/monitor/public/db-ui.js`
- Modify: `web/monitor/public/mind.html`（在 `<main>` 末尾加 `<section id="db"><h2>库</h2><div id="db-root"></div></section>`；`<head>` 加 `<script type="module" src="/db-ui.js"></script>`）
- Modify: `web/monitor/public/mind.css`
- Modify: `web/monitor/server.mjs:17-20`（`assets` 加 `['/db-ui.js', ['db-ui.js','text/javascript; charset=utf-8']]`）
- Modify: `web/monitor/static-files.test.mjs`（检查列表 `['app.js','say-ui.js','scene-ui.js']` 加 `'db-ui.js'`）
- Test: `web/monitor/db-ui.test.mjs`

**Interfaces:**
- Consumes: Task 3 的接口经 `/api/mind/db/*`；返回结构见 Task 2 Produces。
- Produces（`db-ui.js` 导出）：
  - `request(path, body?) -> Promise<object>`：自带的一份，写法照 `speech-settings.js` 的 `request`。**不要**从 `/speech-settings.js` 导入：测试用 `data:` URL 加载模块，绝对路径导入在那里解析不了。
  - `esc(s) -> string`：照 `speech-settings.js` 的转义，转 `& < > " '` 五个字符。
  - `class LibraryModel(req = request)`：
    - `overview()`、`people()`、`person(id)`、`memories(filters)`、`knowledge(filters)`、`item(c, id)`：每个读取各自带序号，过时的响应返回 `false` 且不覆盖新的结果，照 `SpeechSettingsModel` 的 `loadSeq` / `pageSeq`；
    - `save(path, fields, {id, expected_version} = {})`：返回写入的版本；
    - `restore(path, id, expected_version, restore_version)`；
    - `addNotes(personId, kind, text) -> Promise<{saved: number, failed: [{line: string, error: string}]}>`：按行拆，去掉首尾空白和空行；每行一条，依次 POST `/db/person/note`；单行失败不停，记进 `failed`；超过 200 字的行不发送，直接记 `failed`，`error` 为"超过 200 字"。
  - HTML 函数（只返回字符串，所有存储的文字都经 `esc`）：`overviewHtml(o)`、`peopleHtml(items)`、`personHtml(p)`、`memoriesHtml(page)`、`knowledgeHtml(page)`、`historyHtml(item)`、`voiceLabelsHtml(o)`。
  - `mount(root, model = new LibraryModel())`：画出标签"人物 / 记忆 / 知识 / 现有日常记忆 / 声纹标注"，绑定按钮。"现有日常记忆"只列 `GET /memories?offset=0&limit=10` 的条目（只读，编辑仍在设置里的原有一栏）。

**页面文字（照 spec 3.5、3.8）：**
- 顶部数字行：每个集合的条数；声纹标注条数。
- 固定提示一行："这些内容只在服务器数据库里，不进仓库；现在没有任何提示在读它们。没有删除，只能停用。"
- 人物：列表（称呼、种类、条目数）；"加一个人"表单（称呼、种类、开玩笑、引用原话）。点开人看卡片、条目、和他有关的记忆和知识、历史；可以改对待方式、停用（`status = retired`）。
- 人物详情里的"一次加多条"：一个种类下拉（性格 / 稳定事实 / 喜好 / 不能碰的 / 和主人的关系）、一个多行框（提示"每行一条，最多 200 字"）、"保存"按钮；保存后显示"存了 N 条"，失败的行留在框里并逐行写原因。这就是初始性格录入的入口。
- 记忆：按种类筛、按人筛；"加一件事"表单（种类、标题、内容、涉及谁、关联的知识、时间点或时间段）。
- 知识：按种类筛；"加一条知识"表单；长期任务点开显示挂在下面的记忆（`children`）。
- 每条都有"历史"按钮，用 `historyHtml`：版本列表（第几版、时间、谁、原因）；人物条目和知识另有两栏"主人原版 / 当前版"，以及"回到我的版本"按钮（当前版不是主人原版时才显示）。
- 声纹标注：显示条数（本期为 0）和一句"第 4 期的每日回顾页会写这里"。
- 409 时提示"已被改过，请刷新后重试"，并重新读取这一条。

- [ ] **Step 1: 写失败的测试** `web/monitor/db-ui.test.mjs`（加载方式照 `speech-settings.test.mjs`：`import('data:text/javascript;base64,'+…)`）

```js
test('addNotes sends one note per non-empty line and reports failures',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push([p,b]);if(b.fields.text==='坏')throw Error('不合法');return {version:1};});
 const r=await m.addNotes('owner','trait','爱笑\n\n  怕冷  \n坏\n'+'字'.repeat(201));
 assert.deepEqual(calls.map(c=>c[1].fields.text),['爱笑','怕冷','坏']);
 assert.ok(calls.every(c=>c[0]==='/db/person/note'&&c[1].fields.person_id==='owner'&&c[1].fields.kind==='trait'));
 assert.equal(r.saved,2);
 assert.deepEqual(r.failed.map(f=>f.error),['不合法','超过 200 字']);
});
test('stale reads cannot overwrite newer ones',async()=>{
 const rs=[];const m=new ui.LibraryModel(()=>new Promise(r=>rs.push(r)));
 const a=m.person('owner'),b=m.person('p_0123456789ab');
 rs[1]({card:{person_id:'p_0123456789ab'}});await b;rs[0]({card:{person_id:'owner'}});assert.equal(await a,false);
});
test('save sends id and expected_version only when editing',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push(b);return {};});
 await m.save('/db/memory',{kind:'event'});await m.save('/db/memory',{kind:'event'},{id:'e_0123456789ab',expected_version:3});
 assert.deepEqual(Object.keys(calls[0]),['fields']);assert.equal(calls[1].expected_version,3);
});
test('all views escape stored text',()=>{
 const x='<img src=x onerror=alert(1)>';
 const htmls=[ui.peopleHtml([{person_id:'p_0123456789ab',display:x,kind:'regular',notes:1}]),
  ui.personHtml({card:{person_id:'owner',display:x,kind:'owner',treatment:{tease:'ok',quote:'gist'},aliases:[x]},versions:[],notes:[{note_id:'n_0123456789ab',kind:'trait',text:x,version:1}],memories:[],knowledge:[]}),
  ui.memoriesHtml({items:[{memory_id:'e_0123456789ab',kind:'event',title:x,statement:x,subjects:[],when:{type:'point',start_ms:1}}],total:1,offset:0,limit:20}),
  ui.knowledgeHtml({items:[{kid:'k_0123456789ab',kind:'term',name:x,aliases:[x],heard_as:[x],statement:x}],total:1,offset:0,limit:20}),
  ui.historyHtml({versions:[{version:2,text:x,reason:x,actor:'owner',changed_at:1,origin:'owner'}],owner_original:{version:2,text:x},children:[]})];
 for(const h of htmls){assert.doesNotMatch(h,/<img/);assert.match(h,/&lt;img/);}
});
test('history shows owner original and offers switching back only when current differs',()=>{
 const owner={version:1,text:'原版',origin:'owner'},fused={version:2,text:'融合',origin:'fused'};
 assert.match(ui.historyHtml({versions:[fused,owner],owner_original:owner,children:[]}),/回到我的版本/);
 assert.doesNotMatch(ui.historyHtml({versions:[owner],owner_original:owner,children:[]}),/回到我的版本/);
});
test('overview shows counts and the server-only notice',()=>{
 const h=ui.overviewHtml({collections:{people_cards:{total:2},people_notes:{total:5},memory_items:{total:0},knowledge_items:{total:1}},voice_labels:{total:0},existing:{mind_memories:7}});
 assert.match(h,/5/);assert.match(h,/不进仓库/);assert.match(h,/没有删除/);
});
```

- [ ] **Step 2: 运行，确认失败**

Run: `node --test web/monitor/db-ui.test.mjs web/monitor/static-files.test.mjs`
Expected: FAIL，`db-ui.js` 不存在；静态文件测试在列表加上 `db-ui.js` 后报读不到文件

- [ ] **Step 3: 实现 `db-ui.js`，改 `mind.html`、`mind.css`、`server.mjs` 的 `assets`、`static-files.test.mjs` 的列表**

- [ ] **Step 4: 运行，确认通过；跑全部观察台测试**

Run: `node --test web/monitor/*.test.mjs`
Expected: 全部 pass

- [ ] **Step 5: 本机看一眼页面**：用观察台说明里的入口启动（`web/monitor/README.md`），打开情景页，确认"库"一块能画出来（连不上服务器时显示读取失败而不是白屏），窄屏 400 px 下没有横向滚动。

- [ ] **Step 6: Commit**

```bash
git add web/monitor/public/db-ui.js web/monitor/public/mind.html web/monitor/public/mind.css web/monitor/server.mjs web/monitor/static-files.test.mjs web/monitor/db-ui.test.mjs
git commit -m "monitor: library view on the scene page, with bulk character entry"
```

---

### Task 6: 部署与主人试用（需要主人同意后才做）

这一步要 ssh 到服务器，执行前先问主人。

**Files:** 无代码改动。

- [ ] **Step 1: 全部测试再跑一遍**

Run: `python3 -B -m unittest discover -s tests -v && node --test web/monitor/*.test.mjs`
Expected: 全部 OK / pass

- [ ] **Step 2: 查仓库里没有真实名字**

先把服务器设置里出现过的名字逐行写进 `.private/names.txt`（不进仓库），再运行：
`git grep -nF -f .private/names.txt -- server web docs tests`
Expected: 没有输出

- [ ] **Step 3: 先看会复制什么，再部署 mind**

Run: `server/mind/deploy.sh --dry-run`，确认列出 `library.py`；然后 `server/mind/deploy.sh`
Expected: 单元重启后在跑；`hello` 库里出现五个新集合的索引（只读查 `index_information()`）

- [ ] **Step 4: 上线前后对照**：记下部署前一小时 `mind_runs` 条数和最新几分钟的情景；部署后一小时再看。由本功能引起的新增应为 0，情景页和台词记录照常。

- [ ] **Step 5: 主人按 spec 3.8"能试"逐条试**：加主人和一位同事的卡、贴几条性格、加两件事和一条"登山团体"并挂上、把工作背景抄成一条知识、改一条并看历史、两个页面同时改同一条看到 409。

- [ ] **Step 6: 按 spec 3.8"算失败"逐条核**：任抽 5 条直接查库对照页面；确认旧版本内容没变；确认原有集合和录音目录没有写入或删除；单次读取不超过 1 秒。把结果记进 `docs/session-handoff.md` 顶部。

---

## 不在本计划内

- 导入主人卡和人脸名提议、身世年表、会话查询加索引（第 2 期）。
- mind 改读人物库（第 3 期）。
- 每日回顾页、声纹标注的写入入口（第 4 期；本期只建 `voice_labels` 集合、索引和 `voice_label()` 校验）。
- 整理与融合、文本向量（第 5 期）。
- 任何模式的读写。
