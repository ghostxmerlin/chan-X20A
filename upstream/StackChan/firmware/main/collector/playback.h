/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <esp_http_server.h>
#include "reaction_sound.h"

namespace collector {
void playback_init();
// Caller must authorize and verify that microphone initialization has completed.
esp_err_t playback_handle(httpd_req_t* request);
// Nonblocking; keeps only the latest fresh cue behind an active remote cue,
// drops during HTTP playback. Caller checks audio readiness.
bool playback_reaction(RemoteReaction reaction);
// 0 selects a random local clip; 1..40 selects one. Busy requests never queue.
void playback_set_audio_ready();
esp_err_t playback_voice(int requested_id, bool from_asr, uint32_t* job_id = nullptr, int* selected_id = nullptr);
void playback_asr_command(uint8_t id, bool has_value);
// Speaker volume (10..100) for every following playback; saved in NVS by the caller's task.
int playback_volume();
esp_err_t playback_set_volume(int volume, const char* source);
void playback_json(ArduinoJson::JsonObject target);
bool playback_output_enabled();
esp_err_t playback_hearing(int clip,uint32_t session);
void playback_set_hearing_session(uint32_t session);
bool playback_self_sounding();
// Nonblocking atomic state: accepted job, including delay/loading, through cleanup.
bool playback_busy();
}
