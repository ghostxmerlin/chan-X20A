/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include <cstdint>

namespace collector {
enum ScreenFunction : uint8_t {
    ScreenFunctionTracking = 1 << 0,
    ScreenFunctionListening = 1 << 1,
    ScreenFunctionSpeaking = 1 << 2,
    // The fourth (green) segment follows the screen's actual visible expression.
    ScreenFunctionRecognition = 1 << 4,
};
struct ScreenStatus {
    const char* version = "";
    const char* ip = "";
    const char* ssid = ""; // Connected network name; shown instead of a bare "online".
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
    // Sample before the LVGL lock; rendering does not call service accessors.
    uint8_t function_flags = 0;
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
// On-device Wi-Fi setup: a scan list page (swipe left from pairing) and a
// digits-only password keypad. The collector owns the radio and NVS; the screen
// only shows results and hands back one request at a time.
struct ScreenWifiEntry { char ssid[33] = ""; int rssi = -100; bool secure = true; bool saved = false; };
constexpr size_t kScreenWifiEntries = 8;
constexpr size_t kScreenWifiPasswordMax = 32;
void screen_update_wifi_scan(const ScreenWifiEntry* entries, size_t count, bool scanning);
void screen_update_wifi_status(const char* text);
// Each returns true once per user action and clears the request.
bool screen_wifi_take_scan_request();
bool screen_wifi_take_credentials(char* ssid, size_t ssid_size, char* password, size_t password_size);
bool screen_showing_wifi();
// hello modes step 1b. Home-page taps are the mode's: the classifier (touch_gesture.h) waits 300 ms for a
// double tap; each call returns the latest one once. A left swipe on home opens the summary (was: a tap).
enum class ScreenHomeGesture : uint8_t { None, Tap, DoubleTap };
ScreenHomeGesture screen_take_home_gesture();
// Corner badge, every page: the mode's ASCII label on its color; offline = grey with " OFF".
void screen_update_mode(const char* label, uint32_t color, bool online);
// A short ASCII line at the bottom for 2 s (the mode's toast). Empty text shows nothing.
void screen_show_toast(const char* text, uint32_t now_ms);
// 0.4.93 chat stage dots (chat_stage.h), home page top left: visible only in chat; lit = -1 none, 0 / 1 / 2 =
// listen / think / speak in `color`; grey = all three grey (skip). Created when shown, deleted when hidden.
void screen_update_chat_dots(bool visible, int lit, bool grey, uint32_t color);
}
