# Continuous Hearing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 配置声纹，ASR start/stop 连续监听，按后端情绪标签播放本体台词和眼睛。

**Architecture:** collector 唯一采集任务向独立 hearing 模块提交有界 PCM 块，直接通过 WebSocket 送 sherpa-onnx。网页复用已有鉴权 POST 和遥测，后端负责声纹持久化和推理，本体决定回应。

**Tech Stack:** ESP-IDF 5.5.4 / C++ / existing Opus+WebSocket / Python sherpa-onnx / Node monitor.

**Spec:** [design](../specs/2026-11-08-continuous-hearing-design.md)

## Global Constraints

- 所有人均可触发，声纹只显示身份；开机待机；stop 失效旧回应；ASR open 等待800ms不变。
- 原分区/依赖/遥控仅左右/M12手动不变；token不显示、不进入argv；现有声纹默认保留。
- 24kHz mono Opus60ms/40kbps/VBR/complexity0/AUDIO，无DTX；SEA1，后端16k解码。

## Review Focus

- 延迟推理跨 cancel/new enrollment：旧任务不可写库（任务1）。
- 自身声响在推理结束前消失：仍标记原语音，禁止回应/录入（任务1、2）。
- stop 在连接或播放期间到达：不重连复活，不影响手动音频（任务2）。
- HTTP密钥不出现在子进程参数或状态，保存空密钥保留原值（任务3）。
- 输入丢帧/断线：不能拼接跨缺口句子或播放旧情绪（任务1、2）。

## Task 1 — Backend protocol and enrollment

**Files:** server/ear/hearing_protocol.py (new validation/SEA1/enrollment persistence), ear_server.py, deploy.sh, tests/test_ear_protocol.py.
**Interfaces:** parse_audio_packet(data, framing) -> packet; authenticate(path, header, token) -> bool; enrollment request carries request_id/name/count=5/replace; events carry request_id, progress/done/error/cancelled.

- [ ] Write/run failing protocol/auth/atomic enrollment/cancellation/self-overlap tests.
- [ ] Implement bounded input validation, 24k Opus declaration, SEA1 sequence/timestamp and gap VAD reset, bounded inference tasks; capture immutable self/enrollment generation at slicing time.
- [ ] Preserve legacy Mac PCM16 and token compatibility. Enrollment has120s timeout, count5, valid1–12s speech, cancellation/disconnect barrier and atomic save/backup.
- [ ] Run Python tests and syntax checks; commit independently testable backend.

## Task 2 — Device direct hearing and owned playback

**Files:** collector/hearing.{h,cpp}, hearing_policy.h (new); collector.cpp, asr_unit.cpp, playback.{h,cpp}, CMakeLists.txt; tests/ host policy harness.
**Interfaces:** hearing::init(device_id,boot_id); feed_audio(interleaved,frames,seq,t_us); asr_command(id,has_value); json(JsonObject); handle(httpd_req_t*); playback_hearing(clip,session); playback_cancel_hearing().

- [ ] Write/run failing host tests for ASR IDs0x12/0x13, nonneutral emotion pools, everyone policy, stale/dedup/cooldown, stop ownership.
- [ ] Implement idle/listening/enrolling state, NVS config, bounded capture queue, Opus+SEA1, strict backend parsing, fresh-session reconnect and immediate cancellation.
- [ ] Extend playback successful-PCM eye cue to hearing origin; cancel only hearing-owned playback on stop. Include self output tail state in capture metadata.
- [ ] Add authenticated boot-bound POST /v1/hearing, redacted telemetry/info, ASR dispatch; build fixed toolchain, confirm image fits app slot.
- [ ] Run host regressions; commit.

## Task 3 — Web configuration and enrollment

**Files:** web/monitor/hearing.mjs, public/hearing-ui.js (new); control.mjs, core.mjs, server.mjs, public/index.html/app.js/style.css; Node tests.
**Interfaces:** action hearing_config/listen/enroll/cancel; explicit boot field, strict fields/types; new hearing telemetry redacted. Curl body goes over fd3, credentials over stdin headers.

- [ ] Write/run failing control validation, secret transport and UI state tests.
- [ ] Add configuration, password blank-preserve, identity/status, start/stop, five-phrase enrollment and explicit replacement/cancel UI.
- [ ] Add local existing-backend configuration import without returning token to browser. No automatic config/write/listen on page load.
- [ ] Run Node tests; document configuration and readiness/physical limits; commit.

## Task 4 — Integration, review, delivery

- [ ] Preserve remote port/start arguments and remote modifications; back up code before deploy; deploy tested backend only after compare.
- [ ] Verify actual backend hello, 24k SEA1/Opus acceptance, disconnect/cancel, recording metadata and no secret disclosure. No user enrollment without actual speech.
- [ ] Run all relevant Python/Node/firmware checks and independent final code review; fix findings.
- [ ] Update handoff/version/artifact evidence, commit and push branch. If device remains deferred, clearly separate server deployed from firmware built but not flashed.
