# 图库（按时间管理、彻底删除机器人照片）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 8787 上的「图库」页按日 / 小时翻看机器人身体摄像头照片，维护状态下彻底删除选中的照片和由它产生的全部文字。

**Architecture:** 删除逻辑放在新模块 `server/mind/gallery.py`（只依赖 db、recordings 根目录、spool 目录）；mind API 加 `/gallery/*` 路由；暂停机制加一个「维护中」原因；8787 加转发白名单和 `gallery.html / gallery-ui.js / gallery.css`。

**Tech Stack:** Python 3.11 + pymongo（mind），Node 24 原生 http（8787），无前端框架。

**Spec:** `docs/superpowers/specs/2026-11-20-photo-library-design.md`

## Global Constraints

- 只管机器人那套（库 `hello`）；Mac 那台身体（`hello_mac`）不做。
- 删除只在维护状态下可以执行：机器人没连着 + 暂停原因里有 `maintenance`。
- 没有回收站、没有撤销；只删主人选中的，不加任何自动或定时删除。
- 衍生文字一并删：看一眼、分钟描述、mind_packages、情景（mind_scenes）、重叠的段落总结（mind_segments）、mind_runs、运行日志的行 + 正文、log-spool 文件里的副本。
- 删除这件事写一条运行日志 `gallery.delete`，只记时间范围和各处条数，不记路径和描述原文。
- 页面用中文称呼（图库、看一眼、分钟描述、情景、段落总结），表名只用灰色小字；样式沿用 `web/monitor/public/log.css` 的变量。
- 8787 的 CSP 是 `img-src 'self' blob:`：图片走 JSON 里的 base64 → Blob → objectURL，不用 data: URL。
- mind 的 POST 正文上限 16 KiB：一次 plan / delete 最多 500 个 id，页面分批。
- 测试在本机跑：`.tools/ear-test-venv/bin/python -m unittest …`、`node --test …`；不在服务器上跑测试。
- 不用 systemctl 停服务（都是 systemd-run 临时服务，一停就没了）。

## Review Focus

1. 维护期间机器人又连上了：删除接口必须拒绝（409），页面显示原因；已经开始的一批照常做完。
2. 主人的任何操作（开关、页面按钮）都不能撤掉 `maintenance` 原因：note_owner 只撤 `away`。测试放在 Task 1。
3. 共享的日志正文（同一份 system 提示被很多行引用）不能被删：只删没有剩余引用的正文。测试放在 Task 3。
4. frames.path 指到 recordings 之外（`../x.jpg`）：不读、不删，计入 `files_skipped`。测试放在 Task 2。
5. 结束维护后 mind 会补算 48 小时内缺的情景：补出来的情景里没有画面（照片、分钟描述都没了），这是预期行为，在 Task 5 的真机核对里确认。

---

### Task 1: 暂停加「维护中」原因

**Files:**
- Modify: `server/mind/pause.py`（REASON_ZH、active()、decide()、Controller、view()、文件头说明）
- Modify: `server/ear/presence.py:130-150`（Pause.reasons）
- Test: `tests/test_mind_pause.py`、`tests/test_ear_presence.py`

**Interfaces:**
- Produces: `pause.Controller.set_maintenance(on: bool, *, now_ms=None) -> dict`（返回新的 pause 文档）；`pause.maintenance(db) -> bool`；pause 文档新字段 `maintenance_since: int | None`；`reasons` 可含 `'maintenance'`；`REASON_ZH['maintenance'] = '维护中'`；`view()` 多返回 `maintenance: bool`。

- [ ] **Step 1: 写失败的测试**（`tests/test_mind_pause.py` 新类 `MaintenanceTest`）
  - `test_set_maintenance_pauses_and_owner_input_does_not_lift_it`：`set_maintenance(True)` 后 `paused()` 为真、`reasons` 含 `'maintenance'`、`view()['maintenance']` 为真；随后 `note_owner('switch')` 之后仍含 `'maintenance'`；`set_maintenance(False)` 后不再含。
  - `test_maintenance_survives_a_restart`：写入文档后新建 Controller，`tick()` 后仍含 `'maintenance'`，`maintenance_since` 不变。
  - `test_maintenance_with_away_lists_both`：没人 10 分钟 + 维护 → `reasons == ['maintenance', 'away']`，`why == 'maintenance'`，`switch.pause` 事件的 sum 是 `暂停：维护中、没人`。
  - `test_module_reader`：`pause.active(doc)` 对 `{'on': True, 'reasons': ['maintenance']}` 为真；`pause.maintenance(db)` 读库为真。
  - `tests/test_ear_presence.py` 加 `test_maintenance_holds_and_presence_does_not_lift_it`：`Pause.reasons()` 对含 `maintenance` 的文档返回含 `'maintenance'`，有人脸 / 熟人声音时也不去掉。
- [ ] **Step 2: 跑测试确认失败**：`.tools/ear-test-venv/bin/python -m unittest tests/test_mind_pause.py tests/test_ear_presence.py` → 新测试 FAIL。
- [ ] **Step 3: 实现**
  - `decide()` 从 `prev['maintenance_since']` 延续维护；reasons 顺序 `maintenance, budget, away`。
  - `Controller.set_maintenance` 在锁内改 `self._doc['maintenance_since']`（开：now；关：None），然后 `_tick(now, force_write=True)`。
  - `active()` 和 ear 的 `Pause.reasons()` 都把 `maintenance` 算作暂停。
- [ ] **Step 4: 跑测试确认通过**：同上命令 → OK；再跑 `tests/test_mind_*.py` 和 `tests/test_ear_*.py` 中涉及 pause 的测试不回退（`.tools/ear-test-venv/bin/python -m unittest discover -s tests -p "test_*pause*.py"` 和 `-p "test_ear_presence.py"`）。
- [ ] **Step 5: Commit** `git commit -m "mind: a 维护中 pause reason (gallery maintenance)"`

### Task 2: gallery.py 的读取部分

**Files:**
- Create: `server/mind/gallery.py`
- Test: `tests/test_mind_gallery.py`（用 `mind_fixtures.FakeDB` 和临时 recordings 目录）

**Interfaces:**
- Produces（全部是模块函数，`db` 为 hello 库，`tz` 为 `ZoneInfo`，时间为 epoch ms）：
  - `days(db, tz) -> list[{"day": "YYYY-MM-DD", "n": int}]`，新的在前。
  - `hours(db, day: str, tz) -> list[{"hour": 0..23, "n": int}]`，只列有照片的小时。
  - `frames(db, lo: int, hi: int) -> list[{"id", "at", "session", "snap": bool, "look": bool, "eye": bool, "scene": bool}]`，按 at 升序；`hi - lo` 最多 3600_000，否则 `ValueError`。
  - `images(db, root: str, ids: list[str]) -> dict[id, b64 | None]`，最多 60 个，否则 `ValueError`。
  - `detail(db, root, id) -> {"id", "at", "session", "snap", "jpeg_b64", "looks": [{"at", "text"}], "eye": {"minute", "text"} | None, "scene": {"minute", "text"} | None}`；不存在时返回 None。
  - `oid(s: str)`：24 位十六进制 → `bson.ObjectId`，否则原样返回（FakeDB 测试用字符串 id）。
  - `read_file(root, rel) -> bytes | None`：真实路径校验，同 api.py `_read_jpeg`（把 `_read_jpeg` 改成调用它）。

- [ ] **Step 1: 写失败的测试**
  - `test_days_and_hours_in_beijing_time`：在北京时间 11-19 23:59 和 11-20 00:01 各放一张 → `days` 为 `[{"day":"2026-11-20","n":1},{"day":"2026-11-19","n":1}]`；`hours(db,"2026-11-20",tz)` 为 `[{"hour":0,"n":1}]`。
  - `test_frames_flags`：一张被 vision_looks 引用（同 path）、所在分钟有 eye_minutes 和 mind_scenes → `look/eye/scene` 都为真；另一张都为假；超过 1 小时的范围抛 `ValueError`。
  - `test_images_reads_only_under_root`：path `../outside.jpg` → None；正常 → base64；61 个 id → `ValueError`。
  - `test_detail_has_look_text_eye_and_scene`：返回看一眼的 text、分钟描述文字、情景文字。
- [ ] **Step 2: 跑测试确认失败**：`.tools/ear-test-venv/bin/python -m unittest tests/test_mind_gallery.py` → ImportError / FAIL。
- [ ] **Step 3: 实现**：按日 / 小时统计用 `find({"at": …}, {"at": 1})` 在 Python 里分桶（FakeDB 的 aggregate 不全）；分钟 = `at // 60000 * 60000`；分钟描述文字取 `eye_minutes.result` 转字符串，情景文字取 `mind_scenes.scene`（live lane，最新 version）。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `git commit -m "mind: gallery reads (days, hours, frames, images, detail)"`

### Task 3: gallery.py 的删除清单和执行

**Files:**
- Modify: `server/mind/gallery.py`
- Test: `tests/test_mind_gallery.py`（新类 `DeleteTest`）

**Interfaces:**
- Consumes: Task 2 的 `oid`、`read_file` 的路径校验。
- Produces:
  - `plan(db, ids: list[str], *, spool_dir: str | None) -> dict`：`{"fp": str, "frames": n, "files": n, "looks": n, "eye_minutes": n, "packages": n, "scenes": n, "segments": [{"start", "end", "title"}], "runs": n, "log_rows": n, "from": ms, "to": ms}`；最多 500 个 id，否则 `ValueError`；只读。
  - `delete(db, root: str, ids, fp: str, *, spool_dir, emit) -> dict`：重新算清单，`fp` 不同抛 `gallery.Stale`；返回各处实际删除条数，外加 `files_skipped`、`bodies`、`bodies_redacted`、`spool_redacted`。
  - `class Stale(Exception)`。

清单怎么算（`fp` = 各集合命中的 `_id` 排序后 sha256 的前 16 位）：
- frames：`_id ∈ ids`；paths = 它们的 path；minutes = 它们的分钟。
- looks：`vision_looks.path ∈ paths`。
- eye_minutes：`minute ∈ minutes` 或 `frames.path ∈ paths`。
- packages / scenes / runs：`mind_packages` / `mind_scenes` / `mind_runs` 的 `minute ∈ minutes`（所有 lane）。
- segments：`start < m + 60000` 且（`end` 为空或 `end > m`），m 取 minutes 中任一个。
- log_rows（月表 `log_YYYYMM`，月份按北京时间，可能跨两个月）：`kind == "call.look"` 且 `ref.path ∈ paths`；`kind ∈ {"call.eye", "call.fusion"}` 且 `ref.minute ∈ minutes`；`kind == "call.segment"` 且 `ref.minute` 落在被删段落的 [start, end] 里。
- 片段（fragments）：被删的看一眼 text、分钟描述、情景、段落标题和正文，按 `[。！？；\n]` 切开，保留至少 8 个字的片段，去重。

执行顺序（先删文字，最后删文件）：
1. 日志正文：被删日志行引用的正文（`body` 里每个 sha），删掉后月表里已没有任何行再引用的，就删掉。
2. 用片段扫一遍剩下的正文（`logbody_*` 里 `text` 匹配片段的正则，每批最多 200 个片段），把片段替换成「［图库已删除］」，记入 `bodies_redacted`。
3. spool：`spool_dir` 下日期在 [第一天, 最后一天 + 1] 的 `*.jsonl`，按字节找片段的 UTF-8，原地用等长的 `*` 覆盖（`r+b`，文件大小不变，这样正在追加写的进程和 `.offset` 不受影响），记入 `spool_redacted`。
4. 依次删除日志行、segments、scenes、packages、runs、eye_minutes、looks、frames 记录。
5. 删除文件（只删真实路径在 `root/frames` 之内的，否则计入 `files_skipped`）；文件夹空了就删文件夹。
6. `emit("gallery.delete", by="page", sum="删除 N 张照片（HH:MM–HH:MM）", extra=各处条数和 from/to)`。

- [ ] **Step 1: 写失败的测试**（一个固定的小库：2 张要删、1 张留下且在同一分钟、1 张留下在别的分钟）
  - `test_plan_counts_everything_and_is_read_only`：数字逐项断言；调用前后各集合条数不变。
  - `test_delete_removes_every_copy`：删除后 frames、looks、eye_minutes、packages、scenes、runs、重叠的 segment、对应日志行都没了；不重叠的 segment、别的分钟的 scene 还在；文件不存在了，留下那张的文件还在。
  - `test_shared_body_is_kept_unique_body_is_deleted`：两行共用 system 正文、只有被删行引用 output 正文 → system 留下、output 删掉。
  - `test_fragments_are_redacted_in_other_bodies_and_spool`：一份 realtime context 正文里含看一眼回答 → 片段变成「［图库已删除］」；spool 文件里同一句被等长 `*` 覆盖，文件字节数不变，每行仍是合法 JSON。
  - `test_stale_plan_is_refused`：plan 之后再插入一条同 path 的 look → `delete` 抛 `Stale`，什么都没删。
  - `test_outside_path_is_skipped`：path 为 `../outside.jpg` → 记录删掉、外面的文件还在、`files_skipped == 1`。
  - `test_run_log_line_has_no_paths_or_text`：`emit` 收到的 sum / extra 里没有 `frames/` 也没有任何片段。
  - `test_more_than_500_ids_is_refused`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现** `plan` / `delete` / `Stale`。若 FakeDB 缺 `delete_one` 或 `update_many`，在 `tests/mind_fixtures.py` 里补上。
- [ ] **Step 4: 跑测试确认通过**：`.tools/ear-test-venv/bin/python -m unittest tests/test_mind_gallery.py` → OK。
- [ ] **Step 5: Commit** `git commit -m "mind: gallery plan and complete delete"`

### Task 4: mind API 路由

**Files:**
- Modify: `server/mind/api.py`（文件头说明、`get()`、`post()`、`do_POST` 白名单、`_read_jpeg` 改用 `gallery.read_file`）
- Modify: `server/mind/runner.py`（ctx 里放 `spool_dir`：`~/soul-spike/log-spool`，用 `SOUL_LOG_SPOOL` 环境变量或 runner 已有的 spool 路径）
- Test: `tests/test_mind_gallery_api.py`

**Interfaces:**
- Consumes: Task 1 的 `ctx["pause"].set_maintenance` / `.doc()`；Task 2、3 的 gallery 函数。
- Produces（全部要 Bearer token）：
  - `GET /gallery/days`、`GET /gallery/hours?day=YYYY-MM-DD`、`GET /gallery/frames?from&to`、`GET /gallery/images?ids=a,b,c`、`GET /gallery/frame?id=`
  - `GET /gallery/maintenance` → `{"on": bool, "since": ms|None, "robot_connected": bool, "can_delete": bool, "why_not": str|None}`
  - `POST /gallery/maintenance {on: bool}` → 同上
  - `POST /gallery/plan {ids}` → plan 结果
  - `POST /gallery/delete {ids, fp}` → delete 结果；不在维护状态或机器人连着 → 409 `{"code": "not_maintenance"}`；清单过期 → 409 `{"code": "stale"}`

机器人是否连着：`body` 表（ear 写的 body sense / 连接状态）或 `switches` 的 `presence_status`。实现者先查清 ear 写哪一条最近的连接状态，选一个 60 秒内会刷新的；查不到时按"连着"处理（不许删）。

- [ ] **Step 1: 写失败的测试**（照 `tests/test_mind_looks_api.py` 起 server，ctx 带 FakeDB 的 pause Controller、临时 recordings_root、spool_dir）
  - `test_reads`：days / hours / frames / images / frame 各返回 200 和预期结构；坏参数 400；没有的 id 404。
  - `test_delete_needs_maintenance`：不在维护 → 409 `not_maintenance`；`POST /gallery/maintenance {on:true}` 后、机器人未连着 → plan + delete 200；机器人连着 → 409。
  - `test_gallery_posts_do_not_count_as_owner_input`：`/gallery/*` 不在 `OWNER_POSTS` 里（一个 away 暂停在 plan 之后仍然在）。
  - `test_stale_is_409`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现路由**；`Stale` 映射为 409 `{"code": "stale"}`；接口文件头"POST 不写原始记录"一句改成"图库的删除接口例外，而且只做删除"。
- [ ] **Step 4: 跑测试确认通过**；再跑全部 mind 测试：`.tools/ear-test-venv/bin/python -m unittest discover -s tests -p "test_mind_*.py"` → OK。
- [ ] **Step 5: Commit** `git commit -m "mind: /gallery API (reads, maintenance, plan, delete)"`

### Task 5: 8787「图库」页

**Files:**
- Modify: `web/monitor/mind.mjs`（PATHS 加 `/gallery/days`、`/gallery/hours`、`/gallery/frames`、`/gallery/images`、`/gallery/frame`、`/gallery/maintenance`；POST_PATHS 加 `/gallery/maintenance`、`/gallery/plan`、`/gallery/delete`；PARAMS 加 `ids`，`ids` 只允许 `^[A-Za-z0-9,]{1,1600}$`；`day` 已有）
- Modify: `web/monitor/server.mjs`（GET / POST 白名单同上；assets 加 `/gallery.html`、`/gallery-ui.js`、`/gallery.css`）
- Modify: `web/monitor/public/index.html`（导航加「图库」链接，放在日志旁边）
- Create: `web/monitor/public/gallery.html`、`gallery-ui.js`、`gallery.css`
- Test: `web/monitor/gallery-ui.test.mjs`

**Interfaces:**
- Consumes: Task 4 的接口（经 `/api/mind/gallery/...`）。
- Produces（`gallery-ui.js` 导出的纯函数，供测试）：
  - `groupByMinute(frames) -> [{minute, items}]`
  - `selectRange(frames, fromId, toId) -> Set<id>`
  - `batches(ids, size = 500) -> string[][]`
  - `planSummary(plans) -> string`：把多批清单合成确认框文字，例如「将删除 37 张照片、2 条看一眼、5 分钟的分钟描述和情景、2 段段落总结（夜间调整 Hello、……），删除后无法恢复。」
  - `maintenanceHtml(m) -> string`：未维护 / 维护中 / 机器人还连着 三种状态的提示。

页面行为见设计文档「页面」一节：三栏（日期和小时 / 按分钟分组的缩略图 / 大图和详情）；缩略图用 IntersectionObserver 每次最多取 60 张；删除前先 plan 所有批次、合并显示确认框，确认后逐批 delete；任何一批返回 stale 就停下、重新 plan、再让主人确认一次。

- [ ] **Step 1: 写失败的测试**（照 `looks-ui.test.mjs` 的写法）：`groupByMinute`、`selectRange`、`batches(1001 个) → [500, 500, 1]`、`planSummary` 的完整文字、`maintenanceHtml` 三种状态、`MindProxy.check('/gallery/images?ids=a1,b2')` 通过、`ids=../x` 拒绝、`/gallery/delete` 在 POST 白名单里。
- [ ] **Step 2: 跑测试确认失败**：`node --test web/monitor/gallery-ui.test.mjs` → FAIL。
- [ ] **Step 3: 实现页面和白名单**。
- [ ] **Step 4: 跑测试确认通过**：`node --test web/monitor/*.test.mjs` → 全部 pass。
- [ ] **Step 5: Commit** `git commit -m "monitor: 图库 page"`

### Task 6: 部署和真机核对

- [ ] **Step 1:** 全量本地测试：`.tools/ear-test-venv/bin/python -m unittest discover -s tests -p "test_*.py"`（约 200 秒）和 `node --test web/monitor/*.test.mjs` 全部通过。
- [ ] **Step 2:** 部署：先 `server/mind/deploy.sh --dry-run` 看文件清单，再 `server/mind/deploy.sh`；ear 因 presence.py 改动跑 `server/ear/deploy.sh`；然后 `node web/monitor/service.mjs stop && node web/monitor/service.mjs start`。
- [ ] **Step 3:** 只读核对（真数据）：用无头 Chrome 打开 `http://127.0.0.1:8787/gallery.html`，截图确认日期、小时、缩略图、大图和详情都正常；对一个真实小时调 plan，确认数字合理。**不删真数据**，第一次删除留给主人。
- [ ] **Step 4:** 维护开关核对：`开始维护` → `GET /switches` 的 pause 显示「维护中」；`结束维护` → 撤掉；日志里有对应的 `switch.pause` / `switch.resume`。
- [ ] **Step 5:** 写交付说明给主人：怎么进入维护、怎么删、删的范围、删不到的东西（hello 说出口的话，即 realtime_turns / conversations 里对画面的转述；Mongo 托管方自己的备份）。
