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
void playback_json(ArduinoJson::JsonObject target);
bool playback_output_enabled();
}
