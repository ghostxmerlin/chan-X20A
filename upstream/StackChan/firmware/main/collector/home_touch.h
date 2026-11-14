/* SPDX-License-Identifier: MIT */
#pragma once
#include "touch_gesture.h"
#include <cstdint>

namespace collector::touch {
// The home page's touch input (hello modes step 1b, collector-0.4.92), pure so it is host-tested
// (tests/home_touch_test.cpp). screen.cpp hands every fresh sample to sample() and every failed / stale read to
// lost(). A lost read is only a release for the classifier: the stroke goes on if the finger is still there within
// its 60 ms / 80 px rejoin. It must NOT cancel the stroke until the finger lifts (the other pages do that): the part
// before the glitch would then be classified alone, and a short one becomes a tap 300 ms later (in TALK: say a
// round). Review I1, 2026-11-14.
class HomeTouch {
public:
    void sample(bool down, uint32_t now_ms, int x, int y)
    {
        last_ms_ = now_ms;
        g_.feed(down, now_ms, x, y);
    }
    void lost() { g_.feed(false, last_ms_, -1, -1); }   // at the last sample's time: the next frame may rejoin
    Gesture take(uint32_t now_ms) { return g_.take(now_ms); }
    void reset() { g_.reset(); }

private:
    GestureClassifier g_;
    uint32_t last_ms_ = 0;
};
}
