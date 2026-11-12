/* SPDX-License-Identifier: MIT */
#pragma once
#include "asr_protocol.h"
#include <ArduinoJson.hpp>
#include <cstdint>


namespace collector {
// M5Stack Unit ASR on StackChan's top black port (Port B): UART2 at 115200,
// receive only on GPIO8 (the unit's TX, confirmed on the dev robot); GPIO9 is
// never driven. The reader task and its UART interrupt live on core 1.
void asr_init();
// Latest frame as (sequence << 8 | id); sequence 0 means nothing received yet.
// Lock-free, safe from the LVGL timer. Every frame is also logged under
// "AsrUnit", so it shows on the robot's own log page.
uint32_t asr_latest();
void asr_json(ArduinoJson::JsonObject out,uint32_t after=0);
void asr_info_json(ArduinoJson::JsonObject out);
}
