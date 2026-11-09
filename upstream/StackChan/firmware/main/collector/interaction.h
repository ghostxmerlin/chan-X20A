/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <esp_err.h>
#include <esp_http_server.h>
#include <cstdint>
namespace collector::interaction {
struct Indicators { bool tracking = false, recognition = false; };
// Read before acquiring the LVGL display lock.
Indicators indicators();
esp_err_t set_mode(const char* mode,bool enabled);
void asr_command(uint8_t id,bool has_value);
void tick(int64_t now);
// An explicit legacy preview request takes control of M12 without automatic
// modes selecting it back. Does not issue the replacement M12 command itself.
void release_for_manual_m12();
void json(ArduinoJson::JsonObject out);
esp_err_t handle(httpd_req_t* request);
}
