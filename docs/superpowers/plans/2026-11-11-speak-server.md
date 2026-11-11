# hello 开口第一步：服务器端（soul-tts、ear 转发 /mouth、角色、/say、页面）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 页面上点角色按钮（吐槽 / 鼓励），mind 根据当前情景写出一句话，交给服务器上的 soul-tts 流式合成，再经 ear 转发的 `/mouth` 推给设备。设备暂时用 `voice/tts_service/soul_tts/devsim.py` 冒充，所以这一步不改固件。

**Architecture:**
- `voice/tts_service`：代码以 voice 里那份为准，部署时复制到服务器的 `~/soul-tts/`，作为临时单元 `soul-tts` 运行（`player=stream`）。
- ear 新增 `mouth_link.py`：校验设备令牌，把 `/mouth` 双向转发到 127.0.0.1:52751。
- mind 新增 `speak.py`，以及接口 `GET /roles`、`POST /say`、`POST /say/stop`、`GET /utterances`。
- 角色文件放在 `config/roles/*.json`，由 mind 的部署一起复制过去。
- `selfvoice` 额外读取 hello 最近 1 小时说过的话（`hello.utterances`）。

**Spec:** `docs/superpowers/specs/2026-11-11-speak-design.md`

## Global Constraints

- 用户定的音色：
  - 吐槽：`tang`，汤师爷复刻音色；
  - 鼓励：`anhuan`，`longanhuan_v3.1`，语气指令"请讲上海话。用温柔、体贴的语气说。"；
  - 两者都用 `qwen-audio-3.1-tts-flash`，流式。
- TTS 价格：输入每百万 token 1.5 元，输出每百万 token 12 元（用户提供）。
- 一句话：用 luna，`reasoning_effort: low`，严格 json_schema `{"text"}`；不超过 30 字。去掉引号、括号和舞台说明；如果超出长度，就在最后一个标点处截断。
- soul-tts 只监听服务器本机：接口 127.0.0.1:52750，mouth 127.0.0.1:52751。设备令牌和接口令牌都用 ear 的 `SOUL_TOKEN_FILE`。
- 没有设备连着 `/mouth` 时，`/say` 返回 409 "hello 的嘴没连上"，**不合成、不花钱**。
- 部署前，`voice/tts_service` 要先审核；Critical 和 Important 的问题先在 voice 里修好再部署。
- 部署要用户明确说"部署"。

## Review Focus

1. **设备没连上**：期望 409，不调用 luna，也不调用 TTS。
2. **连点两次**：期望第二句打断第一句（`interrupt: true`），并且两句都记录下来。
3. **转发时设备断了，或 soul-tts 重启了**：期望两边都关闭，ear 的其他连接不受影响。
4. **luna 写得太长，或带了引号、括号、舞台说明**：期望清理后再说出来。
5. **hello 说的话被自己听回来**：期望 selfvoice 认出是她，不写进纪要。

---

### Task 1: voices.json 增加 anhuan（在 voice 里改）
- 增加 `anhuan`：`voice: longanhuan_v3.1`，`model: qwen-audio-3.1-tts-flash`，`rate: 1.0`，`instruction: "请讲上海话。用温柔、体贴的语气说。"`。
- 跑一遍 voice 的单元测试，确认通过。

### Task 2: ear 转发 /mouth
**Files:** Create `server/ear/mouth_link.py`；Modify `server/ear/ear_server.py`（handler 按路径分发：`/mouth` 交给它，和 `/vision` 一样）、`server/ear/deploy.sh`（FILES 里加上它）；Test `tests/test_mouth_link.py`（在 venv-ear 里用真的 websockets，起本机端口测试）
- `async def relay(client_ws, upstream_url, token, connect=websockets.connect) -> str`：连上游时带 `Authorization: Bearer <token>`；两个方向都原样转发文本和二进制；任何一边关闭，另一边也关；返回关闭原因。上游连不上时，以关闭码 1011 关掉设备这边。
- 测试：
  - `test_frames_flow_both_ways`
  - `test_upstream_down_closes_the_device_side`
  - `test_device_close_closes_upstream`
  - `test_mouth_path_requires_the_token`（未鉴权的一律在 handler 里 4401，复用现有逻辑）

### Task 3: soul-tts 部署脚本
**Files:** Create `server/tts/deploy.sh`、`server/tts/remote_setup.sh`
- 复制 `../voice/tts_service/soul_tts/`（不带 tests 和 `__pycache__`）到服务器的 `~/soul-tts/soul_tts/`。
- 建立或重启临时单元 `soul-tts`，环境变量：`SOUL_TTS_PLAYER=stream`、`SOUL_TTS_TOKEN_FILE`、`SOUL_TTS_DEVICE_TOKEN_FILE`（这两个都用 ear 的令牌文件）、`SOUL_TTS_ENV_FILE=~/aidea/agent/.env`、`SOUL_TTS_LISTEN=127.0.0.1:52750`、`SOUL_TTS_MOUTH_LISTEN=127.0.0.1:52751`；用 `~/soul-spike/.venv/bin/python -m soul_tts serve` 启动。
- 等到日志出现 `soul-tts listening` 再结束。`bash -n` 检查通过。

### Task 4: 角色文件和 mind 的 /say
**Files:** Create `config/roles/roast.json`、`config/roles/comfort.json`、`server/mind/speak.py`；Modify `server/mind/api.py`、`server/mind/selfvoice.py`（`set_spoken(texts)`、`refresh_spoken(db, now)`）、`server/mind/runner.py` 和 `notes_runner.py`（每次 tick 调用 `refresh_spoken`）、`server/mind/deploy.sh`（复制 `config/roles/` 到 `~/soul-mind/roles/`）；Test `tests/test_mind_speak.py`
- `load_roles(dir) -> dict`：校验必填字段（`id`、`button`、`persona`、`rules`、`voice`）。
- `context(db, store, s, now_ms) -> dict`：环境说明、此刻情景（最新 live 场景里的"在干什么"和摘要，5 分钟以内的才用）、最新 eye 结果（5 分钟内）、当前对话（正在进行或结束不到 5 分钟）、最近 2 分钟的原话（最多 10 句）、hello 最近说的 5 句。
- `clean(text) -> str`：去掉引号、括号、舞台说明；超过 30 字时在最后一个标点处截断。
- `say(env, store, s, roles, role_id, tts, now_ms) -> dict`：
  - 如果 `tts.health()` 显示设备没连上，返回 409；
  - 否则调用 luna，清理文字，`tts.say(text, voice=…, instruction=…, rate=…, interrupt=True)`；
  - 记一条 `hello.utterances`（`role`、`text`、`tts_id`、`at`、`context_summary`）和一条 `mind_runs`（`step: "say"`）。
- 测试：
  - `test_roles_load_and_validate`
  - `test_context_uses_only_fresh_scene_eye_and_conversation`
  - `test_clean_strips_quotes_brackets_and_cuts_at_punctuation`
  - `test_say_refuses_without_a_device_and_calls_nothing`
  - `test_say_calls_luna_then_tts_and_logs`
  - `test_spoken_text_is_recognised_as_hello`（selfvoice）
- 接口：`GET /roles` 返回 `[{id, button, name}]`；`POST /say {role}`；`POST /say/stop`；`GET /utterances?from&to`。代理放行这些路径。

### Task 5: 页面
**Files:** Modify `web/monitor/public/mind.html`、`mind-ui.js`；Test `web/monitor/mind.test.mjs`
- "让 hello 说一句"区域：
  - 每个角色一个按钮，再加一个"别说了"按钮；
  - 显示最近 5 句：时间、角色、文字、状态；
  - 显示今天说了几句、花了多少钱。
- 测试：
  - `say panel html`（不写 `style=`）
  - `role buttons from /roles`

### Task 6: 审核修复、部署、联调（要用户说"部署"）
- 根据审核结果在 voice 里修 tts_service。
- 部署 ear、soul-tts、mind，重启观察台。
- 在 Mac 上运行 `devsim`，连 `ws://<服务器>:52732/mouth`，带设备令牌。页面上点"吐槽"，期望 devsim 收到 `tts.start`、Opus 包和 `tts.end`，然后回报 `playing` 和 `done`。
- 记录这几项：生成一句话的耗时、TTS 首包时间、总耗时、费用。
