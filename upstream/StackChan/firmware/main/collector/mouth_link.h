/* SPDX-License-Identifier: MIT */
#pragma once
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
}
