# Providence, Docking only: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** hello becomes Providence with one mode (Docking), the owner's StickS3 voice module on Port C, the M12 on
Port B, and no factory voice commands, on the server, the page, the firmware and the Mac app.

**Architecture:** server first and tolerant of today's firmware (it still reports other modes and sends ASR digits),
then the firmware (one build: pins, Stick words, one mode), then the Mac app. The mode rules stay one table in
`server/mind/mode_table.py` + `modes.py`; `tests/gen_mode_vectors.py` turns them into `tests/data/mode_vectors.json`,
which the firmware (`tests/mode_rules_test.cpp`) and the Mac self-test replay.

**Tech Stack:** Python 3 (mind, ear; ear tests need numpy → server venv), Node (monitor, `node --test`), C++17
firmware on ESP-IDF 5.5.4 (host tests compiled by `tests/test_*.py` with `c++`), Swift (Mac app, `swift run
HelloSelfTest`).

**Spec:** `docs/superpowers/specs/2026-11-20-providence-docking-only-design.md`. Stick protocol:
`~/exia/stick/docs/voice-protocol.md`.

## Global Constraints

- Only mode: `crowd` (shown as Docking, label `DOCK`, eyes v14, pool lively; tap = `listen`, double tap = `quiet`).
- **Settings goes too**: it was an empty placeholder (`collector.cpp:2310` draws the default face for `page_settings`);
  the screen's Wi-Fi list and quick-settings page are screen pages, not modes, and stay.
- Pins: M12 = UART0, TX **GPIO9**, RX **GPIO8** (Port B). Stick = UART2, RX **GPIO18** (Stick TX), TX **GPIO17**
  (Stick RX, nothing sent yet), 115200 8N1.
- Stick words (only ids sent): `50` docking sequence, `51` o raiser, `52` inevitable, `53` providence, `55` say what
  again, `5A` inception, `5E` expelliarmus, `5F` expecto patronum. `53` = being called (`asr_wake`, word
  `providence`). The other seven: logged + in telemetry `asr.events`, no action. Every other id: logged as unknown.
- No factory-id handling anywhere (motion words, forward / backward, turn on / off, play / pause, open / close,
  digits, volume, wake ids `31` / `32` / `FF`).
- Name: Providence (神意高达). The ear's call words: providence / 普罗维登斯 / 神意 (replacing hello / Dynames).
- Firmware version `collector-0.5.19` (`upstream/StackChan/firmware/CMakeLists.txt:5`).
- Flashing and deploying the robot's stack wait for the owner; secrets never printed; kids' names never in the repo.

## Review Focus

1. Today's firmware (0.5.18) reports `talk` / `idle` / `settings` and sends ASR digits 0 / 9 after the server is
   deployed: mind must keep running, show Docking, refuse the digits (Task 1, Task 2 tests).
2. PROVIDENCE while hello is speaking: the firmware stops speech (existing `asr_wake` path), the ear opens a chat even
   in 点屏幕聊天 (Task 3 test).
3. An id in a gap (`54`, `56`–`59`, `5B`–`5D`, `60`–`65`) or a factory id arrives (a Stick reflashed with
   `factory_words_on`): logged as unknown, no mode input, no motion, no order (Task 6 test).
4. Only one of the two pin changes lands: the M12 goes offline (Task 5 test pins both and their uniqueness).
5. A page still open from before the deploy posts a mode change: mind answers a clear refusal, never a 500 (Task 1
   test).

---

### Task 1: mind — one mode (crowd), tolerant of old reports

**Files:**
- Modify: `server/mind/mode_table.py` (MODES → only `crowd`; `_IDS`; `_ON_HELLO` / PRESETS 4–8 → `空`)
- Modify: `server/mind/modes.py` (`transition`), `server/mind/mode_store.py` (`valid_state`, report handling)
- Modify: `tests/gen_mode_vectors.py` (states / inputs / `DIGIT_SELECT`), regenerate `tests/data/mode_vectors.json`
- Test: existing mind mode tests (`tests/test_modes*.py`, `tests/test_mode_store*.py`), new `tests/test_docking_only.py`

**Interfaces:**
- Produces: `mode_table.MODES == {'crowd': {...}}`; `modes.transition(state, input, value, *, source, speaking)`
  unchanged signature; new result code `'removed_mode'` (toast `DOCKING ONLY`) for a `select` of any other mode;
  `mode_store.valid_state(st)` true only for `{'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_docking_only.py`:
  - `test_only_crowd`: `list(mode_table.MODES) == ['crowd']`.
  - `test_boot_and_long_go_to_crowd`: `transition` of a crowd state with `boot` and with `long` → new mode
    `crowd`, effects `['stop_speech']`.
  - `test_select_other_mode_is_refused`: `select {'mode': 'talk'}` → code `removed_mode`, state unchanged.
  - `test_voice_words_do_nothing`: `forward`, `open`, `play` → code `no_action`, state unchanged.
  - `test_old_firmware_report_is_shown_as_docking`: a ModeStore fed a robot report `{'mode': 'talk', ...}` keeps
    running; its snapshot mode is `crowd` and the log line names `talk`.
  - `test_page_mode_post_is_refused_cleanly`: the mind API `POST /mode` with `{"mode": "chat"}` → 409 with code
    `removed_mode` (not 500).
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_docking_only -v` → FAIL (8 modes).
- [ ] **Step 3: Implement**: shrink the tables; in `transition` keep rule order (boot, long → crowd; crowd's tap /
  double tap; everything else `no_action`, `select` of a removed mode `removed_mode`); `valid_state` accepts only the
  crowd state; a report with a mode not in MODES is mirrored as crowd and logged once per mode name; digits 4–8 →
  `no_action`.
- [ ] **Step 4: Regenerate vectors**: `python3 -B tests/gen_mode_vectors.py` (update `DIGIT_SELECT` to `{5: crowd}` =
  already, others empty; states = the one crowd state).
- [ ] **Step 5: Run** `python3 -B -m unittest discover -s tests -p "test_mod*.py"` and `tests.test_docking_only` → PASS
  (the firmware replay `test_firmware_rules_replay_every_vector` is expected to FAIL until Task 7; mark it
  `expectedFailure` with a comment naming Task 7, removed in Task 7).
- [ ] **Step 6: Commit** `mind: Docking is the only mode (old firmware's reports shown as Docking)`.

### Task 2: server — the factory ASR commands are off

**Files:**
- Modify: `server/mind/api.py` / `server/mind/mode_store.py` (digit / mode inputs with `source == 'asr'`)
- Modify: `server/ear/rt_interrupt.py` (`on_order`)
- Test: `tests/test_docking_only.py` (add), `tests/test_realtime_link.py` (add)

**Interfaces:**
- Produces: mind answers a mode input from source `asr` with code `asr_off` (toast `ASR OFF`), nothing applied;
  `RealtimeHub.on_order(link, m)` does nothing (no window change, one log line).

- [ ] **Step 1: Failing tests**: `test_asr_digit_9_is_refused` (preset 9 with `source='asr'` → `asr_off`, switches
  unchanged); `test_asr_order_changes_nothing` (`hub.on_order(link, {"word": "left"})` leaves `_called_until`,
  `_called_text` as they were).
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement** the refusal in the one place mind routes mode inputs (before presets); `on_order` →
  log only.
- [ ] **Step 4: Run** the two files → PASS (ear tests on the server venv: copy to a scratch dir,
  `~/soul-spike/.venv/bin/python -m unittest tests.test_realtime_link`).
- [ ] **Step 5: Commit** `server: the factory voice commands are off (asr digits / orders ignored)`.

### Task 3: ear — the name is Providence; PROVIDENCE opens a chat; no keyword service

**Files:**
- Modify: `server/ear/rt_base.py` (`BODY`, `CALL_RE`, `CALLED_SOURCES`), `server/ear/ask_api.py:206`,
  `server/ear/rt_interrupt.py` (`called`), `server/ear/ear_server.py` (kws: env only)
- Modify: `tests/snapshots/ear.json` (regenerate), `config/persona/luye.md` if it names the body
- Test: `tests/test_realtime_link.py`, `tests/test_refactor_snapshots.py`

**Interfaces:**
- Produces: `CALL_RE` matches a sentence starting with `providence` / `普罗维登斯` / `神意` (any case), not `hello` /
  `Dynames`; `called("asr")` opens a chat when `cfg.answer_all` (as `touch` does); the ear reads the keyword URL only
  from `SOUL_KWS_URL` (`kws.url` ignored).

- [ ] **Step 1: Failing tests**: `test_call_words_are_providence` (`CALL_RE.match("Providence，过来")` truthy,
  `CALL_RE.match("hello，你好")` None, `CALL_RE.match("普罗维登斯你好")` truthy); `test_providence_opens_a_chat_in_tap_mode`
  (`cfg.answer_all = True`, `hub.on_wake(link, {"word": "providence", "interrupted": True})` → `hub.chat_on()` True);
  `test_kws_url_file_is_ignored` (ear startup helper with a `kws.url` present and no env → no keyword link).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; `BODY`: "你住在主人的桌面机器人 Providence（神意高达）里…有人叫 Providence 就是在叫你。"
- [ ] **Step 4: Regenerate snapshots** on the server venv: `python tests/test_refactor_snapshots.py --write`; check the
  diff touches only call / persona lines.
- [ ] **Step 5: Run** the ear tests on the server venv → PASS; full suite failure list = HEAD's.
- [ ] **Step 6: Commit** `ear: the robot is Providence; its name word opens a chat; the keyword service is not used`.

### Task 4: monitor — no mode bar, no other modes' state

**Files:**
- Modify: `web/monitor/public/index.html`, `web/monitor/public/app.js`, `web/monitor/public/mode-ui.js` (delete or
  reduce to the Docking badge), `web/monitor/control.mjs` (`mode` action), `web/monitor/public/switches-ui.js` (presets
  4–8 if shown)
- Test: `web/monitor/mode-ui.test.mjs`, `web/monitor/control.test.mjs`, `web/monitor/static-files.test.mjs`

**Interfaces:**
- Consumes: Task 1's mind snapshot (mode always `crowd`).
- Produces: the page shows a fixed 「Docking」 badge (and an "old firmware: <mode>" note when the robot's telemetry
  reports another mode); `control.validate({action:'mode', ...})` → 400 `只有 Docking 模式`.

- [ ] **Step 1: Failing tests**: the page has no mode buttons; the badge reads Docking; a telemetry mode `talk` shows
  the old-firmware note; `mode` control → 400.
- [ ] **Step 2: Run** `cd web/monitor && node --test *.test.mjs` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS (all, skipped 2).
- [ ] **Step 5: Commit** `monitor: Docking only (no mode bar)`.

### Task 5: firmware — swap the ports

**Files:**
- Modify: `upstream/StackChan/firmware/main/collector/m12_unit.cpp:21-23,331` (GPIO9 TX / GPIO8 RX, Port B text)
- Modify: `upstream/StackChan/firmware/main/collector/asr_unit.cpp:28-32,91-…`, `asr_unit.h:10-12` (UART2 RX GPIO18,
  TX GPIO17, Port C text; `uart_set_pin(kUart, kTx, kRx, …)`)
- Test: new `tests/test_ports.py`

- [ ] **Step 1: Failing test** `tests/test_ports.py`: parse the two `.cpp` files and assert M12 `kUart == UART_NUM_0`,
  `kTx == GPIO_NUM_9`, `kRx == GPIO_NUM_8`; ASR `kUart == UART_NUM_2`, `kRx == GPIO_NUM_18`, `kTx == GPIO_NUM_17`;
  and that no other file under `collector/` names `GPIO_NUM_8`, `GPIO_NUM_9`, `GPIO_NUM_17`, `GPIO_NUM_18`.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_ports -v` → FAIL.
- [ ] **Step 3: Implement** (comments: "Port B since 2026-11-20, swapped with the voice module").
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `firmware: M12 on Port B, the voice module on Port C (owner swapped the plugs)`.

### Task 6: firmware — the Stick's words, nothing of the factory vocabulary

**Files:**
- Modify: `collector/asr_protocol.h` (`asr_word` → the eight Stick words only; new `AsrAction asr_action(const AsrFrame&)`)
- Modify: `collector/asr_unit.cpp:170-215` (dispatch on `asr_action` only)
- Delete: `collector/asr_mode_words.h`, `tests/asr_mode_words_test.cpp`, `tests/test_asr_mode_words.py`
- Modify: `collector/asr_motion.h` (drop the id → word mapping; keep `AsrYawPlan` and `AsrMotionWord` for the 0.5.10
  command line), `tests/asr_motion_test.cpp`; `playback` / `hearing` / `interaction` `asr_command` callers removed
- Test: `tests/asr_protocol_test.cpp` (+ its `tests/test_asr_protocol.py` runner)

**Interfaces:**
- Produces: `enum class AsrAction : uint8_t { Ignore, Call, Heard };` `inline AsrAction asr_action(const AsrFrame& f)`
  (`53` without value → Call; the other seven → Heard; anything else → Ignore); `asr_word(0x53) == "providence"`.

- [ ] **Step 1: Failing tests** in `asr_protocol_test.cpp`: `asr_action({0x53})==Call`; each of `50 51 52 55 5A 5E 5F`
  → Heard with the spec's names; `54`, `56`, `5B`, `60`, `01`, `20`, `29`, `32`, `FF`, `40` → Ignore and
  `asr_word == nullptr`; a frame with a value → Ignore.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_asr_protocol -v` → FAIL.
- [ ] **Step 3: Implement**: Call → `mouth::asr_wake("providence")`; Heard / Ignore → history + log only (telemetry
  `asr.events` carries the word, `(unknown)` for Ignore); no `asr_order`, no mode input, no motion, no volume.
- [ ] **Step 4: Run** every firmware host test (`python3 -B -m unittest discover -s tests -p "test_asr*.py"` and
  `tests.test_ports`) → PASS.
- [ ] **Step 5: Commit** `firmware: the StickS3 voice module (Providence calls, seven words reported, factory words gone)`.

### Task 7: firmware — Docking is the only mode; build 0.5.19

**Files:**
- Modify: `collector/mode_rules.h` (one `Mode::Crowd`; rules as Task 1), `collector/mode_wire.h`, `collector/eye_mode.h`,
  `collector/m12_words.h` (mode words drawn), `collector/collector.cpp` / `mouth_link.cpp` callers of removed modes,
  `upstream/StackChan/firmware/CMakeLists.txt:5` → `collector-0.5.19`
- Modify: firmware host tests that name removed modes (`mode_wire_test.cpp`, `mouth_modes_test.cpp`,
  `remote_modes_test.cpp`, `eye_mode_test.cpp`); remove Task 1's `expectedFailure`
- Test: `tests/test_mode_rules.py` (vector replay)

- [ ] **Step 1: Run** `python3 -B -m unittest tests.test_mode_rules -v` without the expectedFailure → FAIL (8 modes).
- [ ] **Step 2: Implement** the one-mode table and fix every caller so the firmware compiles.
- [ ] **Step 3: Run** all firmware host tests (`python3 -B -m unittest discover -s tests -p "test_*.py" -k mode` plus
  the asr / port tests) → PASS.
- [ ] **Step 4: Build** per `docs/build.md` (ESP-IDF 5.5.4 in `.tools`) → `build/…/stackchan.bin` with
  `collector-0.5.19`; note the image size against the OTA partition.
- [ ] **Step 5: Commit** `collector-0.5.19: Providence — Docking only, StickS3 voice module, ports swapped`.
- [ ] **Step 6: Stop.** Report to the owner; flashing waits for their go.

### Task 8: Mac app — match the robot

**Files:**
- Modify: `mac/HelloMac/Sources/HelloCore/ModeRules.swift` (one mode; replays the new vectors),
  `mac/HelloMac/Sources/HelloCore/AsrWords.swift` (the eight Stick words; providence = wake), callers in `Hello.swift`,
  `FaceView.swift`, README
- Test: `mac/HelloMac/Sources/HelloSelfTest/main.swift`

- [ ] **Step 1: Failing self-test**: vectors replay with the regenerated file; `AsrWords.kind("providence") == .wake`;
  `AsrWords.kind("left")`, `"nine"` → nil.
- [ ] **Step 2: Run** `cd mac/HelloMac && swift run HelloSelfTest` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the self-test and `swift build && .build/debug/HelloMac --selftest` → PASS; `./build-app.sh`.
- [ ] **Step 5: Commit** `mac: Providence, Docking only, the Stick's words`.

### Task 9 (gated): ship

- [ ] Mac stack: `tools/mac/deploy_stack.sh` (owner already uses it; tell them first).
- [ ] Robot stack (ear + mind deploy scripts) and the firmware flash: **only on the owner's go**; push the branch when
  they say so.
