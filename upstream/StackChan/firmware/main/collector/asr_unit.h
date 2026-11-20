/* SPDX-License-Identifier: MIT */
#pragma once
#include "asr_protocol.h"
#include "asr_motion.h"
#include <ArduinoJson.hpp>
#include <cstdint>


namespace collector {
// The owner's StickS3 voice module (Unit ASR wire protocol) on Port C since 2026-11-20: UART2 at 115200, RX GPIO18
// (the Stick's TX), TX GPIO17 (its control frames; nothing sent yet). The reader task and its UART interrupt live
// on core 1.
void asr_init();
// Latest frame as (sequence << 8 | id); sequence 0 means nothing received yet.
// Lock-free, safe from the LVGL timer. Every frame is also logged under
// "AsrUnit", so it shows on the robot's own log page.
uint32_t asr_latest();
void asr_json(ArduinoJson::JsonObject out,uint32_t after=0);
// 0.5.10 robot command line: one yaw word (front, back, tracking on / off) or, with word None, an absolute slot
// (-2..2 without 0) through the ASR task's own plan: the same admission, busy state and two-stage turns as a spoken
// word. Blocks the caller up to wait_ms (the ASR task takes it within ~100 ms); {nullptr, "timeout"} otherwise.
// One caller at a time (others wait for it). Never call it from the ASR task.
AsrMotionOutcome asr_yaw_request(AsrMotionWord word, int slot, uint32_t wait_ms);
void asr_info_json(ArduinoJson::JsonObject out);
}
