/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <cstdint>
// The body's 12 RGB LEDs on the StackChan base (IO expander pin 13; reachable with the hat off, owner 2026-11-20,
// collector-0.5.21). Set from the robot command line (cmd light) and the page (/v1/control target light). RAM only:
// off at every boot.
namespace collector::light {
bool set(uint8_t r, uint8_t g, uint8_t b);      // false: the base IO expander is not there (nothing changed)
void json(ArduinoJson::JsonObject out);         // {ready, r, g, b, on, changed_us, sets}
}
