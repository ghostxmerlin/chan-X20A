/* SPDX-License-Identifier: MIT */
#pragma once
// Robot command line (collector-0.5.10, 2026-11-18): mind's robot tools reach hello through the ear's /mouth link.
//   down: {"type":"cmd","id":<uint32 > 0>,"do":<verb>,"args":{...}}            (<= kTextMax bytes)
//   up:   {"type":"cmd_result","id":N,"ok":true|false,"error":<code>|null,"data":{...}}   (same link, one per cmd)
// The robot's hello carries "cmd":1 so the ear knows the line exists. Verbs and their args:
//   eyes {state}                  one expression, once (an eyes state id like "happy", or a hearing emotion label)
//   word {text, hold_ms}          a short word under the eyes; hold_ms 0..3600000 (default 2000). A hold over 2 s
//                                 stays (comes back after other toasts) until it ends or "text":"" clears it.
//                                 Every character must be in the compiled word font (m12_words.h kGlyphs).
//   clip {clip_id 1..80 | emotion} one SD voice clip (no args: a random one); refused while hello is speaking
//   yaw {to: left90|left45|right45|right90}   an absolute 45-degree slot (the robot's own left = negative)
//   front {}                      face front (from behind: the return turn, then centre: the ASR "front" plan)
//   turn_around {}                turn around, or back when already turned (the ASR "back" / "front" plans)
//   tracking {on}                 face tracking on / off (the ASR up / down path)
//   nod {times 1..2} / shake {times 1..2}   a small gesture (+-8 deg, slow), back to where it started, no NVS
//   volume {level 10..100 | step -30..30}   speaker volume, RAM only (not saved; the saved one returns at boot)
//   face_learn {name}             M12 learns the largest face in view under name (m12d learn)
//   status {}                     telemetry for robot_status
//   light {r, g, b}               the body's 12 RGB LEDs, 0..255 each, 0 0 0 = off (0.5.21; RAM only)
// Errors: bad_args, unknown_verb, no_glyph, rate_limited, queue_full, busy, speaking, motor_off, no_feedback,
// facing_back, facing_unknown, at_limit, no_room, m12_offline, eyes_off, no_clip, failed.
// A-level verbs (everything that can be seen or heard) run at most kRateMax times per kRateWindowUs per verb.
// Pure logic here (parse, rate limit, glyph check), driven by tests/robot_cmd_test.cpp; robot_cmd.cpp runs them.
#include "m12_words.h"
#include <ArduinoJson.hpp>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace collector::cmd {
constexpr size_t kTextMax = 512;                 // a longer cmd is a bad message (no answer: no id read)
constexpr size_t kWordBytes = 32;                // 8 CJK characters
constexpr size_t kNameBytes = 32;                // kM12NameBytes
constexpr uint32_t kWordDefaultMs = 2000, kWordMaxMs = 3600000;
constexpr int kRateMax = 2;
constexpr int64_t kRateWindowUs = 10000000;
constexpr size_t kResultMax = 640;               // the longest result line (status) is about 400 B

enum class Verb : uint8_t { Eyes, Word, Clip, Yaw, Front, TurnAround, Tracking, Nod, Shake, Volume, FaceLearn, Status, Light, Count };
inline const char* verb_id(Verb v)
{
    static const char* const ids[] = {"eyes", "word", "clip", "yaw", "front", "turn_around", "tracking", "nod",
                                      "shake", "volume", "face_learn", "status", "light"};
    static_assert(sizeof(ids) / sizeof(ids[0]) == size_t(Verb::Count), "one id per verb");
    return v < Verb::Count ? ids[size_t(v)] : "";
}
inline bool verb_from(const char* s, Verb& out)
{
    for (size_t i = 0; s && i < size_t(Verb::Count); ++i)
        if (!std::strcmp(s, verb_id(Verb(i)))) { out = Verb(i); return true; }
    return false;
}
// Seen or heard: rate limited. volume / face_learn / status are not.
inline bool rate_limited_verb(Verb v) { return v != Verb::Volume && v != Verb::FaceLearn && v != Verb::Status; }

struct Cmd {
    uint32_t id = 0;
    Verb verb = Verb::Status;
    char text[kWordBytes + 1] = {};              // eyes state, word text, clip emotion, face name
    int value = 0;                               // clip id (0 = by emotion / random), yaw slot, times, volume level
    int step = 0;                                // volume step (has_step)
    bool on = false, has_step = false;
    uint32_t hold_ms = kWordDefaultMs;
    uint8_t rgb[3] = {};                         // light
};

enum class Parse : uint8_t { Ok, NoId, UnknownVerb, BadArgs, NoGlyph };
inline const char* parse_error(Parse p)
{
    switch (p) {
    case Parse::UnknownVerb: return "unknown_verb";
    case Parse::BadArgs: return "bad_args";
    case Parse::NoGlyph: return "no_glyph";
    default: return "";
    }
}

// Bytes of the UTF-8 character starting at p (1..4), 0 when it is not a valid one.
inline size_t utf8_char(const unsigned char* p)
{
    const size_t n = *p < 0x80 ? 1 : (*p >> 5) == 6 ? 2 : (*p >> 4) == 14 ? 3 : (*p >> 3) == 30 ? 4 : 0;
    for (size_t i = 1; i < n; ++i) if ((p[i] & 0xC0) != 0x80) return 0;
    return n;
}
// Every UTF-8 character of `text` is one of `glyphs` (a UTF-8 string of characters). Bad UTF-8: false.
inline bool glyphs_cover(const char* text, const char* glyphs)
{
    for (auto p = reinterpret_cast<const unsigned char*>(text); *p;) {
        const size_t n = utf8_char(p);
        if (!n) return false;
        bool found = false;
        for (auto q = reinterpret_cast<const unsigned char*>(glyphs); *q && !found;) {
            const size_t m = utf8_char(q);
            if (!m) return false;
            found = m == n && !std::memcmp(p, q, n);
            q += m;
        }
        if (!found) return false;
        p += n;
    }
    return true;
}

// The yaw slot of "to": left = negative (the web page's left button), 0 is not a "to" (front is its own verb).
inline bool yaw_slot(const char* to, int& slot)
{
    static constexpr struct { const char* name; int slot; } kTo[] = {{"left90", -2}, {"left45", -1}, {"right45", 1}, {"right90", 2}};
    for (const auto& t : kTo) if (to && !std::strcmp(to, t.name)) { slot = t.slot; return true; }
    return false;
}

namespace detail {
inline bool copy_text(ArduinoJson::JsonVariantConst v, char (&out)[kWordBytes + 1], size_t max, bool allow_empty)
{
    if (!v.is<const char*>()) return false;
    const char* s = v.as<const char*>();
    const size_t n = std::strlen(s);
    if (n > max || (!n && !allow_empty)) return false;
    for (size_t i = 0; i < n; ++i) if (static_cast<unsigned char>(s[i]) < 0x20 || s[i] == 0x7f) return false;
    std::memcpy(out, s, n); out[n] = 0;
    return true;
}
inline bool int_in(ArduinoJson::JsonVariantConst v, int lo, int hi, int& out)
{
    if (v.is<bool>() || !v.is<int>()) return false;
    const int x = v.as<int>();
    if (x < lo || x > hi) return false;
    out = x;
    return true;
}
}  // namespace detail

// A /mouth text already read as type "cmd" (mouth_modes.h read_text), `n` its length. NoId: no answer can be sent
// (too long, or id missing / not 1..4294967295). Otherwise out.id is set, and an error is answered with that id.
inline Parse parse(ArduinoJson::JsonObjectConst o, size_t n, Cmd& out)
{
    out = Cmd{};
    const auto idv = o["id"];
    if (n > kTextMax || idv.is<bool>() || !idv.is<uint32_t>() || !idv.as<uint32_t>()) return Parse::NoId;
    out.id = idv.as<uint32_t>();
    if (!o["do"].is<const char*>() || !verb_from(o["do"].as<const char*>(), out.verb)) return Parse::UnknownVerb;
    if (!o["args"].isNull() && !o["args"].is<ArduinoJson::JsonObjectConst>()) return Parse::BadArgs;
    const auto a = o["args"];
    using namespace detail;
    switch (out.verb) {
    case Verb::Eyes:
        return copy_text(a["state"], out.text, 20, false) ? Parse::Ok : Parse::BadArgs;
    case Verb::Word: {
        if (!copy_text(a["text"], out.text, kWordBytes, true)) return Parse::BadArgs;
        int hold = int(kWordDefaultMs);
        if (!a["hold_ms"].isNull() && !int_in(a["hold_ms"], 0, int(kWordMaxMs), hold)) return Parse::BadArgs;
        out.hold_ms = uint32_t(hold);
        return glyphs_cover(out.text, m12words::kGlyphs) ? Parse::Ok : Parse::NoGlyph;
    }
    case Verb::Clip:
        if (!a["clip_id"].isNull()) return int_in(a["clip_id"], 1, 80, out.value) ? Parse::Ok : Parse::BadArgs;
        if (!a["emotion"].isNull()) return copy_text(a["emotion"], out.text, 20, false) ? Parse::Ok : Parse::BadArgs;
        return Parse::Ok;                        // a random clip
    case Verb::Yaw:
        return a["to"].is<const char*>() && yaw_slot(a["to"].as<const char*>(), out.value) ? Parse::Ok : Parse::BadArgs;
    case Verb::Tracking:
        if (!a["on"].is<bool>()) return Parse::BadArgs;
        out.on = a["on"].as<bool>();
        return Parse::Ok;
    case Verb::Nod: case Verb::Shake:
        out.value = 2;                           // owner default 2026-11-18: two slow cycles
        return a["times"].isNull() || int_in(a["times"], 1, 2, out.value) ? Parse::Ok : Parse::BadArgs;
    case Verb::Volume: {
        const bool level = !a["level"].isNull(), step = !a["step"].isNull();
        if (level == step) return Parse::BadArgs;
        if (level) return int_in(a["level"], 10, 100, out.value) ? Parse::Ok : Parse::BadArgs;
        out.has_step = true;
        return int_in(a["step"], -30, 30, out.step) ? Parse::Ok : Parse::BadArgs;
    }
    case Verb::FaceLearn:
        return copy_text(a["name"], out.text, kNameBytes, false) ? Parse::Ok : Parse::BadArgs;
    case Verb::Light: {
        int c[3];
        for (int i = 0; i < 3; ++i) if (!int_in(a[i == 0 ? "r" : i == 1 ? "g" : "b"], 0, 255, c[i])) return Parse::BadArgs;
        for (int i = 0; i < 3; ++i) out.rgb[i] = static_cast<uint8_t>(c[i]);
        return Parse::Ok;
    }
    default:
        return Parse::Ok;                        // front, turn_around, status: no args
    }
}

// Volume after a cmd: a level, or the current one plus the step, clamped to 10..100.
inline int volume_after(const Cmd& c, int current)
{
    if (!c.has_step) return c.value;
    const int v = current + c.step;
    return v < 10 ? 10 : v > 100 ? 100 : v;
}

// At most kRateMax A-level commands of one verb in any kRateWindowUs (accepted ones count, refused ones do not).
class RateLimit {
public:
    bool allow(Verb v, int64_t now)
    {
        if (!rate_limited_verb(v) || v >= Verb::Count) return true;
        int64_t* t = at_[size_t(v)];
        int oldest = 0;
        for (int i = 1; i < kRateMax; ++i) if (t[i] < t[oldest]) oldest = i;
        if (t[oldest] && now - t[oldest] < kRateWindowUs) return false;
        t[oldest] = now ? now : 1;
        return true;
    }
private:
    int64_t at_[size_t(Verb::Count)][kRateMax] = {};
};

// A word that stays (hold > the toast's 2 s): the screen shows it again after other toasts until `until_ms`.
struct StickyWord {
    char text[kWordBytes + 1] = {};
    uint32_t until_ms = 0;
    bool on = false;
    void set(const char* t, uint32_t now_ms, uint32_t hold_ms)
    {
        on = t && *t && hold_ms > kWordDefaultMs;
        if (on) { std::snprintf(text, sizeof text, "%s", t); until_ms = now_ms + hold_ms; } else text[0] = 0;
    }
    void clear() { on = false; text[0] = 0; }
    // the word to show again now (no toast on screen), or nullptr; it ends by itself at until_ms
    const char* due(uint32_t now_ms, bool toast_showing)
    {
        if (!on) return nullptr;
        if (int32_t(now_ms - until_ms) >= 0) { clear(); return nullptr; }
        return toast_showing ? nullptr : text;
    }
};

// ---- the service (robot_cmd.cpp; not used by the host tests) ----
void init();                    // the worker task (PSRAM stack) and its queue; at boot, before mouth::init
bool ready();
// /mouth task, a text already read as type "cmd": parse; a bad / too frequent one is answered at once, the rest
// queued (full: queue_full). Never blocks.
void submit(ArduinoJson::JsonObjectConst o, size_t n);
// /mouth task: the next cmd_result line (false: none). Lines made for an older link are dropped.
bool take_result(char* out, size_t cap, size_t& n);
// /mouth task, the link dropped: queued commands are not run, unsent answers go (the ear failed them already).
void link_down();
// LVGL timer: the newest "word" command once (text "" = clear the word that stays).
bool take_word(char (&text)[kWordBytes + 1], uint32_t& hold_ms);
// collector.cpp: fills status data the service cannot see (battery, charging, chip_c, lux, camera, rssi).
void set_status_hook(void (*hook)(ArduinoJson::JsonObject out));
void json(ArduinoJson::JsonObject out);
}  // namespace collector::cmd
