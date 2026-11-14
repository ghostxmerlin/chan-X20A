// Home-page touch gestures (hello modes step 1, Task C3). Input is shaped like the device: the touch
// timer (20 ms) reports one real point per frame while the finger is down and (-1,-1) with
// down = false on every frame without a finger (fw/collector.cpp:1767-1769). The first block is the
// plan's test, verbatim; the rest adds touch lost mid-swipe, release bounce, wrap-around and limits.
#include "touch_gesture.h"
#include <cassert>
#include <cstdint>

using namespace collector::touch;

// A finger stroke as the device reports it: one real sample per 20 ms while down, then a release frame at (-1,-1).
static void stroke(GestureClassifier& g, uint32_t t0, int x0, int y0, int x1, int y1, uint32_t held) {
    for (uint32_t t = 0; t <= held; t += 20) g.feed(true, t0 + t, x0 + (x1 - x0) * int(t) / int(held), y0 + (y1 - y0) * int(t) / int(held));
    g.feed(false, t0 + held + 20, -1, -1);
}
static Gesture wait(GestureClassifier& g, uint32_t from, uint32_t until) {
    for (uint32_t t = from; t <= until; t += 10) if (auto r = g.take(t); r != Gesture::None) return r;
    return Gesture::None;
}
// Like the device between strokes: a release frame and a take() every 20 ms.
static Gesture idle(GestureClassifier& g, uint32_t from, uint32_t until) {
    Gesture first = Gesture::None;
    for (uint32_t t = from; t <= until; t += 20) {
        g.feed(false, t, -1, -1);
        const auto r = g.take(t);
        if (r != Gesture::None && first == Gesture::None) first = r;
        else assert(r == Gesture::None);   // at most one gesture per call site in these tests
    }
    return first;
}

int main() {
    // ---- The plan's vectors (docs/superpowers/plans/2026-11-14-hello-modes-step1-framework.md, C3 step 1).
    { GestureClassifier g; stroke(g, 0, 100, 100, 103, 101, 120); assert(wait(g, 140, 700) == Gesture::Tap); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 100); assert(wait(g, 120, 250) == Gesture::None);   // still waiting
      stroke(g, 320, 101, 100, 101, 100, 80); assert(wait(g, 420, 900) == Gesture::DoubleTap); }
    { GestureClassifier g; stroke(g, 0, 160, 200, 162, 60, 240); assert(wait(g, 260, 700) == Gesture::SwipeUp); }
    { GestureClassifier g; stroke(g, 0, 160, 40, 158, 180, 240); assert(wait(g, 260, 700) == Gesture::SwipeDown); }
    { GestureClassifier g; stroke(g, 0, 250, 120, 100, 125, 240); assert(wait(g, 260, 700) == Gesture::SwipeLeft); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 900); assert(wait(g, 920, 1500) == Gesture::None); } // long hold
    { GestureClassifier g; stroke(g, 0, 100, 100, 170, 140, 240); assert(wait(g, 260, 700) == Gesture::None); }  // diagonal
    { GestureClassifier g;                                       // release jitter: last real sample moved 4 px, still a tap
      g.feed(true, 0, 100, 100); g.feed(true, 60, 101, 100); g.feed(true, 100, 104, 101); g.feed(false, 120, -1, -1);
      assert(wait(g, 130, 700) == Gesture::Tap); }
    { GestureClassifier g;                                       // touch lost for one frame mid-swipe: still one swipe up
      for (uint32_t t = 0; t <= 100; t += 20) g.feed(true, t, 160, 200 - int(t));
      g.feed(false, 120, -1, -1);
      for (uint32_t t = 140; t <= 240; t += 20) g.feed(true, t, 160, 200 - int(t) * 140 / 240);
      g.feed(false, 260, -1, -1);
      assert(wait(g, 270, 800) == Gesture::SwipeUp); }

    // ---- Swipe right, and each gesture comes out exactly once.
    { GestureClassifier g; stroke(g, 0, 60, 120, 230, 118, 200);
      assert(idle(g, 240, 1200) == Gesture::SwipeRight); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 102, 100, 120);
      assert(idle(g, 160, 1200) == Gesture::Tap); }               // idle() asserts no second gesture

    // ---- Touch lost early in a swipe (two frames, 40 ms): the first part alone is only 30 px, so the
    // swipe exists only if both parts count as one stroke.
    { GestureClassifier g;
      g.feed(true, 0, 160, 200); g.feed(true, 20, 160, 190); g.feed(true, 40, 161, 180); g.feed(true, 60, 161, 170);
      g.feed(false, 80, -1, -1); assert(g.take(80) == Gesture::None);
      g.feed(false, 100, -1, -1); assert(g.take(100) == Gesture::None);
      for (uint32_t t = 120; t <= 260; t += 20) { g.feed(true, t, 162, 170 - int(t - 120) * 110 / 140); assert(g.take(t) == Gesture::None); }
      assert(idle(g, 280, 1200) == Gesture::SwipeUp); }
    // Same in a fast swipe: the finger travelled 40 px during the 40 ms gap; still one stroke.
    { GestureClassifier g;
      g.feed(true, 0, 160, 200); g.feed(true, 20, 160, 185); g.feed(true, 40, 160, 170);
      g.feed(false, 60, -1, -1); g.feed(false, 80, -1, -1);
      g.feed(true, 100, 161, 130); g.feed(true, 120, 161, 100); g.feed(true, 140, 162, 70);
      assert(idle(g, 160, 1200) == Gesture::SwipeUp); }
    // A lost frame in a tap (release bounce, back down 20 ms later at the same spot): one tap, not a double tap.
    { GestureClassifier g;
      g.feed(true, 0, 100, 100); g.feed(true, 20, 100, 101); g.feed(true, 40, 101, 101);
      g.feed(false, 60, -1, -1); g.feed(true, 80, 102, 101); g.feed(false, 100, -1, -1);
      assert(idle(g, 120, 1200) == Gesture::Tap); }
    // A re-press inside the 60 ms window but far away is not a continuation; and being < 180 ms after the
    // release it is ignored (fw/screen.cpp:879), so only the first tap counts.
    { GestureClassifier g;
      stroke(g, 0, 100, 100, 100, 100, 80);                      // release at 100
      stroke(g, 140, 260, 60, 260, 60, 60);                      // pressed 40 ms after, 160 px away
      assert(idle(g, 220, 1200) == Gesture::Tap); }
    // Bounce 100 ms after a release: ignored (< 180 ms), still a single tap.
    { GestureClassifier g;
      stroke(g, 0, 100, 100, 100, 100, 80);                      // release at 100
      stroke(g, 200, 100, 100, 100, 100, 40);                    // pressed at 200 (100 ms after), held 60 ms
      assert(idle(g, 260, 1200) == Gesture::Tap); }

    // ---- Double tap window: second press 300 ms or more after the first release = two taps.
    { GestureClassifier g;
      stroke(g, 0, 100, 100, 100, 100, 80);                      // release at 100
      assert(idle(g, 120, 380) == Gesture::None);
      assert(idle(g, 400, 400) == Gesture::Tap);                 // 300 ms after the release
      stroke(g, 420, 100, 100, 100, 100, 80);
      assert(idle(g, 540, 1200) == Gesture::Tap); }
    // A tap followed quickly by a swipe: the tap first, then the swipe.
    { GestureClassifier g;
      stroke(g, 0, 100, 100, 100, 100, 80);                      // release at 100
      stroke(g, 300, 250, 120, 100, 120, 200);                   // pressed 200 ms after, swipe left
      assert(g.take(570) == Gesture::None);                      // release at 520, still inside the 60 ms rejoin window
      assert(g.take(580) == Gesture::Tap);
      assert(g.take(580) == Gesture::SwipeLeft);
      assert(g.take(580) == Gesture::None); }
    // A tap then a long hold inside the window: the tap still counts, the hold is nothing.
    { GestureClassifier g;
      stroke(g, 0, 100, 100, 100, 100, 80);
      stroke(g, 250, 100, 100, 100, 100, 900);
      assert(idle(g, 1200, 2000) == Gesture::Tap); }

    // ---- Limits (same numbers as fw/screen.cpp:880-889).
    { GestureClassifier g; g.feed(true, 0, 100, 100); g.feed(false, 20, -1, -1);           // held 20 ms: too short
      assert(idle(g, 40, 1000) == Gesture::None); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 680);                            // held 700 ms: still a tap
      assert(idle(g, 720, 1400) == Gesture::Tap); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 700);                            // held 720 ms: not a tap
      assert(idle(g, 740, 1400) == Gesture::None); }
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 119, 120);                            // moved 19 px: not a tap
      assert(idle(g, 160, 1000) == Gesture::None); }
    { GestureClassifier g; stroke(g, 0, 200, 120, 146, 120, 120);                            // 54 px: not a swipe
      assert(idle(g, 160, 1000) == Gesture::None); }
    { GestureClassifier g; stroke(g, 0, 200, 120, 145, 120, 120);                            // 55 px: swipe left
      assert(idle(g, 160, 1000) == Gesture::SwipeLeft); }
    { GestureClassifier g; stroke(g, 0, 160, 220, 160, 40, 1000);                            // held 1020 ms: too slow
      assert(idle(g, 1040, 2000) == Gesture::None); }
    { GestureClassifier g;                                       // horizontal end point but a 60 px bulge on the way
      g.feed(true, 0, 260, 120); g.feed(true, 40, 220, 160); g.feed(true, 80, 180, 180); g.feed(true, 120, 140, 160);
      g.feed(true, 160, 100, 122); g.feed(false, 180, -1, -1);
      assert(idle(g, 200, 1000) == Gesture::None); }
    { GestureClassifier g; stroke(g, 0, 0, 60, 0, 140, 200);                                  // x = 0 is a real point
      assert(idle(g, 240, 1000) == Gesture::SwipeDown); }

    // ---- Frames that carry no point.
    { GestureClassifier g;                                       // idle release frames do nothing
      assert(idle(g, 0, 5000) == Gesture::None); }
    { GestureClassifier g;                                       // a down frame without a point is not a sample
      g.feed(true, 0, -1, -1); g.feed(true, 20, 100, 100); g.feed(true, 60, -1, -1); g.feed(true, 80, 101, 100);
      g.feed(false, 100, -1, -1);
      assert(idle(g, 120, 1000) == Gesture::Tap); }
    { GestureClassifier g;                                       // only point-less down frames: nothing
      g.feed(true, 0, -1, -1); g.feed(true, 40, -1, -1); g.feed(false, 80, -1, -1);
      assert(idle(g, 100, 1000) == Gesture::None); }

    // ---- reset() (screen off, leaving the home page) drops a pending tap and a stroke in progress.
    { GestureClassifier g; stroke(g, 0, 100, 100, 100, 100, 80); assert(g.take(150) == Gesture::None);
      g.reset(); assert(idle(g, 160, 1000) == Gesture::None); }
    { GestureClassifier g; g.feed(true, 0, 250, 120); g.feed(true, 100, 150, 120); g.reset();
      g.feed(true, 120, 100, 120); g.feed(false, 140, -1, -1);  // the rest of that finger: too short, no swipe
      assert(idle(g, 160, 1000) == Gesture::None); }

    // ---- The millisecond clock wraps (uint32_t, ~49.7 days of uptime).
    { GestureClassifier g; const uint32_t t0 = 0xFFFFFF80u;      // wraps during the stroke
      stroke(g, t0, 250, 120, 100, 120, 240);
      Gesture r = Gesture::None;
      for (uint32_t i = 0; i < 60 && r == Gesture::None; ++i) r = g.take(t0 + 260 + i * 10);
      assert(r == Gesture::SwipeLeft); }
    { GestureClassifier g; const uint32_t t0 = 0xFFFFFF00u;      // wraps inside the double-tap window
      stroke(g, t0, 100, 100, 100, 100, 100);
      stroke(g, t0 + 320, 100, 100, 100, 100, 80);
      Gesture r = Gesture::None;
      for (uint32_t i = 0; i < 60 && r == Gesture::None; ++i) r = g.take(t0 + 420 + i * 10);
      assert(r == Gesture::DoubleTap); }
    return 0;
}
