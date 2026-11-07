/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <esp_http_server.h>
#include "reaction_sound.h"

namespace collector {
// Caller must authorize and verify that microphone initialization has completed.
esp_err_t playback_handle(httpd_req_t* request);
// Nonblocking; keeps only the latest fresh cue behind an active remote cue,
// drops during HTTP playback. Caller checks audio readiness.
bool playback_reaction(RemoteReaction reaction);
// 0 selects a random local clip; 1..40 selects one. Busy requests never queue.
void playback_set_audio_ready();
esp_err_t playback_voice(int requested_id, bool from_asr, uint32_t* job_id = nullptr, int* selected_id = nullptr);
void playback_asr_command(uint8_t id, bool has_value);
void playback_json(ArduinoJson::JsonObject target);
bool playback_output_enabled();
}
