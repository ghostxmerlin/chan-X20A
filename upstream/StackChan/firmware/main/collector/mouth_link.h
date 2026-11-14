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
// to mind over /mouth; digits 0 / 9 are sent to mind (a.to_mind). a.out.toast is what the screen shows.
Applied mode_apply(const char* source, modes::rules::Input input, const modes::rules::Value& value = {});
// hello is speaking: a /mouth stream is playing (tts.start until done or stopped). Takes the audio lock briefly.
bool speaking();
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
}
