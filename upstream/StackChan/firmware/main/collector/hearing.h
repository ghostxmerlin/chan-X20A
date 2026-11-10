/* SPDX-License-Identifier: MIT */
#pragma once
#include "hearing_intent.h"
#include <ArduinoJson.hpp>
#include <esp_http_server.h>
#include <cstdint>
#include <cstddef>
#include <string>
namespace collector::hearing {
void init(const std::string& device_id,const std::string& boot_id);
// The configured ear backend (URL and token); false while none is set. Other uplinks reuse it.
bool backend(std::string& url,std::string& token);
void feed_audio(const int16_t* interleaved,size_t frames,uint32_t seq,int64_t timestamp_us);
ListeningIntent listening_intent();
// Explicit requests revoke any automatic owner, including idempotent starts.
bool set_listening(bool enabled);
bool toggle_listening();
OwnerStart start_for_owner(uint32_t expected_revision,uint32_t& owner_revision);
void stop_for_owner(uint32_t owner_revision);
void asr_command(uint8_t id,bool has_value);
// events: include hearing events with seq > events_after (speech, sound, gap, error, link).
void json(ArduinoJson::JsonObject target,bool events=false,uint32_t events_after=0);
// Body of POST /v1/control {"target":"hearing",op,...}; auth and boot checked by the caller.
esp_err_t handle(httpd_req_t* request,ArduinoJson::JsonObject body);
}
