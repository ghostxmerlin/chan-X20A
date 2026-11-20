/* SPDX-License-Identifier: MIT */
#pragma once
#include "mouth_modes.h"
#include "mouth_stream.h"
#include <ArduinoJson.hpp>
#include <string>

namespace collector::mouth {
// /mouth (collector-0.4.77): hello's speaker downlink. One task (PSRAM stack) connects to the ear's
// host and port at "/mouth" with the hearing token whenever the backend is configured; the ear
// relays it to soul-tts. Opus packets go into a PSRAM ring; the player in playback.cpp plays them.
void init(const std::string& device_id, const std::string& boot_id);
void json(ArduinoJson::JsonObject out);

// Player side (playback.cpp). false: no stream to play right now.
struct PlayerView { uint32_t stream = 0; bool ended = false, stopped = false; size_t buffered = 0; uint32_t played_frames = 0; };
bool player_view(PlayerView& v);
// Next packet of `stream` (for the decoder); false when empty, stopped or the stream changed.
bool player_pop(uint32_t stream, uint8_t* out, size_t& n);
// One frame of `stream` reached the speaker (played_ms for the server's flow control).
void player_played(uint32_t stream);
constexpr size_t kPacketMax = 768;
// Queue a report for the server; Done / Stopped / Error also finish the stream.
void player_report(const Report& r);

// hello modes step 1b (collector-0.4.92): hello owns the mode (mouth_modes.h, mode_rules.h). Safe from any
// task (ASR, the LVGL timer, httpd, main); none of these blocks on the network or the audio path, none writes NVS.
// One input from `source` (asr, touch, page): runs the rules now; a change, or an input with effects, is reported
// to mind over /mouth; digits 0 / 9 and 1 (and crowd's double tap = 1, 0.4.98) are sent to mind (a.to_mind).
// a.out.toast is what the screen shows.
Applied mode_apply(const char* source, modes::rules::Input input, const modes::rules::Value& value = {});
// 0.4.96 (owner 2026-11-15: 0 = all paid off, 9 = all on, also without a computer): every digit 0 / 9 that
// mode_apply takes, from any source, calls hook(digit == 9) after the mode lock, online or not. The hook must only
// queue (it runs on the caller's task: ASR, the screen, httpd); collector.cpp sets it once at boot.
void set_senses_hook(void (*hook)(bool on));
// 0.4.98 (owner 2026-11-15): crowd's tap ("look") holds its mode_report up to 0.8 s; mode_apply calls hook(token)
// after the mode lock (the hook must only queue: collector.cpp hands it to m12_look) and mode_look_done(token),
// from any task, lets that report go at once. A later input, the link dropping or the 0.8 s also let it go.
void set_look_hook(void (*hook)(uint32_t token));
void mode_look_done(uint32_t token);
// hello is speaking: a /mouth stream is playing (tts.start until done or stopped). Takes the audio lock briefly.
bool speaking();
// The voice module heard the robot's name (since 2026-11-20 the StickS3's "providence"; 0.5.06 the Unit ASR's "hello"):
// an independent, strong "talking to me". The stream playing now stops at once (hello may interrupt), and
// {"type":"asr_wake","word":"providence","n":..,"interrupted":..} goes up on /mouth. ASR task only; lock-free except
// the short player lock.
void asr_wake(const char* word);
uint32_t asr_wakes();                  // how many so far (the screen shows 在呢 for each new one)
// Main loop: closes settings after 5 minutes without input.
void mode_tick();
// A copy of the mode state (state, look, online, latest toast and mode_result) taken under the mode lock.
// false (and a default view: talk) only when the session could not be allocated at boot.
bool mode_view(ModeView& out);
// Telemetry / info "mode": mode, dir, host, return_to, seq, label, by, since_ms, online, counters.
void mode_json(ArduinoJson::JsonObject out);
// Changes whenever the view changes (mode, online/offline, toast, mode_result). Lock free: poll it each screen
// tick and call mode_view() only when it moved.
uint32_t mode_version();
// 0.5.00 视觉爆发 indicator (burst_indicator.h): the ear's latest {"type":"burst"} frame, or the link drop, as one packed
// word (burst::unpack). Lock free, any task; the LVGL timer polls it every pass (burst::Tracker).
uint32_t burst_word();
}
