// Nod / shake (fw/head_gesture.h, collector-0.5.10, 0.5.11): the waypoints (clamped, back to the start; one-sided when
// one side has no room), the no-room refusal, legs advancing on a complete trajectory or the leg timeout, the
// overall timeout and cancel.
#include "head_gesture.h"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace collector;

int main()
{
    GesturePlan p;
    assert(plan_gesture(100, 80, 2, 50, 850, p));                            // pitch 10 deg, floor 5 deg, amp 8
    assert(p.count == 5 && p.points[0] == 50 && p.points[1] == 180 && p.points[2] == 50 && p.points[3] == 180 && p.points[4] == 100);
    assert(plan_gesture(0, kShakeAmpTenths, 1, -900, 900, p));               // yaw at the front, one cycle, +-15
    assert(p.count == 3 && p.points[0] == -150 && p.points[1] == 150 && p.points[2] == 0);
    assert(plan_gesture(890, 80, 1, -900, 900, p) && p.count == 2 && p.points[0] == 810 && p.points[1] == 890);   // at the edge: one side
    // 2026-11-19: the saved pitch on its 5 deg floor (head at 3.7): no room below -> up 12 and back, twice
    assert(plan_gesture(37, kNodAmpTenths, 2, 50, 850, p));
    assert(p.count == 4 && p.points[0] == 170 && p.points[1] == 50 && p.points[2] == 170 && p.points[3] == 50);
    assert(plan_gesture(100, kNodAmpTenths, 1, 50, 850, p));                 // 10 deg: 5 of room below < 6 -> one-sided
    assert(p.count == 2 && p.points[0] == 220 && p.points[1] == 100);
    assert(plan_gesture(850, 80, 1, 50, 850, p) && p.count == 2 && p.points[0] == 770 && p.points[1] == 850);   // top: down and back
    assert(!plan_gesture(20, 80, 1, 50, 100, p));                            // 5..10 deg: no room
    assert(!plan_gesture(0, 80, 3, -900, 900, p) && !plan_gesture(0, 80, 0, -900, 900, p) && !plan_gesture(0, 0, 1, -900, 900, p));

    constexpr int64_t ms = 1000;
    {
        HeadGesture g;
        assert(plan_gesture(0, 80, 1, -900, 900, p));
        g.begin(p, 1000 * ms);
        int t = 0;
        assert(g.active() && !std::strcmp(g.result(), "running"));
        assert(g.step(1000 * ms, true, t) == HeadGesture::Action::Move && t == -80);   // first leg at once
        assert(g.step(1020 * ms, false, t) == HeadGesture::Action::None);
        assert(g.step(1500 * ms, true, t) == HeadGesture::Action::Move && t == 80);    // trajectory complete: next
        assert(g.step(1520 * ms, false, t) == HeadGesture::Action::None);
        assert(g.step(1500 * ms + HeadGesture::kLegUs + 1, false, t) == HeadGesture::Action::Move && t == 0);   // leg timeout
        assert(g.step(3100 * ms, false, t) == HeadGesture::Action::None);
        assert(g.step(3200 * ms, true, t) == HeadGesture::Action::Done && !g.active() && !std::strcmp(g.result(), "done"));
        assert(g.legs_done() == 3 && g.step(3300 * ms, true, t) == HeadGesture::Action::None);
    }
    {
        HeadGesture g;
        assert(plan_gesture(0, 80, 2, -900, 900, p));
        g.begin(p, 0);
        int t = 0;
        assert(g.step(0, true, t) == HeadGesture::Action::Move);
        assert(g.step(HeadGesture::kMaxUs + 1, false, t) == HeadGesture::Action::Done && !std::strcmp(g.result(), "timeout"));
        g.begin(p, 0);
        assert(g.step(0, true, t) == HeadGesture::Action::Move);
        g.cancel("superseded");
        assert(!g.active() && !std::strcmp(g.result(), "superseded") && g.step(10, true, t) == HeadGesture::Action::None);
        g.cancel("again");                                                    // nothing running: unchanged
        assert(!std::strcmp(g.result(), "superseded"));
    }
    std::puts("head_gesture ok");
    return 0;
}
