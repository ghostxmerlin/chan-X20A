/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include <cstdint>

namespace collector {
struct ScreenStatus {
    const char* version;
    const char* ip;
    const char* token;
    const char* motor;
    unsigned channel;
    uint32_t frame;
    bool audio_ready;
    bool audio_streaming;
};
// All calls run under the display's LVGL lock (or on the LVGL timer task).
void screen_create(lv_obj_t* parent);
void screen_update(const ScreenStatus& status);
// Advance only the text ring, at most 12.5 redraws/s. Hidden page pauses it.
void screen_tick(uint32_t now_ms);
// Valid fresh samples only. Release toggles once; holding never repeats.
void screen_touch(bool down, uint32_t now_ms);
bool screen_showing_status();
}
