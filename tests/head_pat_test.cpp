// Head pats (owner 2026-11-20, the StackChan's hat off): the Si12T head touch -> tap / double tap / protest.
// A pat = touched, then released within 700 ms; a burst ends 450 ms after its last pat: 1 pat = tap, 2-3 = double
// tap; the 4th pat within 3 s of the burst's first = protest at once, the rest of that burst ignored. A hold is no pat.
#include "../upstream/StackChan/firmware/main/collector/head_pat.h"
#include <cassert>
#include <cstdio>
#include <vector>
using namespace collector::head;

namespace {
constexpr int64_t kMs = 1000;
struct Run {
    Detector d;
    int64_t t = 0;
    std::vector<Gesture> got;
    void step(bool touched, int64_t ms)
    {
        for (int64_t end = t + ms * kMs; t < end; t += 40 * kMs) {
            const Gesture g = d.feed(t, touched);
            if (g != Gesture::None) got.push_back(g);
        }
    }
    void pat(int64_t down_ms = 120, int64_t up_ms = 200) { step(true, down_ms); step(false, up_ms); }
};
}

int main()
{
    {   // one pat: a tap, only once the burst is over (450 ms of nothing)
        Run r; r.step(false, 200); r.pat(120, 200);
        assert(r.got.empty());
        r.step(false, 400);
        assert(r.got.size() == 1 && r.got[0] == Gesture::Tap);
        r.step(false, 2000);
        assert(r.got.size() == 1);
    }
    {   // two pats: a double tap; three: still a double tap
        Run r; r.pat(); r.pat(); r.step(false, 600);
        assert(r.got.size() == 1 && r.got[0] == Gesture::DoubleTap);
        Run t; t.pat(); t.pat(); t.pat(); t.step(false, 600);
        assert(t.got.size() == 1 && t.got[0] == Gesture::DoubleTap);
    }
    {   // four quick pats: a protest at the 4th, the rest of the burst ignored, nothing at its end
        Run r; r.pat(); r.pat(); r.pat(); r.pat();
        assert(r.got.size() == 1 && r.got[0] == Gesture::Protest);
        r.pat(); r.pat(); r.step(false, 1000);
        assert(r.got.size() == 1);
        // a new burst after the quiet gap counts again
        r.pat(); r.step(false, 600);
        assert(r.got.size() == 2 && r.got[1] == Gesture::Tap);
    }
    {   // a hold is not a pat: nothing at all
        Run r; r.step(true, 1500); r.step(false, 1000);
        assert(r.got.empty());
    }
    {   // four pats spread over more than 3 s (each burst separate): taps, no protest
        Run r;
        for (int i = 0; i < 4; ++i) { r.pat(); r.step(false, 500); }
        assert(r.got.size() == 4);
        for (Gesture g : r.got) assert(g == Gesture::Tap);
    }
    {   // disabled: nothing, and a burst in progress is dropped
        Run r; r.pat(); r.d.set_enabled(false); r.step(false, 600); r.pat(); r.step(false, 600);
        assert(r.got.empty() && !r.d.enabled());
        r.d.set_enabled(true); r.pat(); r.step(false, 600);
        assert(r.got.size() == 1 && r.got[0] == Gesture::Tap);
    }
    assert(gesture_name(Gesture::Protest)[0] == 'p');
    std::puts("PASS: head pats (tap, double tap, protest, hold, spread, disabled)");
}
