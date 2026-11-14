/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace collector {
// Unit ASR words that are mode inputs (hello modes step 1, owner 2026-11-14; design v2 §1.2 / §5.1).
// The latest keyword table wins and replaces the old local uses: open/close no longer toggle
// tracking, start/stop no longer toggle hearing, next no longer plays a random clip. The robot only
// names the input; mind decides what it means in the current mode (the robot sends it over /mouth).
//   0x07 forward / 0x09 backward  enter / leave the talk show
//   0x16 play / 0x17 pause        conversation
//   0x10 open / 0x11 close        settings
//   0x12 start / 0x13 stop        event-driven
//   0x1B next / 0x1A previous     per mode (talk show: next / previous line)
//   0x20..0x29 zero..nine         "digit", *digit = 0..9 (presets)
// Everything else returns nullptr and stays local, unchanged: the direction words (up/down
// tracking, left/right 45, turn left/right 90, front/back), turn on/off (M12 view), the lights,
// ok, hi ASR, hello, volume, firmware version. Frames that carry a value are never words.
// *digit (when not null) is set to the digit, or -1 for every other result.
inline const char* asr_mode_input(uint8_t id, bool has_value, int* digit)
{
    if (digit) *digit = -1;
    if (has_value) return nullptr;
    switch (id) {
    case 0x07: return "forward";
    case 0x09: return "backward";
    case 0x16: return "play";
    case 0x17: return "pause";
    case 0x10: return "open";
    case 0x11: return "close";
    case 0x12: return "start";
    case 0x13: return "stop";
    case 0x1B: return "next";
    case 0x1A: return "previous";
    default: break;
    }
    if (id >= 0x20 && id <= 0x29) {
        if (digit) *digit = id - 0x20;
        return "digit";
    }
    return nullptr;
}
} // namespace collector
