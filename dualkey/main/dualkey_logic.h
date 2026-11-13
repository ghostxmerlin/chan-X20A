// DualKey remote: pure logic (no ESP-IDF), unit-tested on the host by tests/dualkey_logic_test.cpp.
//
//  - Debouncer:       key debounce on a fixed sampling period.
//  - RatingGrouper:   plan B.3.3 press grouping. Presses <= 350 ms apart are one group. A gap over 350 ms
//                     closes the open group at once and starts a new one; otherwise the group closes 500 ms
//                     after its last press. At most 5 presses count; extra presses set `capped` and still
//                     extend the group. Pressing the other key closes the open group.
//  - DirDetector:     joystick push -> one direction event. Enter at |axis| >= 76 (60 %), keep while >= 60
//                     (hysteresis), must be held 150 ms, diagonal (minor > 0.6 * major) never counts. After an
//                     event the stick must come back to centre (both |axis| < 30) to re-arm.
//  - SignalQueue:     32 unacknowledged signals, oldest dropped (and counted) when full, resend after reconnect.
//  - Backoff:         reconnect delay 1 s doubling to 30 s.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace dualkey {

inline uint32_t elapsed(uint32_t now, uint32_t since) { return now - since; }  // wrap-safe

// ---------------------------------------------------------------- keys
class Debouncer {
public:
    explicit Debouncer(uint8_t stable_samples = 3) : need_(stable_samples ? stable_samples : 1) {}
    // raw = true while the key reads pressed. Returns +1 on a press edge, -1 on a release edge, else 0.
    int update(bool raw) {
        if (raw == pressed_) {
            run_ = 0;
            return 0;
        }
        if (++run_ < need_) return 0;
        run_ = 0;
        pressed_ = raw;
        return raw ? +1 : -1;
    }
    bool pressed() const { return pressed_; }

private:
    uint8_t need_;
    uint8_t run_ = 0;
    bool pressed_ = false;
};

// ---------------------------------------------------------------- rating groups
constexpr uint32_t kGroupGapMs = 350;
constexpr uint32_t kGroupCloseMs = 500;
constexpr int kMaxPresses = 5;

struct RatingGroup {
    int polarity = 0;  // +1 good, -1 bad
    int count = 0;     // 1..5
    uint32_t presses_ms[kMaxPresses] = {};  // offsets from the first press
    bool capped = false;
    uint32_t first_ms = 0;  // device clock of the first press
    uint32_t last_ms = 0;   // device clock of the last press (including capped extras)
};

class RatingGrouper {
public:
    // Records a press. When it closes the previous group (other key, or gap > 350 ms), that group is copied to
    // *closed and true is returned; the press itself always opens or extends the current group.
    bool press(int polarity, uint32_t now, RatingGroup* closed) {
        bool emitted = false;
        if (open_ && (polarity != cur_.polarity || elapsed(now, cur_.last_ms) > kGroupGapMs)) {
            if (closed) *closed = cur_;
            open_ = false;
            emitted = true;
        }
        if (!open_) {
            cur_ = RatingGroup{};
            cur_.polarity = polarity;
            cur_.first_ms = now;
            open_ = true;
        }
        if (cur_.count < kMaxPresses) {
            cur_.presses_ms[cur_.count++] = elapsed(now, cur_.first_ms);
        } else {
            cur_.capped = true;
        }
        cur_.last_ms = now;
        return emitted;
    }
    // Closes the open group once 500 ms have passed since its last press.
    bool poll(uint32_t now, RatingGroup* closed) {
        if (!open_ || elapsed(now, cur_.last_ms) < kGroupCloseMs) return false;
        if (closed) *closed = cur_;
        open_ = false;
        return true;
    }
    void cancel() { open_ = false; }
    bool is_open() const { return open_; }
    const RatingGroup& current() const { return cur_; }

private:
    RatingGroup cur_{};
    bool open_ = false;
};

// ---------------------------------------------------------------- joystick
enum class Dir : uint8_t { None, Up, Down, Left, Right };

inline const char* dir_name(Dir d) {
    switch (d) {
        case Dir::Up: return "up";
        case Dir::Down: return "down";
        case Dir::Left: return "left";
        case Dir::Right: return "right";
        default: return "none";
    }
}

// Mount correction. 90 turns readings a quarter turn counter-clockwise: right->up, up->left, left->down, down->right.
inline void rotate_reading(int degrees, int& x, int& y) {
    int ox = x, oy = y;
    switch (((degrees % 360) + 360) % 360) {
        case 90: x = -oy; y = ox; break;
        case 180: x = -ox; y = -oy; break;
        case 270: x = oy; y = -ox; break;
        default: break;
    }
}

// How the Chain Joystick sits on this DualKey, measured by the user on 2026-11-13: raw "right" was a
// physical push up, raw "up" a push left. A 90-degree turn makes the reported direction the physical one.
constexpr int kMountRotate = 90;

// Built-in mount correction plus the user's joystick_rotate offset from NVS (0/90/180/270).
inline void mount_correct(int user_rotate, int& x, int& y) { rotate_reading(kMountRotate + user_rotate, x, y); }

struct DirConfig {
    int enter = 76;      // 60 % of 127
    int exit = 60;       // hysteresis: candidate kept while the axis stays at or above this
    int centre = 30;     // both axes below this re-arms
    uint32_t hold_ms = 150;
};

class DirDetector {
public:
    explicit DirDetector(DirConfig cfg = DirConfig{}) : cfg_(cfg) {}

    // x: +right, y: +up, each -127..127. Returns a direction once per push; *start_ms gets the time the push
    // first crossed the enter threshold.
    Dir update(int x, int y, uint32_t now, uint32_t* start_ms) {
        int ax = std::abs(x), ay = std::abs(y);
        if (!armed_) {
            if (ax < cfg_.centre && ay < cfg_.centre) armed_ = true;
            return Dir::None;
        }
        if (cand_ != Dir::None && classify(x, y, cfg_.exit) == cand_) {
            if (elapsed(now, since_) >= cfg_.hold_ms) {
                if (start_ms) *start_ms = since_;
                Dir fired = cand_;
                cand_ = Dir::None;
                armed_ = false;
                return fired;
            }
            return Dir::None;
        }
        cand_ = classify(x, y, cfg_.enter);
        since_ = now;
        return Dir::None;
    }
    bool armed() const { return armed_; }
    Dir candidate() const { return cand_; }

private:
    static Dir classify(int x, int y, int threshold) {
        int ax = std::abs(x), ay = std::abs(y);
        int major = ax > ay ? ax : ay, minor = ax > ay ? ay : ax;
        if (major < threshold || minor * 5 > major * 3) return Dir::None;
        if (ay >= ax) return y > 0 ? Dir::Up : Dir::Down;
        return x > 0 ? Dir::Right : Dir::Left;
    }

    DirConfig cfg_;
    bool armed_ = false;
    Dir cand_ = Dir::None;
    uint32_t since_ = 0;
};

enum class ClickType : uint8_t { Single, Double, Long };

inline const char* click_name(ClickType c) {
    switch (c) {
        case ClickType::Double: return "double";
        case ClickType::Long: return "long";
        default: return "single";
    }
}

// Joystick click fusion. The joystick's own firmware reports "long" only after 3 s held (its shortest setting,
// range 3-10 s) and reports anything released earlier as a single click. We poll the button level and fire our
// own long press at 1 s while it is still held, then drop the joystick's own event for that same press
// (its single ~200 ms after release, or its long at 3 s). Single and double clicks pass through unchanged.
constexpr uint32_t kLongPressMs = 1000;
constexpr uint32_t kSuppressAfterReleaseMs = 1000;  // the joystick's trailing event arrives within ~200 ms

class ClickFuser {
public:
    // Polled button level. Returns true with *out = Long when the hold crosses kLongPressMs.
    bool level(bool pressed, uint32_t now, ClickType* out) {
        if (pressed && !pressed_) {
            pressed_ = true;
            down_ms_ = now;
            long_fired_ = false;
            if (suppress_ && released_ && elapsed(now, release_ms_) >= kSuppressAfterReleaseMs) suppress_ = false;
        } else if (!pressed && pressed_) {
            pressed_ = false;
            if (suppress_) {
                released_ = true;
                release_ms_ = now;
            }
        }
        if (pressed_ && !long_fired_ && elapsed(now, down_ms_) >= kLongPressMs) {
            long_fired_ = true;
            suppress_ = true;
            released_ = false;
            if (out) *out = ClickType::Long;
            return true;
        }
        return false;
    }
    // Event decoded from the joystick (0xE0 report). Returns true with *out when it should be reported.
    bool joystick_event(ClickType type, uint32_t now, ClickType* out) {
        if (suppress_ && released_ && elapsed(now, release_ms_) >= kSuppressAfterReleaseMs) suppress_ = false;
        if (suppress_) {
            suppress_ = false;  // the joystick's own report of the press we already sent as long
            return false;
        }
        if (out) *out = type;
        return true;
    }

private:
    bool pressed_ = false;
    bool long_fired_ = false;
    bool suppress_ = false;
    bool released_ = false;
    uint32_t down_ms_ = 0;
    uint32_t release_ms_ = 0;
};

// ---------------------------------------------------------------- signal bodies and frames
constexpr size_t kBodyMax = 160;   // "kind":...,"value":{...}
constexpr size_t kFrameMax = 256;  // whole signal frame

inline int format_rating(const RatingGroup& g, char* out, size_t n) {
    int len = snprintf(out, n, "\"kind\":\"rating\",\"value\":{\"polarity\":%d,\"count\":%d,\"presses_ms\":[",
                       g.polarity > 0 ? 1 : -1, g.count);
    for (int i = 0; i < g.count && len > 0 && (size_t)len < n; ++i) {
        len += snprintf(out + len, n - len, i ? ",%u" : "%u", (unsigned)g.presses_ms[i]);
    }
    if (len > 0 && (size_t)len < n) len += snprintf(out + len, n - len, "],\"capped\":%s}", g.capped ? "true" : "false");
    return (len > 0 && (size_t)len < n) ? len : -1;
}

inline int format_mode(Dir d, char* out, size_t n) {
    int len = snprintf(out, n, "\"kind\":\"mode\",\"value\":{\"direction\":\"%s\"}", dir_name(d));
    return (len > 0 && (size_t)len < n) ? len : -1;
}

inline int format_click(ClickType c, char* out, size_t n) {
    int len = snprintf(out, n, "\"kind\":\"click\",\"value\":{\"type\":\"%s\"}", click_name(c));
    return (len > 0 && (size_t)len < n) ? len : -1;
}

// ---------------------------------------------------------------- unacknowledged queue
constexpr size_t kQueueCap = 32;

struct Signal {
    uint32_t seq = 0;
    uint32_t t0_ms = 0;  // first press / push start / click time; age_ms = now - t0_ms at send time
    bool sent = false;
    char body[kBodyMax] = {};
};

inline int render_signal(const Signal& s, uint32_t now, char* out, size_t n) {
    int len = snprintf(out, n, "{\"type\":\"signal\",\"seq\":%u,\"age_ms\":%u,%s}", (unsigned)s.seq,
                       (unsigned)elapsed(now, s.t0_ms), s.body);
    return (len > 0 && (size_t)len < n) ? len : -1;
}

class SignalQueue {
public:
    // Appends a signal with the next sequence number (from 1). Drops the oldest when full.
    uint32_t push(const char* body, uint32_t t0_ms) {
        if (count_ == kQueueCap) {
            remove_at(0);
            ++dropped_;
        }
        Signal& s = items_[count_++];
        s.seq = ++last_seq_;
        s.t0_ms = t0_ms;
        s.sent = false;
        strncpy(s.body, body, sizeof(s.body) - 1);
        s.body[sizeof(s.body) - 1] = '\0';
        return s.seq;
    }
    bool ack(uint32_t seq) {
        for (size_t i = 0; i < count_; ++i) {
            if (items_[i].seq == seq) {
                remove_at(i);
                return true;
            }
        }
        return false;
    }
    void mark_unsent() {
        for (size_t i = 0; i < count_; ++i) items_[i].sent = false;
    }
    void mark_sent(uint32_t seq) {
        for (size_t i = 0; i < count_; ++i)
            if (items_[i].seq == seq) items_[i].sent = true;
    }
    const Signal* next_unsent() const {
        for (size_t i = 0; i < count_; ++i)
            if (!items_[i].sent) return &items_[i];
        return nullptr;
    }
    size_t size() const { return count_; }
    uint32_t dropped() const { return dropped_; }
    uint32_t last_seq() const { return last_seq_; }
    const Signal& at(size_t i) const { return items_[i]; }

private:
    void remove_at(size_t i) {
        for (size_t j = i + 1; j < count_; ++j) items_[j - 1] = items_[j];
        --count_;
    }
    Signal items_[kQueueCap];
    size_t count_ = 0;
    uint32_t last_seq_ = 0;
    uint32_t dropped_ = 0;
};

// ---------------------------------------------------------------- reconnect back-off
class Backoff {
public:
    uint32_t next() {
        uint32_t d = delay_;
        delay_ = delay_ >= 15000 ? 30000 : delay_ * 2;
        return d;
    }
    void reset() { delay_ = 1000; }

private:
    uint32_t delay_ = 1000;
};

}  // namespace dualkey
