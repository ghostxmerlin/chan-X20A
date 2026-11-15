/* SPDX-License-Identifier: MIT */
#pragma once
// 视觉爆发 indicator (collector-0.5.00, owner 2026-11-15; contract docs/superpowers/specs/2026-11-15-burst-indicator.md).
// While the ear's vision burst runs (hello takes and recognises photos one by one for 2 minutes after "注意看"), the
// ear sends on /mouth
//   {"type":"burst","on":true,"left_s":<0..600>,"looks":<looks so far>}   at start and after every recognised look
//   {"type":"burst","on":false}                                           at stop
// and the home top row shows an eye, the look count and a draining time bar. Display only: no mode change, no reply.
// Pure logic (no FreeRTOS, no LVGL), driven by tests/burst_indicator_test.cpp:
//   /mouth task: parse() -> pack() into one atomic word (mouth_link.cpp; the only writer), lost = the link dropped.
//   LVGL timer: Tracker::tick(word, now) every pass -> Started / Stopped events (toasts) and the View it draws.
// A lost stop frame cannot leave it on: no frame for max(left_s + 10 s, 15 s) since the last one turns it off.
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace collector::burst {
constexpr size_t kTextMax = 200;                  // a burst frame longer than this is a bad message (ignored)
constexpr int kLeftMaxS = 600;                    // left_s 0..600
constexpr uint32_t kLooksMax = 4095;              // the packed word keeps 12 bits; the row shows at most 999
constexpr int64_t kGraceUs = 10LL * 1000000;      // the safety timeout: left_s + 10 s after the last frame ...
constexpr int64_t kMinHoldUs = 15LL * 1000000;    // ... but never less than 15 s

struct Frame {
    bool on = false;
    int left_s = 0;                               // on only
    uint32_t looks = 0;                           // on only
};

// A /mouth text already read as type "burst" (mouth_modes.h read_text), `n` its length. false (ignored, no reply):
// too long; "on" missing or not a bool; on and "left_s" not an integer 0..600 or "looks" not an integer >= 0 (bool,
// float, string, null, negative: bad). Off ignores every other field; unknown extra fields are ignored. looks above
// kLooksMax is kept as kLooksMax.
inline bool parse(ArduinoJson::JsonObjectConst o, size_t n, Frame& f)
{
    if (n > kTextMax) return false;
    const auto on = o["on"];
    if (!on.is<bool>()) return false;
    Frame r;
    r.on = on.as<bool>();
    if (r.on) {
        const auto left = o["left_s"], looks = o["looks"];
        if (left.is<bool>() || !left.is<int>() || looks.is<bool>() || !looks.is<uint32_t>()) return false;
        const int l = left.as<int>();
        if (l < 0 || l > kLeftMaxS) return false;
        r.left_s = l;
        r.looks = std::min(looks.as<uint32_t>(), kLooksMax);
    }
    f = r;
    return true;
}

// One 32-bit word, written by the /mouth task only and polled lock free by the LVGL timer:
//   [31:24] seq (1..255, +1 per publish; 0 = nothing yet)  [23] on  [22] lost (the link dropped)
//   [21:12] left_s  [11:0] looks
struct Word {
    uint8_t seq = 0;
    bool on = false, lost = false;
    int left_s = 0;
    uint32_t looks = 0;
};
inline uint8_t next_seq(uint8_t seq) { return seq == 255 ? 1 : uint8_t(seq + 1); }
inline uint32_t pack(const Frame& f, uint8_t seq, bool lost)
{
    const uint32_t left = f.on ? uint32_t(std::clamp(f.left_s, 0, kLeftMaxS)) : 0;
    const uint32_t looks = f.on ? std::min(f.looks, kLooksMax) : 0;
    return uint32_t(seq) << 24 | uint32_t(f.on) << 23 | uint32_t(lost) << 22 | left << 12 | looks;
}
inline Word unpack(uint32_t w)
{
    Word u;
    u.seq = uint8_t(w >> 24);
    u.on = w >> 23 & 1;
    u.lost = w >> 22 & 1;
    u.left_s = int(w >> 12 & 0x3FF);
    u.looks = w & 0xFFF;
    return u;
}
// The word after `current` for frame `f` (the writer's step).
inline uint32_t publish(uint32_t current, const Frame& f, bool lost)
{
    return pack(f, next_seq(unpack(current).seq), lost);
}

// The safety timeout after a frame with `left_s` (from the frame's arrival).
inline int64_t hold_us(int left_s) { return std::max(int64_t(left_s) * 1000000 + kGraceUs, kMinHoldUs); }

// What the row draws. left_s counts down on the robot's clock from the last frame (rounded up, never below 0);
// total_s is the largest left_s seen in this burst (the bar's full length, >= 1).
struct View {
    bool on = false;
    uint32_t looks = 0;
    int left_s = 0, total_s = 1;
    bool operator==(const View& o) const { return on == o.on && looks == o.looks && left_s == o.left_s && total_s == o.total_s; }
    bool operator!=(const View& o) const { return !(*this == o); }
};

enum class Event : uint8_t { None, Started, Stopped };
enum class Why : uint8_t { None, Frame, Timeout, Link };   // what stopped it
inline const char* why_name(Why w)
{
    switch (w) {
    case Why::Frame: return "frame";
    case Why::Timeout: return "timeout";
    case Why::Link: return "link";
    default: return "";
    }
}

// LVGL timer side. tick() every pass with the latest word: a new seq is a frame (or the link drop); otherwise only the
// countdown and the safety timeout move. At most one event per tick.
class Tracker {
public:
    Event tick(uint32_t word, int64_t now_us)
    {
        const Word u = unpack(word);
        Event e = Event::None;
        if (u.seq != seen_) {
            seen_ = u.seq;
            if (u.on) {
                if (!view_.on) { view_ = View{}; view_.on = true; view_.total_s = 1; e = Event::Started; ++starts_; }
                view_.looks = u.looks;
                left_at_ = u.left_s;
                view_.total_s = std::max({view_.total_s, u.left_s, 1});
                last_us_ = now_us;
            } else if (view_.on) {
                stop(u.lost ? Why::Link : Why::Frame);
                e = Event::Stopped;
            }
        } else if (view_.on && now_us - last_us_ >= hold_us(left_at_)) {
            stop(Why::Timeout);
            e = Event::Stopped;
        }
        if (view_.on) {
            const int64_t gone = now_us - last_us_;
            const int64_t left_us = int64_t(left_at_) * 1000000 - gone;
            view_.left_s = left_us <= 0 ? 0 : int((left_us + 999999) / 1000000);
        }
        return e;
    }
    const View& view() const { return view_; }
    // The look count when it stopped (for the toast) and why.
    uint32_t stopped_looks() const { return stopped_looks_; }
    Why why() const { return why_; }
    uint32_t starts() const { return starts_; }

private:
    void stop(Why w)
    {
        stopped_looks_ = view_.looks;
        why_ = w;
        view_ = View{};
    }
    View view_{};
    uint8_t seen_ = 0;
    int left_at_ = 0;
    int64_t last_us_ = 0;
    uint32_t stopped_looks_ = 0, starts_ = 0;
    Why why_ = Why::None;
};

// Toasts (ASCII, the toast font): "LOOKING 2 MIN" (left_s rounded up to minutes; under a minute "LOOKING 45 S";
// 0 "LOOKING") and "LOOK DONE 12".
inline void start_toast(int left_s, char* out, size_t cap)
{
    if (left_s >= 60) std::snprintf(out, cap, "LOOKING %d MIN", (left_s + 59) / 60);
    else if (left_s > 0) std::snprintf(out, cap, "LOOKING %d S", left_s);
    else std::snprintf(out, cap, "LOOKING");
}
inline void stop_toast(uint32_t looks, char* out, size_t cap)
{
    std::snprintf(out, cap, "LOOK DONE %lu", (unsigned long)looks);
}

// The row's look count: "0".."999" ("999" for more).
inline void count_text(uint32_t looks, char* out, size_t cap)
{
    std::snprintf(out, cap, "%lu", (unsigned long)std::min<uint32_t>(looks, 999));
}
// The time bar's filled width inside `width` px: left_s / total_s, at least 1 px while any time is left.
inline int bar_fill(const View& v, int width)
{
    if (!v.on || v.left_s <= 0 || width <= 0) return 0;
    const int total = std::max(v.total_s, 1);
    return std::clamp(int(int64_t(v.left_s) * width / total), 1, width);
}
}
