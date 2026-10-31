/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <cstdint>
namespace collector {
void remote_init();
void remote_json(ArduinoJson::JsonObject target, uint32_t after, bool include_history);
bool remote_arm(bool enabled);
bool remote_enabled();
const char* remote_display_state();
}
