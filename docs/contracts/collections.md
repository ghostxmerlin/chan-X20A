# 数据约定：集合和公告板文档（2026-11-19）

重构第 4.1 步（docs/plans/2026-11-19-server-layers.md）。Mongo 库 hello 里每个集合谁写、谁读、字段的意思。「用到它的模块」由 tools/arch/code_map.py 从代码扫出（只认得 db["x"] 这种写法；耳朵通过 ear/hello_db.py 的方法写）。改字段前先改这里。

## 公告板文档（hello.switches，几个进程共用的状态）

| _id | 谁写 | 谁读 | 内容 |
|---|---|---|---|
| tingwu / eye / notes / realtime / realtime_tools / billing | mind（网页开关、预设）、耳朵（闭嘴 → realtime 关） | 耳朵、mind 各进程 | {on, at, by} 开关 |
| mouth | mind | 耳朵 | {mode: off / clips / tts} |
| rt_voice | mind（set_voice） | 耳朵（启动时） | 千问的声音 |
| pause | mind（pause.Controller） | 耳朵、mind 各进程（pause.active / budget_stop） | 暂停和每天上限：on, reasons, budget {spent_cny, cap_cny, cap_usd …} |
| presence_status | 耳朵（presence.py） | mind（pause） | face_at, voice_at, owner_at, faces, at |
| realtime_status | 耳朵（realtime_link._write_status） | 观察台、mind | Docking 会话状态、quiet / low / role / debug |
| rt_modes_status | 耳朵（realtime_link._persist_modes） | 耳朵（重启时 restore_modes）、mind（debug_mode.state） | 安静 / 少插话 / 角色 / 调试：{until_ms（null = 直到说退出）, open, sticky, since_ms} |
| eye_status / notes_status / tingwu_status … | 各自的进程 | 观察台 | 「实际在做什么」 |

## 集合

| 集合 | 写 | 用到它的模块 | 字段要点 |
|---|---|---|---|
| body | ear/hello_db.py（body_sense.py） |  | 身体感觉：kind ev（每个事件）/ sum（摘要，10 秒或状态变了一条）；raw 50 Hz 数据不进库（在录音目录的 body/ 下） |
| chat_turns |  | mind/chat.py、mind/library.py、mind/memory_extract.py、mind/tool_impls.py |  |
| conversation_versions |  | mind/notes.py、mind/notes_runner.py、mind/records.py、mind/speech_context.py |  |
| conversations |  | mind/api.py、mind/chat.py、mind/library.py、mind/memory_extract.py、mind/notes.py、mind/notes_runner.py、mind/realtime.py、mind/records.py、mind/semantic.py、mind/speak.py、mind/speech_context.py、mind/store.py、mind/tool_impls.py |  |
| doc_chunks |  | mind/semantic.py | 资料块：_id 项目:条目号、project、title、text、status、source、hash |
| eye_minutes |  | mind/api.py、mind/eye.py、mind/eye_runner.py、mind/library.py、mind/place.py、mind/realtime.py、mind/records.py、mind/speak.py、mind/speech_context.py、mind/tool_impls.py |  |
| eye_state |  | mind/eye.py、mind/eye_runner.py |  |
| frames | ear/hello_db.py | mind/api.py、mind/eye.py |  |
| library_jobs |  | mind/memory_extract.py |  |
| memory_items |  | mind/library.py |  |
| memory_recall |  | mind/library.py、mind/memory_extract.py、mind/recall.py、mind/semantic.py |  |
| memory_uses |  | mind/recall.py |  |
| mind_labels |  | mind/api.py、mind/place.py、mind/store.py |  |
| mind_memories |  | mind/library.py、mind/memory.py |  |
| mind_memory_evidence |  | mind/memory.py |  |
| mind_memory_jobs |  | mind/api.py、mind/memory.py、mind/memory_worker.py |  |
| mind_packages |  | mind/memory_worker.py、mind/store.py |  |
| mind_runs |  | mind/api.py、mind/memory_extract.py、mind/say_prefetch.py、mind/say_round.py、mind/store.py |  |
| mind_scenes |  | mind/library.py、mind/realtime.py、mind/store.py |  |
| mind_segments |  | mind/library.py、mind/memory_extract.py、mind/semantic.py、mind/store.py |  |
| mind_settings |  | mind/settings_store.py、mind/speech_context.py、mind/store.py |  |
| mind_state |  | mind/store.py |  |
| mode |  | mind/mode_store.py |  |
| mode_log |  | mind/api.py、mind/billing.py、mind/mode_store.py |  |
| notes_state |  | mind/notes.py、mind/notes_runner.py |  |
| owner_signals |  | mind/library.py、mind/memory_extract.py、mind/owner_signals.py、mind/store.py |  |
| people_cards |  | mind/daily.py、mind/library.py |  |
| place |  | mind/place.py |  |
| proposals |  | mind/proposals.py |  |
| realtime_turns | ear/hello_db.py | mind/tool_impls.py |  |
| reminders |  | mind/daily.py |  |
| say_rounds |  | mind/api.py、mind/memory_extract.py、mind/owner_signals.py、mind/say_round.py、mind/speak.py、mind/store.py |  |
| sessions | ear/hello_db.py | mind/records.py |  |
| sounds | ear/hello_db.py（sound_doc） | mind/daily.py、mind/tool_impls.py |  |
| speech | ear/hello_db.py（speech_doc） | mind/api.py、mind/debug_mode.py、mind/library.py、mind/memory_extract.py、mind/tool_impls.py | 一句话一行：t（毫秒，说话开始）、text、speaker {band known/unsure/unknown, name（只在 known 时）, best, score}、**self = {sounding, moving}：hello 当时在不在出声、在不在动（不是「这句是 hello 说的」；判断 hello 自己的话用 mind/selfvoice.is_hello(text, self.sounding)）**、emotion、session |
| tingwu | ear/hello_db.py |  |  |
| utterances |  | mind/api.py、mind/chat.py、mind/memory_extract.py、mind/owner_signals.py、mind/selfvoice.py、mind/speak.py、mind/speech_context.py、mind/tool_impls.py |  |
| vision | ear/hello_db.py（vision） | mind/api.py、mind/daily.py、mind/eye.py、mind/realtime.py、mind/records.py、mind/tool_impls.py |  |
| vision_looks | ear/hello_db.py | mind/api.py、mind/eye.py、mind/tool_impls.py |  |
| voice_labels |  | mind/library.py |  |
| watches |  | mind/daily.py |  |
