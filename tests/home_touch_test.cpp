// Home page touch input (fw/home_touch.h, review I1 2026-11-14): a failed touch read in the middle of a stroke is a
// release the classifier may rejoin, never a cancel that drops the rest of the stroke. Frames are 20 ms apart, as the
// LVGL timer reads them (fw/collector.cpp: screen_touch on a fresh read, screen_cancel_touch otherwise).
#include "home_touch.h"
#include <cassert>
#include <cstdio>
using namespace collector::touch;

namespace {
// Runs frames: 'd' = down at the next point, 'l' = lost read, 'u' = up (no finger); returns the gestures seen.
struct Run {
    HomeTouch h;
    uint32_t t = 1000;
    int n = 0, taps = 0, lefts = 0, others = 0;
    void take() { for (Gesture g; (g = h.take(t)) != Gesture::None;) (g == Gesture::Tap ? taps : g == Gesture::SwipeLeft ? lefts : others)++; }
    void frames(const char* script, int x0, int dx)
    {
        int x = x0;
        for (const char* c = script; *c; ++c, t += 20) {
            if (*c == 'd') { h.sample(true, t, x, 120); x += dx; }
            else if (*c == 'l') h.lost();
            else h.sample(false, t, -1, -1);
            take();
        }
    }
    void idle(int ms) { for (int i = 0; i < ms; i += 20, t += 20) { h.sample(false, t, -1, -1); take(); } }
};

// What screen.cpp did before the fix: after a lost read mid-stroke, every down frame was dropped until a release.
struct OldRun : Run {
    bool cancelled = false;
    void frames_old(const char* script, int x0, int dx)
    {
        int x = x0;
        for (const char* c = script; *c; ++c, t += 20) {
            if (*c == 'd') { if (!cancelled) h.sample(true, t, x, 120); x += dx; }
            else if (*c == 'l') { h.lost(); cancelled = true; }
            else { cancelled = false; h.sample(false, t, -1, -1); }
            take();
        }
    }
};
}

int main()
{
    // A left swipe of 9 frames x 12 px = 108 px with one failed read after the 3rd frame: still one left swipe.
    { Run r; r.frames("dddldddddduu", 200, -12); r.idle(400); assert(r.lefts == 1 && r.taps == 0 && r.others == 0); }
    // Two failed reads in a row: the next real frame is 60 ms after the last one, the end of the rejoin window
    // (C3: 60 ms). The swipe is lost (both halves are too short), but nothing turns into a tap.
    { Run r; r.frames("dddllddddduu", 200, -12); r.idle(400); assert(r.lefts == 0 && r.taps == 0); }
    // The same stroke without a glitch, for reference.
    { Run r; r.frames("ddddddddduu", 200, -12); r.idle(400); assert(r.lefts == 1 && r.taps == 0); }
    // The bug this guards against: a swipe that starts slowly (3 frames, 2 px) and has a failed read before it speeds
    // up. The old cancel dropped the rest of the stroke and the slow start alone became a tap 300 ms later.
    {
        const int xs[] = {200, 199, 198, -1, 186, 170, 154, 138, 122, 106};   // -1 = lost read
        OldRun o; Run r;
        for (int x : xs) {
            if (x < 0) { o.h.lost(); o.cancelled = true; r.h.lost(); }
            else { if (!o.cancelled) o.h.sample(true, o.t, x, 120); r.h.sample(true, r.t, x, 120); }
            o.t += 20; r.t += 20; o.take(); r.take();
        }
        o.frames_old("uu", 0, 0); r.frames("uu", 0, 0);
        o.idle(400); r.idle(400);
        assert(o.taps == 1 && o.lefts == 0);             // before the fix: a phantom tap (in TALK: say a round)
        assert(r.taps == 0 && r.lefts == 1);             // now: the left swipe it was
    }
    { Run r; r.frames("dddldddddduu", 200, -2); r.idle(400); assert(r.taps == 1); }   // a slow 18 px press IS a tap
    // A real tap with a failed read in it stays one tap; a lost read with no stroke changes nothing.
    { Run r; r.frames("ddlduu", 160, 0); r.idle(400); assert(r.taps == 1 && r.lefts == 0 && r.others == 0); }
    { Run r; r.frames("llluuu", 160, 0); r.idle(400); assert(r.taps == 0 && r.lefts == 0 && r.others == 0); }
    // A read that stays lost past the rejoin window ends the stroke as it was (here too short for anything... a
    // 3-frame press of 0 px is a tap), and the finger seen again later starts a new stroke.
    { Run r; r.frames("dddlllllduu", 160, 0); r.idle(400); assert(r.taps >= 1 && r.lefts == 0); }
    // reset() (leaving home, screen off) drops a pending tap.
    { Run r; r.frames("dddduu", 160, 0); r.h.reset(); r.idle(400); assert(r.taps == 0); }
    std::puts("home touch ok");
}
