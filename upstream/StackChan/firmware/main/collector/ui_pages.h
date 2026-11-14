/* SPDX-License-Identifier: MIT */
#pragma once
#include "mode_rules.h"
#include "touch_gesture.h"
#include "volume_setting.h"
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
namespace collector::ui {
// Page navigation and hit testing for the screen (collector-0.4.94, owner 2026-11-14: "下滑 快捷设置页 音量
// 网络状态/修改入口 计费开关等；上滑 模式选择；左滑 状态摘要"). Pure logic, no LVGL: tests/ui_pages_test.cpp.
//
// Global gestures, the same in every mode, on the home page (taps and double taps stay the mode's):
//   swipe down = quick settings, swipe up = mode picker, swipe left = status summary.
// Every page closes back with the opposite swipe (quick: up, picker: down, summary: right) or its back control
// (the title bar), and goes home by itself after 30 s without a touch (the Wi-Fi list / keypad: 90 s, typing a
// password takes longer). The older pages keep their own moves: summary left = log, hold = pairing; log right =
// summary; pairing left = Wi-Fi list; a tap on summary / log goes home, on pairing to the summary.
enum class Page : uint8_t { Home, Summary, Log, Pairing, Faces, Wifi, WifiKey, Quick, Picker };

// Every word the new pages show, in one place. ASCII today: the screen font (collector_mono_15) has only 0x20-0x7E
// and the linked xiaozhi font_puhui_basic_20_4 lacks most of these characters (计费全上次选择返回...). When the CJK
// font (branch codex/ui-font-cjk, ui_font_cjk(size)) merges, switch these to Chinese and the labels' font with them.
// Mode tile labels stay the mode table's (mode_rules.h), like the corner badge.
namespace text {
inline constexpr const char* kBack = "< Back";
inline constexpr const char* kQuickTitle = "Quick";        // 快捷设置
inline constexpr const char* kPickerTitle = "Mode";        // 模式
inline constexpr const char* kVolume = "Volume";           // 音量
inline constexpr const char* kWifiButton = "Wi-Fi >";      // 改网络
inline constexpr const char* kWifiNamed = "Wi-Fi %s";      // 已连接 %s
inline constexpr const char* kWifiHidden = "(hidden)";
inline constexpr const char* kWifiOffline = "Wi-Fi offline";   // 没有网络
inline constexpr const char* kNotConnected = "not connected";
inline constexpr const char* kBilling = "Billing";         // 计费
inline constexpr const char* kAllOn = "ALL ON";            // 全开
inline constexpr const char* kAllOff = "ALL OFF";          // 全关
inline constexpr const char* kLastFormat = "last: %s%s%.18s  %s ago";   // 上次：全开 ...
inline constexpr const char* kHearing = "Hearing";         // 听觉
inline constexpr const char* kOn = "ON";
inline constexpr const char* kOff = "OFF";
inline constexpr const char* kHearingRefused = "HEARING: NO EAR SET";
inline constexpr const char* kSummaryHint = "Tap/>:home  <:log  Hold:pair";
}
using touch::Gesture;

constexpr uint32_t kIdleMs = 30000;
constexpr uint32_t kTypingIdleMs = 90000;
// 0 = never (home; the M12 face page follows its own request).
inline uint32_t idle_limit(Page p)
{
    switch (p) {
    case Page::Home: case Page::Faces: return 0;
    case Page::Wifi: case Page::WifiKey: return kTypingIdleMs;
    default: return kIdleMs;
    }
}

class Nav {
public:
    Page page() const { return page_; }
    // Where the Wi-Fi list goes back to: the page that opened it (pairing, or quick settings).
    Page wifi_back() const { return wifi_back_; }

    // The page a swipe leads to; page() when the swipe does nothing here. Show it with go().
    Page swipe(Gesture g) const
    {
        switch (page_) {
        case Page::Home:
            return g == Gesture::SwipeDown ? Page::Quick : g == Gesture::SwipeUp ? Page::Picker :
                   g == Gesture::SwipeLeft ? Page::Summary : page_;
        case Page::Quick: return g == Gesture::SwipeUp ? Page::Home : page_;
        case Page::Picker: return g == Gesture::SwipeDown ? Page::Home : page_;
        case Page::Summary: return g == Gesture::SwipeLeft ? Page::Log : g == Gesture::SwipeRight ? Page::Home : page_;
        case Page::Log: return g == Gesture::SwipeRight ? Page::Summary : page_;
        case Page::Pairing: return g == Gesture::SwipeLeft ? Page::Wifi : g == Gesture::SwipeRight ? Page::Summary : page_;
        case Page::Wifi: return g == Gesture::SwipeRight ? wifi_back_ : page_;
        default: return page_;
        }
    }
    // The back control (title bar): quick / picker -> home; Wi-Fi list -> its opener; keypad -> the list.
    Page back() const
    {
        switch (page_) {
        case Page::Wifi: return wifi_back_;
        case Page::WifiKey: return Page::Wifi;
        case Page::Pairing: return Page::Summary;
        default: return Page::Home;
        }
    }
    void go(Page p, uint32_t now_ms)
    {
        if (p == Page::Wifi && page_ != Page::Wifi && page_ != Page::WifiKey)
            wifi_back_ = page_ == Page::Quick ? Page::Quick : Page::Pairing;
        page_ = p;
        touched_ms_ = now_ms;
    }
    // Any finger on the screen (down samples) keeps the page open.
    void touched(uint32_t now_ms) { touched_ms_ = now_ms; }
    // The page has been left alone for its idle limit: the caller goes home.
    bool idle(uint32_t now_ms) const
    {
        const uint32_t limit = idle_limit(page_);
        return limit && now_ms - touched_ms_ >= limit;
    }

private:
    Page page_ = Page::Home;
    Page wifi_back_ = Page::Pairing;
    uint32_t touched_ms_ = 0;
};

// A touch stroke on a page other than home (screen.cpp's own numbers, as before): swipe = main axis >= 55 px,
// main > 2 x cross, cross never more than 55 px off, held 30-1000 ms; tap = held 30-700 ms, never more than
// 18 px off. Returns None for anything else.
inline Gesture stroke(uint32_t held_ms, int dx, int dy, int max_dx, int max_dy)
{
    const int adx = dx < 0 ? -dx : dx, ady = dy < 0 ? -dy : dy;
    if (held_ms >= 30 && held_ms <= 1000) {
        if (adx >= 55 && adx > ady * 2 && max_dy <= 55) return dx < 0 ? Gesture::SwipeLeft : Gesture::SwipeRight;
        if (ady >= 55 && ady > adx * 2 && max_dx <= 55) return dy < 0 ? Gesture::SwipeUp : Gesture::SwipeDown;
    }
    if (held_ms >= 30 && held_ms <= 700 && max_dx <= 18 && max_dy <= 18) return Gesture::Tap;
    return Gesture::None;
}

struct Rect {
    int x, y, w, h;
    bool hit(int px, int py) const { return px >= x && px < x + w && py >= y && py < y + h; }
};
constexpr int kTitleH = 28;
constexpr Rect kBack{0, 0, 140, kTitleH};    // "< Back" (the top-right corner is the mode badge's)

// ---- Mode picker: 3 x 3 tiles, the eight modes with subtitle twice (mode_table labels / colours). ----
struct Tile { modes::rules::Mode mode; modes::rules::Dir dir; };
constexpr size_t kTiles = 9;
inline constexpr Tile kTileModes[kTiles] = {
    {modes::rules::Mode::Talk, modes::rules::Dir::None}, {modes::rules::Mode::Chat, modes::rules::Dir::None},
    {modes::rules::Mode::Settings, modes::rules::Dir::None}, {modes::rules::Mode::Event, modes::rules::Dir::None},
    {modes::rules::Mode::Subtitle, modes::rules::Dir::ZhEn}, {modes::rules::Mode::Subtitle, modes::rules::Dir::EnZh},
    {modes::rules::Mode::Crowd, modes::rules::Dir::None}, {modes::rules::Mode::Companion, modes::rules::Dir::None},
    {modes::rules::Mode::Idle, modes::rules::Dir::None}};
constexpr int kTileX[3] = {8, 112, 216}, kTileY[3] = {34, 100, 166}, kTileW = 96, kTileH = 60;
inline Rect tile_rect(size_t i) { return Rect{kTileX[i % 3], kTileY[i / 3], kTileW, kTileH}; }
// The tile under (x, y), or -1 (gaps and the title bar).
inline int picker_hit(int x, int y)
{
    for (size_t i = 0; i < kTiles; ++i) if (tile_rect(i).hit(x, y)) return int(i);
    return -1;
}
inline const char* tile_label(size_t i)
{
    const Tile& t = kTileModes[i];
    return t.mode == modes::rules::Mode::Subtitle ? modes::rules::kSubtitleLabels[size_t(t.dir)] : modes::rules::def(t.mode).label;
}
inline uint32_t tile_color(size_t i) { return modes::rules::def(kTileModes[i].mode).color; }
// Highlighted: the current mode (subtitle: its direction too).
inline bool tile_current(size_t i, const modes::rules::State& s)
{
    const Tile& t = kTileModes[i];
    return s.mode == t.mode && (t.mode != modes::rules::Mode::Subtitle || s.dir == t.dir);
}
// What a tap on tile i asks the rules: select {mode, dir}. The rules decide: another mode = enter it; the current
// one = leave it (to IDLE, settings back to where it came from); the other subtitle direction = switch.
inline modes::rules::Value tile_value(size_t i)
{
    modes::rules::Value v;
    v.target = kTileModes[i].mode;
    v.dir = kTileModes[i].dir;
    return v;
}

// ---- Quick settings ----
enum class Quick : uint8_t { None, Back, VolumeDown, VolumeUp, Wifi, AllOn, AllOff, Hearing };
constexpr Rect kVolumeDown{124, 34, 56, 36}, kVolumeUp{252, 34, 56, 36};
constexpr Rect kWifiButton{222, 76, 86, 36};
constexpr Rect kAllOn{92, 118, 104, 36}, kAllOff{204, 118, 104, 36};
constexpr Rect kHearing{204, 178, 104, 36};
inline Quick quick_hit(int x, int y)
{
    if (kBack.hit(x, y)) return Quick::Back;
    if (kVolumeDown.hit(x, y)) return Quick::VolumeDown;
    if (kVolumeUp.hit(x, y)) return Quick::VolumeUp;
    if (kWifiButton.hit(x, y)) return Quick::Wifi;
    if (kAllOn.hit(x, y)) return Quick::AllOn;
    if (kAllOff.hit(x, y)) return Quick::AllOff;
    if (kHearing.hit(x, y)) return Quick::Hearing;
    return Quick::None;
}
// - / + by one step (10) inside 10..100, as the ASR volume words; -1 for any other button.
inline int volume_after(Quick b, int current)
{
    if (b == Quick::VolumeDown) return std::max(kVolumeMin, std::min(kVolumeMax, current) - kVolumeStep);
    if (b == Quick::VolumeUp) return std::min(kVolumeMax, std::max(kVolumeMin, current) + kVolumeStep);
    return -1;
}
// 计费 全开 / 全关 = the digits 9 / 0, sent to mind as the ASR digit words are (mode_input); -1 otherwise.
inline int billing_digit(Quick b) { return b == Quick::AllOn ? 9 : b == Quick::AllOff ? 0 : -1; }

// The line under the billing buttons. Billing has no state (mind keeps none): it says what the last answered 9 / 0
// was and how long ago, by the robot's clock (it has no wall clock). digit < 0 (no answer yet, or the answer's
// input is not known) = an empty line. A code other than ok is shown.
inline void billing_line(char* out, size_t n, int digit, const char* code, int64_t age_s)
{
    if (!n) return;
    out[0] = 0;
    if (digit != 0 && digit != 9) return;
    if (age_s < 0) age_s = 0;
    char age[16];
    if (age_s < 60) std::snprintf(age, sizeof age, "%ds", int(age_s));
    else if (age_s < 3600) std::snprintf(age, sizeof age, "%dm", int(age_s / 60));
    else std::snprintf(age, sizeof age, "%dh", int(age_s / 3600 > 99 ? 99 : age_s / 3600));
    const bool ok = !code || !*code || !std::strcmp(code, "ok");
    std::snprintf(out, n, text::kLastFormat, digit == 9 ? text::kAllOn : text::kAllOff, ok ? "" : " ", ok ? "" : code, age);
}
}
