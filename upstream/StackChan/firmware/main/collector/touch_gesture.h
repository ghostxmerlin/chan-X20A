/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace collector::touch {
// Home-page touch gestures (hello modes step 1, Task C3). Pure logic: no LVGL, no FreeRTOS, no
// allocation; time is the caller's monotonic millisecond clock (uint32_t, wrap-safe).
//
// Input is what the device gives the screen timer (fw/collector.cpp:1767-1769): every 20 ms one
// feed(); while a finger is down it carries a real point, every frame without a finger is
// down = false at (-1,-1). A frame whose touch data is stale (I2C read failed) should be fed the same
// way, as a release. Only points with x >= 0 and y >= 0 count (as fw/screen.cpp:868); a release uses
// the last real point.
//
// Rules (numbers from fw/screen.cpp:879-889 so home feels like the other pages; vertical relaxed in 0.4.96):
//   swipe L/R  main axis moved >= 55 px, main > 2 x cross axis, cross axis never strayed > 55 px
//              from the start, held 30-1500 ms.
//   swipe U/D  0.4.96 (owner: "下拉不太好触发"): moved >= 40 px, main > 1.5 x cross axis, cross axis never
//              strayed > 70 px, held 30-1500 ms.
//   tap        held 30-700 ms, never more than 18 px from the start on either axis. A swipe needs >= 40 px,
//              so no stroke is both (static_assert below); a 19-39 px stroke is nothing.
//   double     a second tap pressed < 300 ms after the first tap's release. A first tap with no
//              second press for 300 ms is a Tap.
//   debounce   a press < 180 ms after the last release is ignored until its own release.
//   lost touch the finger "lifts" for a few frames mid-stroke: a press <= 120 ms after the release
//              and <= 80 px from the last point continues the same stroke (owner-approved rule
//              "60 ms, position continuous", widened to 120 ms in 0.4.96 for sparse samples while the eyes
//              animate; 80 px covers a fast swipe across the gap).
// A stroke is classified 120 ms after its release (once it can no longer continue); a tap waits for
// the 300 ms double-tap window. Every finished stroke also leaves a Stroke record (take_stroke): held time,
// travel, sample count / lost reads / largest gap between samples, and the result or why it was nothing. The classifier only names the gesture; what it does depends on the
// mode (snapshot touch.tap / touch.double) and is decided by the caller.
enum class Gesture : uint8_t { None, Tap, DoubleTap, SwipeUp, SwipeDown, SwipeLeft, SwipeRight };
// Why a stroke was nothing: too_short (under 30 ms, or moved 19..(swipe min - 1) px), too_slow (held past the
// swipe / tap limit), diagonal (moved enough but not mainly along one axis), cross (strayed off the axis on the way).
enum class Why : uint8_t { None, TooShort, TooSlow, Diagonal, Cross };
inline const char* gesture_id(Gesture g)
{
    static constexpr const char* kIds[] = {"none", "tap", "double", "swipe_up", "swipe_down", "swipe_left", "swipe_right"};
    return kIds[uint8_t(g) < 7 ? uint8_t(g) : 0];
}
inline const char* why_id(Why w)
{
    static constexpr const char* kIds[] = {"", "too_short", "too_slow", "diagonal", "cross"};
    return kIds[uint8_t(w) < 5 ? uint8_t(w) : 0];
}
// One finished stroke (diagnostics, 0.4.96 telemetry touch.home_strokes).
struct Stroke {
    uint16_t held_ms = 0, gap_max_ms = 0;
    int16_t dx = 0, dy = 0, max_dx = 0, max_dy = 0;
    uint8_t samples = 0, lost = 0;
    Gesture result = Gesture::None;
    Why why = Why::None;
};

class GestureClassifier {
public:
    static constexpr int kSwipeMinPx = 55;              // left / right
    static constexpr int kSwipeCrossMaxPx = 55;
    static constexpr int kSwipeVMinPx = 40;             // up / down (0.4.96)
    static constexpr int kSwipeVCrossMaxPx = 70;
    static constexpr uint32_t kSwipeMinMs = 30, kSwipeMaxMs = 1500;
    static constexpr int kTapMaxPx = 18;
    static constexpr uint32_t kTapMinMs = 30, kTapMaxMs = 700;
    static constexpr uint32_t kDoubleWindowMs = 300;
    static constexpr uint32_t kDebounceMs = 180;
    static constexpr uint32_t kRejoinMs = 120;
    static constexpr int kRejoinPx = 80;
    static_assert(kTapMaxPx < kSwipeVMinPx && kTapMaxPx < kSwipeMinPx, "a stroke is never both a tap and a swipe");
    static_assert(kRejoinMs < kDebounceMs, "a press after the rejoin window is still debounced");

    void feed(bool down, uint32_t now_ms, int x, int y)
    {
        advance(now_ms);
        if (!down) {
            if (phase_ == Phase::Down) { released_at_ = now_ms; phase_ = Phase::Lifted; }
            else if (phase_ == Phase::Ignoring) phase_ = Phase::Idle;
            return;   // repeated release frames while idle or lifted change nothing
        }
        if (x < 0 || y < 0) return;   // a down frame without a point is not a sample
        if (phase_ == Phase::Lifted) {
            if (distance(x, y) <= kRejoinPx) { phase_ = Phase::Down; add(x, y, now_ms); return; }
            finish();   // not a continuation: the lifted stroke is done, this is a new press
        }
        if (phase_ == Phase::Down) { add(x, y, now_ms); return; }
        if (phase_ == Phase::Ignoring) return;
        // Idle: a new press.
        if (has_release_ && now_ms - last_release_ < kDebounceMs) { phase_ = Phase::Ignoring; return; }
        if (tap_pending_ && now_ms - tap_release_ >= kDoubleWindowMs) { push(Gesture::Tap); tap_pending_ = false; }
        phase_ = Phase::Down;
        pressed_at_ = last_sample_ms_ = now_ms;   // the stroke starts at its first down sample (time and place)
        start_x_ = last_x_ = x;
        start_y_ = last_y_ = y;
        max_dx_ = max_dy_ = 0;
        samples_ = 1; lost_ = 0; gap_max_ = 0;
    }

    // A failed / stale touch read (home_touch.h): counted in the stroke record when a stroke is under way.
    void note_lost() { if ((phase_ == Phase::Down || phase_ == Phase::Lifted) && lost_ < 255) ++lost_; }

    // The latest finished stroke since the last call (diagnostics); false when none.
    bool take_stroke(Stroke& out)
    {
        if (!stroke_ready_) return false;
        out = stroke_; stroke_ready_ = false;
        return true;
    }

    // Call once per frame; returns at most one gesture per call (None when there is nothing).
    Gesture take(uint32_t now_ms)
    {
        advance(now_ms);
        if (!count_) return Gesture::None;
        const Gesture g = queue_[head_];
        head_ = static_cast<uint8_t>((head_ + 1) % kQueue);
        --count_;
        return g;
    }

    // Screen off or leaving the home page: forget the stroke, a pending tap and anything queued.
    void reset() { *this = GestureClassifier(); }

private:
    enum class Phase : uint8_t { Idle, Down, Lifted, Ignoring };
    static constexpr uint8_t kQueue = 4;

    static int abs_int(int v) { return v < 0 ? -v : v; }
    int distance(int x, int y) const
    {
        const int dx = abs_int(x - last_x_), dy = abs_int(y - last_y_);
        return dx > dy ? dx : dy;
    }
    void add(int x, int y, uint32_t now_ms)
    {
        const uint32_t gap = now_ms - last_sample_ms_;
        if (gap > gap_max_) gap_max_ = gap;
        last_sample_ms_ = now_ms;
        if (samples_ < 255) ++samples_;
        last_x_ = x;
        last_y_ = y;
        const int dx = abs_int(x - start_x_), dy = abs_int(y - start_y_);
        if (dx > max_dx_) max_dx_ = dx;
        if (dy > max_dy_) max_dy_ = dy;
    }
    void push(Gesture g)
    {
        if (g == Gesture::None || count_ == kQueue) return;
        queue_[(head_ + count_) % kQueue] = g;
        ++count_;
    }
    void advance(uint32_t now_ms)
    {
        if (phase_ == Phase::Lifted && now_ms - released_at_ >= kRejoinMs) finish();
        // A second press already down (or lifted for a frame) still decides between Tap and DoubleTap.
        if (tap_pending_ && phase_ != Phase::Down && phase_ != Phase::Lifted &&
            now_ms - tap_release_ >= kDoubleWindowMs) {
            push(Gesture::Tap);
            tap_pending_ = false;
        }
    }
    Gesture classify(Why& why) const
    {
        why = Why::None;
        const uint32_t held = released_at_ - pressed_at_;
        const int dx = last_x_ - start_x_, dy = last_y_ - start_y_;
        const int adx = abs_int(dx), ady = abs_int(dy);
        const bool h = adx >= kSwipeMinPx, v = ady >= kSwipeVMinPx;   // moved far enough for a swipe
        if (held >= kSwipeMinMs && held <= kSwipeMaxMs) {
            if (h && adx > ady * 2 && max_dy_ <= kSwipeCrossMaxPx)
                return dx < 0 ? Gesture::SwipeLeft : Gesture::SwipeRight;
            if (v && ady * 2 > adx * 3 && max_dx_ <= kSwipeVCrossMaxPx)
                return dy < 0 ? Gesture::SwipeUp : Gesture::SwipeDown;
        }
        if (held >= kTapMinMs && held <= kTapMaxMs && max_dx_ <= kTapMaxPx && max_dy_ <= kTapMaxPx)
            return Gesture::Tap;
        if (held < kSwipeMinMs) why = Why::TooShort;
        else if (h || v) {
            const bool along = (h && adx > ady * 2) || (v && ady * 2 > adx * 3);
            why = held > kSwipeMaxMs ? Why::TooSlow : along ? Why::Cross : Why::Diagonal;
        } else why = max_dx_ > kTapMaxPx || max_dy_ > kTapMaxPx ? Why::TooShort : Why::TooSlow;   // short move / long hold
        return Gesture::None;
    }
    // The lifted stroke can no longer continue: classify it.
    void finish()
    {
        phase_ = Phase::Idle;
        last_release_ = released_at_;
        has_release_ = true;
        Why why;
        const Gesture g = classify(why);
        record(g == Gesture::Tap && tap_pending_ ? Gesture::DoubleTap : g, why);
        if (tap_pending_) {   // this stroke was pressed inside the first tap's window
            tap_pending_ = false;
            if (g == Gesture::Tap) { push(Gesture::DoubleTap); return; }
            push(Gesture::Tap);
            push(g);
            return;
        }
        if (g == Gesture::Tap) { tap_pending_ = true; tap_release_ = released_at_; return; }
        push(g);
    }

    static int16_t clamp16(int v) { return int16_t(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }
    void record(Gesture g, Why why)
    {
        const uint32_t held = released_at_ - pressed_at_;
        stroke_.held_ms = uint16_t(held > 65535 ? 65535 : held);
        stroke_.gap_max_ms = uint16_t(gap_max_ > 65535 ? 65535 : gap_max_);
        stroke_.dx = clamp16(last_x_ - start_x_); stroke_.dy = clamp16(last_y_ - start_y_);
        stroke_.max_dx = clamp16(max_dx_); stroke_.max_dy = clamp16(max_dy_);
        stroke_.samples = samples_; stroke_.lost = lost_;
        stroke_.result = g; stroke_.why = why;
        stroke_ready_ = true;
    }

    Phase phase_ = Phase::Idle;
    uint32_t pressed_at_ = 0, released_at_ = 0, last_sample_ms_ = 0, gap_max_ = 0;
    uint8_t samples_ = 0, lost_ = 0;
    Stroke stroke_{};
    bool stroke_ready_ = false;
    int start_x_ = -1, start_y_ = -1, last_x_ = -1, last_y_ = -1, max_dx_ = 0, max_dy_ = 0;
    bool has_release_ = false;
    uint32_t last_release_ = 0;
    bool tap_pending_ = false;
    uint32_t tap_release_ = 0;
    Gesture queue_[kQueue] = {};
    uint8_t head_ = 0, count_ = 0;
};
} // namespace collector::touch
