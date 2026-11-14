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
// Rules (numbers from fw/screen.cpp:879-889 so home feels like the other pages):
//   swipe      main axis moved >= 55 px, main > 2 x cross axis, cross axis never strayed > 55 px
//              from the start, held 30-1000 ms; up/down added beside left/right.
//   tap        held 30-700 ms, never more than 18 px from the start on either axis.
//   double     a second tap pressed < 300 ms after the first tap's release. A first tap with no
//              second press for 300 ms is a Tap.
//   debounce   a press < 180 ms after the last release is ignored until its own release.
//   lost touch the finger "lifts" for a frame or two mid-stroke: a press <= 60 ms after the release
//              and <= 80 px from the last point continues the same stroke (owner-approved rule
//              "60 ms, position continuous"; 80 px covers a fast swipe across a 60 ms gap).
// A stroke is classified 60 ms after its release (once it can no longer continue); a tap waits for
// the 300 ms double-tap window. The classifier only names the gesture; what it does depends on the
// mode (snapshot touch.tap / touch.double) and is decided by the caller.
enum class Gesture : uint8_t { None, Tap, DoubleTap, SwipeUp, SwipeDown, SwipeLeft, SwipeRight };

class GestureClassifier {
public:
    static constexpr int kSwipeMinPx = 55;
    static constexpr int kSwipeCrossMaxPx = 55;
    static constexpr uint32_t kSwipeMinMs = 30, kSwipeMaxMs = 1000;
    static constexpr int kTapMaxPx = 18;
    static constexpr uint32_t kTapMinMs = 30, kTapMaxMs = 700;
    static constexpr uint32_t kDoubleWindowMs = 300;
    static constexpr uint32_t kDebounceMs = 180;
    static constexpr uint32_t kRejoinMs = 60;
    static constexpr int kRejoinPx = 80;

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
            if (distance(x, y) <= kRejoinPx) { phase_ = Phase::Down; add(x, y); return; }
            finish();   // not a continuation: the lifted stroke is done, this is a new press
        }
        if (phase_ == Phase::Down) { add(x, y); return; }
        if (phase_ == Phase::Ignoring) return;
        // Idle: a new press.
        if (has_release_ && now_ms - last_release_ < kDebounceMs) { phase_ = Phase::Ignoring; return; }
        if (tap_pending_ && now_ms - tap_release_ >= kDoubleWindowMs) { push(Gesture::Tap); tap_pending_ = false; }
        phase_ = Phase::Down;
        pressed_at_ = now_ms;
        start_x_ = last_x_ = x;
        start_y_ = last_y_ = y;
        max_dx_ = max_dy_ = 0;
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
    void add(int x, int y)
    {
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
    Gesture classify() const
    {
        const uint32_t held = released_at_ - pressed_at_;
        const int dx = last_x_ - start_x_, dy = last_y_ - start_y_;
        const int adx = abs_int(dx), ady = abs_int(dy);
        if (held >= kSwipeMinMs && held <= kSwipeMaxMs) {
            if (adx >= kSwipeMinPx && adx > ady * 2 && max_dy_ <= kSwipeCrossMaxPx)
                return dx < 0 ? Gesture::SwipeLeft : Gesture::SwipeRight;
            if (ady >= kSwipeMinPx && ady > adx * 2 && max_dx_ <= kSwipeCrossMaxPx)
                return dy < 0 ? Gesture::SwipeUp : Gesture::SwipeDown;
        }
        if (held >= kTapMinMs && held <= kTapMaxMs && max_dx_ <= kTapMaxPx && max_dy_ <= kTapMaxPx)
            return Gesture::Tap;
        return Gesture::None;
    }
    // The lifted stroke can no longer continue: classify it.
    void finish()
    {
        phase_ = Phase::Idle;
        last_release_ = released_at_;
        has_release_ = true;
        const Gesture g = classify();
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

    Phase phase_ = Phase::Idle;
    uint32_t pressed_at_ = 0, released_at_ = 0;
    int start_x_ = -1, start_y_ = -1, last_x_ = -1, last_y_ = -1, max_dx_ = 0, max_dy_ = 0;
    bool has_release_ = false;
    uint32_t last_release_ = 0;
    bool tap_pending_ = false;
    uint32_t tap_release_ = 0;
    Gesture queue_[kQueue] = {};
    uint8_t head_ = 0, count_ = 0;
};
} // namespace collector::touch
