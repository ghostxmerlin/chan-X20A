/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include <cstdint>
#include "top_row.h"

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
    // 0.4.94 quick settings page: the station's signal and the speaker volume (10..100; -1 unknown).
    bool rssi_valid = false;
    int rssi = 0;
    int volume = -1;
    // 0.4.98 home top row (top_row.h): M12 vision, sampled from m12_snapshot() before the LVGL lock (held there).
    // Wi-Fi bars come from wifi_connected / ip / rssi above, the battery from power_valid / battery_percent / charging.
    top::Vision vision;
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
// 0.4.95 Chinese words: the SD CJK font (ui_font.h; 16 px and 24 px variants, nullptr = not loaded). The caller
// passes them again whenever ui_font::generation() changes. Every page word then shows in Chinese (ui_text_zh.h);
// without the 16 px font, its ASCII stand-in (ui::text) in the ASCII font, so the screen works with no font on SD.
// The summary title row shows "状态摘要" and "模式 <名>" ("chan-X20A", "TALK" without). Open pages are rebuilt.
// The corner badge and toasts stay ASCII.
void screen_set_cjk_font(const lv_font_t* small, const lv_font_t* large);
// A short ASCII line at the bottom for 2 s (the mode's toast). Empty text shows nothing.
void screen_show_toast(const char* text, uint32_t now_ms);
// 0.5.03 M12 words (m12_words.h): busy = a toast is on screen now; a word replaces nothing that is showing
bool screen_toast_busy();
void screen_show_word(const char* text, uint32_t now_ms);
// 0.4.93 chat stage dots (chat_stage.h), home page top row, left: visible only in chat / crowd; lit = -1 none,
// 0 / 1 / 2 = listen / think / speak in `color`; grey = all three grey (skip).
// 0.4.98: drawn as three narrow blocks as tall as the mode badge (same semantics and timing) by the home top row, one
// drawn object (no objects created or deleted). The rest of the row (M12 vision, the recognised name, Wi-Fi, battery)
// follows screen_update(); the row is invalidated only when what it shows changes.
void screen_update_chat_dots(bool visible, int lit, bool grey, uint32_t color, bool aside = false);
// The part of the recognised name the top row draws ("" none; "known" when it cannot be drawn). For host previews.
const char* screen_top_name();
// 0.5.00 视觉爆发 indicator (burst_indicator.h): the home top row's burst group between the name and the Wi-Fi bars,
// an eye, the look count and a 2 px time bar (left_s / total_s). on = false hides it. Call every LVGL tick with the
// tracker's view; the row is invalidated only when the count or the bar's filled pixels change.
void screen_update_burst(bool on, uint32_t looks, int left_s, int total_s);

// 0.4.94 global swipes (ui_pages.h): on home in every mode, down = quick settings, up = mode picker, left = summary;
// each page closes with the opposite swipe, its "< Back" title, or (quick / picker) 30 s without a touch. Those two are
// built when they open and deleted when they close. The screen only draws and hands requests back, one at a time; the
// caller runs them (mode_apply for picks and billing, the main loop for volume / hearing, which take service locks).
// The current mode for the picker's highlight (modes::rules::Mode / Dir as numbers).
void screen_update_mode_state(uint8_t mode, uint8_t dir);
// mind's latest answer to a digit 0 / 9 (digit -1 = none or not known) and when it came (ms, the screen clock).
void screen_update_billing(int digit, const char* code, uint32_t at_ms);
// A picker tile was tapped (index into ui::kTileModes). Once per tap.
bool screen_take_mode_pick(size_t& tile);
// Quick page requests, -1 = none: a volume 10..100; billing 9 (all on) / 0 (all off); hearing 1 (on) / 0 (off).
int screen_take_volume_request();
int screen_take_billing_request();
int screen_take_hearing_request();
bool screen_showing_quick();
bool screen_showing_picker();
}
