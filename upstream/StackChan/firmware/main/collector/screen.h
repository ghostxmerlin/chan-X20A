/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include <cstdint>
#include "emotion_state.h"

namespace collector {
struct ScreenStatus {
    EmotionView emotion;
    const char* version = "";
    const char* ip = "";
    const char* token = "";
    const char* motor = "";
    unsigned channel = 0;
    uint32_t frame = 0;
    bool audio_ready = false;
    bool audio_streaming = false;
    bool wifi_connected = false;
    bool camera_working = false;
    float camera_fps = 0;
    bool remote_ready = false;
    bool remote_recent = false;
    int32_t remote_last_age_ms = -1;
    uint32_t remote_received = 0;
    bool power_valid = false;
    int battery_percent = 0;
    bool charging = false;
    bool imu_valid = false;
    float accel[3] = {}; // m/s^2, matching the telemetry contract
    float gyro[3] = {}; // deg/s
    bool yaw_valid = false;
    bool pitch_valid = false;
    float yaw_deg = 0;
    float pitch_deg = 0;
};
enum class ScreenReaction { None, Heart, Reject };
// All calls run under the display's LVGL lock (or on the LVGL timer task).
void screen_create(lv_obj_t* parent);
// RAM-only display gate: keep the current page, stop redraws and touch
// navigation while off, and restore its latest state and prior backlight.
// The LVGL service timer must keep running so a remote press can wake it.
void screen_set_enabled(bool enabled, uint32_t now_ms);
bool screen_enabled();
void screen_update(const ScreenStatus& status);
// Keep the latest actual log snapshot; the visible log follows its newest line.
// Text is copied (maximum 4095 bytes); callers retain ownership of their buffer.
void screen_update_log(const char* text, uint32_t sequence);
// Advance only the text ring, at most 10 redraws/s. Hidden page pauses it.
void screen_tick(uint32_t now_ms);
// Valid fresh samples only. Coordinates are required while down for swipes.
// A release without coordinates uses the last down sample. Summary hold opens
// pairing at 900 ms; other pages ignore holds. A hold never repeats navigation.
void screen_touch(bool down, uint32_t now_ms, int x = -1, int y = -1);
// Reactions are transient home-only feedback; a new event restarts its 1000 ms
// at UI acceptance. Input freshness is checked separately by the caller.
void screen_show_reaction(ScreenReaction reaction, uint32_t now_ms);
// M12 face view: a full-screen page with one box per face (screen coordinates,
// 320x240). Showing it leaves any other page; hiding it returns home. A tap on it
// also returns home, which callers see as screen_showing_faces() == false.
constexpr size_t kScreenMaxFaces = 6;
struct ScreenFace { int16_t x = 0, y = 0, w = 0, h = 0; };
void screen_show_faces(bool on, uint32_t now_ms);
// Boxes beyond kScreenMaxFaces are ignored; status is one short line.
void screen_update_faces(const ScreenFace* faces, size_t count, const char* status);
bool screen_showing_faces();
// Discard an interrupted/invalid touch sample without generating navigation.
void screen_cancel_touch();
bool screen_showing_status();
bool screen_showing_log();
bool screen_showing_pairing();
}
