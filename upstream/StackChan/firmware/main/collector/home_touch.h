/* SPDX-License-Identifier: MIT */
#pragma once
#include "touch_gesture.h"
#include <cstddef>
#include <cstdint>

namespace collector::touch {
// The home page's touch input (hello modes step 1b, collector-0.4.92), pure so it is host-tested
// (tests/home_touch_test.cpp). screen.cpp hands every fresh sample to sample() and every failed / stale read to
// lost(). A lost read is only a release for the classifier: the stroke goes on if the finger is still there within
// its 60 ms / 80 px rejoin. It must NOT cancel the stroke until the finger lifts (the other pages do that): the part
// before the glitch would then be classified alone, and a short one becomes a tap 300 ms later (in TALK: say a
// round). Review I1, 2026-11-14.
//
// 0.4.96: now_ms is the touch reading's own time (the FT6336 poll that produced it), not the screen timer's, so a
// stroke starts at its first down sample even when the timer runs late; the same reading seen twice (the timer
// faster than the poll) is fed once. Each finished stroke leaves a Stroke record (take_stroke) for the
// telemetry ring (StrokeRing, touch.home_strokes).
class HomeTouch {
public:
    void sample(bool down, uint32_t now_ms, int x, int y)
    {
        if (down && fed_ && now_ms == last_ms_) return;   // the same reading again: not a fresh sample
        last_ms_ = now_ms; fed_ = true;
        g_.feed(down, now_ms, x, y);
    }
    void lost() { g_.note_lost(); g_.feed(false, last_ms_, -1, -1); }   // at the last sample's time: may rejoin
    Gesture take(uint32_t now_ms) { return g_.take(now_ms); }
    bool take_stroke(Stroke& out) { return g_.take_stroke(out); }
    void reset() { g_.reset(); }

private:
    GestureClassifier g_;
    uint32_t last_ms_ = 0;
    bool fed_ = false;
};

// The last N finished home strokes, oldest first (static, a few bytes each). The caller locks.
template<size_t N>
class StrokeRing {
public:
    void push(const Stroke& s) { items_[(head_ + count_) % N] = s; if (count_ < N) ++count_; else head_ = (head_ + 1) % N; ++total_; }
    size_t size() const { return count_; }
    const Stroke& at(size_t i) const { return items_[(head_ + i) % N]; }   // 0 = oldest
    uint32_t total() const { return total_; }
private:
    Stroke items_[N] = {};
    size_t head_ = 0, count_ = 0;
    uint32_t total_ = 0;
};
}
