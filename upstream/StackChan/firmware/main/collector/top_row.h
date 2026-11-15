/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace collector::top {
// Home page top row (owner 2026-11-15, collector-0.4.98), one strip as tall as the mode badge:
//   [stage blocks] [M12: lens person face] [name ...........] [Wi-Fi bars] [battery %] [MODE badge]
// 0.5.00: while the vision burst runs (burst_indicator.h) a burst group sits between the name and the Wi-Fi bars:
//   [stage blocks] [M12: lens person face] [name ....] [eye 12 + time bar] [Wi-Fi bars] [battery %] [MODE badge]
// taking its width from the end of the name's room (the name keeps the rest, cut between characters as before; when
// the name would not fit beside the full group, the count gives way and only the eye and its time bar stay).
// Pure logic, host-tested (tests/top_row_test.cpp): what the main loop's values mean for the drawing. The main loop
// samples M12 / Wi-Fi / power before the LVGL lock and passes them in ScreenStatus; the screen draws on change only.

// ---- M12 vision -----------------------------------------------------------------------------------------------
// Offline: no M12 (serial down or no heartbeat for 6 s). Off: online, standby. Hot: m12d paused detection (tier 3,
// >= 90 C). On: detecting (m12d confirmed det); person / face / known only then.
enum class VisionPower : uint8_t { Offline, Off, Hot, On };
constexpr size_t kNameBytes = 32;               // m12_protocol.h kM12NameBytes
constexpr int64_t kFreshUs = 1500000;           // a detection line older than this says nothing (the face view's rule)
constexpr int64_t kHoldUs = 1000000;            // person / face / name stay this long after the last frame showing them

struct Vision {
    VisionPower power = VisionPower::Offline;
    bool person = false, face = false;          // a face counts as a person too (close-up: no body in view)
    char name[kNameBytes + 1] = {};             // the recognised face's name ("" = nobody recognised)
    bool known() const { return name[0] != 0; }
    bool operator==(const Vision& o) const
    {
        return power == o.power && person == o.person && face == o.face && !std::strcmp(name, o.name);
    }
    bool operator!=(const Vision& o) const { return !(*this == o); }
};

// From an M12Snapshot (m12_unit.h; a template so this header stays free of the M12 code). The recognised name is the
// largest named face of the last fresh detection line.
template <typename Snapshot> Vision vision_from(const Snapshot& m, int64_t now_us)
{
    using Function = decltype(m.function);
    Vision v;
    if (!m.valid || !m.online) return v;
    if (m.hb.tier == 3) { v.power = VisionPower::Hot; return v; }
    if (m.function != Function::detect) { v.power = VisionPower::Off; return v; }
    v.power = VisionPower::On;
    const bool answered = m.frame_us > 0 && m.frame_us > m.requested_us;
    if (!answered || now_us - m.frame_us >= kFreshUs) return v;
    const size_t faces = std::min<size_t>(m.count, sizeof m.faces / sizeof m.faces[0]);
    v.face = faces > 0;
    v.person = v.face || m.persons > 0;
    long best = -1;
    for (size_t i = 0; i < faces; ++i) {
        const auto& f = m.faces[i];
        const long area = long(f.w) * long(f.h);
        if (f.name[0] && area > best) { best = area; std::snprintf(v.name, sizeof v.name, "%s", f.name); }
    }
    return v;
}

// M12 lines come at the camera's rate and a face is often missed for a frame: what was seen stays kHoldUs, so the
// row does not flicker. Power changes show at once (turning vision off clears everything).
class VisionHold {
public:
    Vision tick(const Vision& raw, int64_t now_us)
    {
        if (raw.power != VisionPower::On) { have_ = false; return raw; }
        if (!have_) { shown_ = Vision{}; shown_.power = VisionPower::On; person_us_ = face_us_ = name_us_ = 0; have_ = true; }
        if (raw.person) person_us_ = now_us;
        if (raw.face) face_us_ = now_us;
        if (raw.known()) { name_us_ = now_us; std::snprintf(shown_.name, sizeof shown_.name, "%s", raw.name); }
        shown_.person = raw.person || (person_us_ && now_us - person_us_ < kHoldUs);
        shown_.face = raw.face || (face_us_ && now_us - face_us_ < kHoldUs);
        if (!raw.known() && !(name_us_ && now_us - name_us_ < kHoldUs)) shown_.name[0] = 0;
        shown_.person = shown_.person || shown_.face;
        return shown_;
    }

private:
    Vision shown_{};
    int64_t person_us_ = 0, face_us_ = 0, name_us_ = 0;
    bool have_ = false;
};

// How the name is drawn. CJK: the name in the SD CJK font (loaded, and it has every glyph). Ascii: an ASCII name in
// the ASCII font (any time). Stand: "known" in the ASCII font, never a Chinese string without its glyphs.
enum class NameFont : uint8_t { None, Cjk, Ascii, Stand };
constexpr const char* kKnownStandIn = "known";
inline bool ascii_text(const char* s)
{
    for (; s && *s; ++s) if (static_cast<uint8_t>(*s) >= 0x80) return false;
    return true;
}
// One UTF-8 character from `s` at `i` (advanced past it); false at the end or on a malformed sequence.
inline bool utf8_next(const char* s, size_t& i, uint32_t& cp)
{
    const auto b = [&](size_t k) { return static_cast<uint8_t>(s[i + k]); };
    const uint8_t c = b(0);
    if (!c) return false;
    size_t n = c < 0x80 ? 0 : (c & 0xE0) == 0xC0 ? 1 : (c & 0xF0) == 0xE0 ? 2 : (c & 0xF8) == 0xF0 ? 3 : 9;
    if (n == 9) return false;
    cp = n == 0 ? c : n == 1 ? (c & 0x1F) : n == 2 ? (c & 0x0F) : (c & 0x07);
    for (size_t k = 1; k <= n; ++k) {
        if ((b(k) & 0xC0) != 0x80) return false;   // also stops at the terminating 0
        cp = cp << 6 | (b(k) & 0x3F);
    }
    if ((n == 1 && cp < 0x80) || (n == 2 && cp < 0x800) || (n == 3 && (cp < 0x10000 || cp > 0x10FFFF))) return false;
    i += n + 1;
    return true;
}
// Every character of `s` passes `has(cp)` (e.g. the font has its glyph); false for a malformed string.
template <typename Has> bool all_chars(const char* s, Has has)
{
    if (!s) return false;
    size_t i = 0;
    uint32_t cp = 0;
    while (s[i]) if (!utf8_next(s, i, cp) || !has(cp)) return false;
    return true;
}
// The bytes of the longest whole-character prefix of `s` whose width (sum of `width(cp)`) fits in `avail` px, so a
// long name is cut between characters, never through one. A malformed tail is dropped.
template <typename Width> size_t fit_prefix(const char* s, int avail, Width width)
{
    if (!s) return 0;
    size_t i = 0, fit = 0;
    uint32_t cp = 0;
    int used = 0;
    while (s[i] && utf8_next(s, i, cp)) {
        used += width(cp);
        if (used > avail) break;
        fit = i;
    }
    return fit;
}
// `cjk_covers`: the CJK font is loaded and has a glyph for every character of `name` (the screen checks it).
inline NameFont name_font(const char* name, bool cjk_covers)
{
    if (!name || !*name) return NameFont::None;
    if (cjk_covers) return NameFont::Cjk;
    return ascii_text(name) ? NameFont::Ascii : NameFont::Stand;
}

// ---- Wi-Fi ----------------------------------------------------------------------------------------------------
// -1 not connected (no IP), 0 connected but signal unknown, 1..4 bars from the station's RSSI.
constexpr int kWifiBars = 4;
inline int wifi_bars(bool connected, bool rssi_valid, int rssi)
{
    if (!connected) return -1;
    if (!rssi_valid || rssi >= 0) return 0;
    return rssi >= -55 ? 4 : rssi >= -65 ? 3 : rssi >= -75 ? 2 : 1;
}

// ---- Battery --------------------------------------------------------------------------------------------------
// Unknown: no power reading. Low <= 15 %, Mid <= 30 %, else Ok. Charging only when the reading says so.
enum class BatteryLevel : uint8_t { Unknown, Low, Mid, Ok };
struct Battery {
    BatteryLevel level = BatteryLevel::Unknown;
    int percent = -1;                           // 0..100, -1 unknown
    bool charging = false;
    bool operator==(const Battery& o) const { return level == o.level && percent == o.percent && charging == o.charging; }
    bool operator!=(const Battery& o) const { return !(*this == o); }
};
inline Battery battery_from(bool valid, int percent, bool charging)
{
    Battery b;
    if (!valid) return b;
    b.percent = std::clamp(percent, 0, 100);
    b.charging = charging;
    b.level = b.percent <= 15 ? BatteryLevel::Low : b.percent <= 30 ? BatteryLevel::Mid : BatteryLevel::Ok;
    return b;
}
// Filled width inside a battery body `inner` px wide: at least 1 px for any charge above 0.
inline int battery_fill(const Battery& b, int inner)
{
    if (b.percent <= 0) return 0;
    return std::max(1, b.percent * inner / 100);
}

// ---- Layout ---------------------------------------------------------------------------------------------------
// The row is one drawn object (no child objects: nothing more in internal RAM than the one object), x 0 = its left
// edge, `width` = up to kRowGap before the badge. Left to right: stage blocks, lens, person, face, the name in the
// free width; right to left from the end: battery (nub), bolt, % digits, Wi-Fi bars.
constexpr int kRowH = 19;                      // the badge: collector_mono_15 line height 17 + 2 x 1 px padding
constexpr int kBlockW = 7, kBlockGap = 3, kBlocksW = 3 * kBlockW + 2 * kBlockGap;
constexpr int kLensPx = 15, kPersonW = 12, kFaceW = 14, kIconGap = 4, kGroupGap = 6;
constexpr int kBarW = 3, kBarGap = 1, kBarsW = kWifiBars * kBarW + (kWifiBars - 1) * kBarGap, kBarsH = 13;
constexpr int kBatW = 23, kBatH = 12, kBatNub = 2, kBoltW = 8, kBoltGap = 2, kPctGap = 3;
// 0.5.00 burst group: an eye (almond outline + pupil), the look count, and a 2 px time bar under both.
constexpr int kEyeW = 17, kEyeH = 11, kEyeGap = 3, kTimeBarH = 2;
inline int burst_w(int count_w) { return kEyeW + kEyeGap + count_w; }
struct Layout {
    int blocks = -1;                           // -1 = not drawn
    int lens = 0, person = -1, face = -1;
    int name = 0, name_w = 0;                  // the name's room (0 = none)
    int burst = -1, burst_w = 0;               // the burst group (-1 = not drawn)
    int wifi = 0, pct = 0, bolt = -1, battery = 0;
};
// `burst_group_w` > 0: the burst group's width (burst_w(count text width)); 0 = no burst.
inline Layout layout(int width, bool stage, bool vision_on, int pct_w, bool bolt, int burst_group_w = 0)
{
    Layout l;
    int x = 0;
    if (stage) { l.blocks = 0; x = kBlocksW + kGroupGap; }
    l.lens = x;
    x += kLensPx;
    if (vision_on) { l.person = x + kIconGap; l.face = l.person + kPersonW + kIconGap; x = l.face + kFaceW; }
    l.name = x + kGroupGap;
    int r = width - kBatNub - kBatW;
    l.battery = r;
    if (bolt) { r -= kBoltGap + kBoltW; l.bolt = r; }
    r -= kPctGap + pct_w;
    l.pct = r;
    r -= kPctGap + kBarsW;
    l.wifi = r;
    if (burst_group_w > 0) { r -= kGroupGap + burst_group_w; l.burst = r; l.burst_w = burst_group_w; }
    l.name_w = std::max(0, r - kGroupGap - l.name);
    return l;
}
// 0.5.00: the burst group's width beside a name `name_px` wide (0 = no name): the full group (eye + count) when the
// name still fits beside it, else the eye alone (the count gives way to the name; the eye and its time bar stay).
inline int burst_fit(int width, bool stage, bool vision_on, int pct_w, bool bolt, int count_w, int name_px)
{
    const int full = burst_w(count_w);
    if (name_px <= 0 || layout(width, stage, vision_on, pct_w, bolt, full).name_w >= name_px) return full;
    return kEyeW;
}
// The width of `s` drawn with `width(cp)` per character (malformed tail ignored).
template <typename Width> int text_width(const char* s, Width width)
{
    if (!s) return 0;
    size_t i = 0;
    uint32_t cp = 0;
    int w = 0;
    while (s[i] && utf8_next(s, i, cp)) w += width(cp);
    return w;
}
}
