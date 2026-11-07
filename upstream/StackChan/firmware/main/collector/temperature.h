/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
namespace collector {
void temperature_init();
void temperature_json(ArduinoJson::JsonObject out);
}
