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

// hello modes step 1 (collector-0.4.92). mind owns the mode; the ear pushes its snapshot down /mouth and
// answers each input with a mode_result. Safe from any task (ASR, screen/main); none of these blocks on the
// network or the audio path, and none writes NVS.
// Queue a mode_input (value < 0 = null, else e.g. the digit 0..9); sent on the /mouth task with age_ms since
// this call. Returns the input's seq (unique per boot, matches mode_result.seq), 0 = not queued: /mouth is not
// connected with the ear's hello (show OFFLINE), or a word is not a wire word. A full queue (8) drops its oldest.
uint32_t mode_link_send_input(const char* source, const char* input, int value = -1);
// input "select" with value {mode, dir?} (dir null or "" = none): the mode picker, page or quick settings.
uint32_t mode_link_send_select(const char* mode, const char* dir = nullptr, const char* source = "select");
// A copy of the current mode state (snapshot, online, latest mode_result) taken under the mode lock.
// false (and an empty view) only when the session could not be allocated at boot.
bool mode_view(ModeView& out);
// Changes whenever the view changes (snapshot applied, online/offline, new mode_result). Lock free: poll it
// each screen tick and call mode_view() only when it moved.
uint32_t mode_version();
}
