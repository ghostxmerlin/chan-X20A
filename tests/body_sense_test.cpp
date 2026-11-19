// Body sense (collector/body_sense.h, collector-0.5.12): stillness, picked_up after stillness, put_down after quiet,
// the robot's own motion ignored, shaken with its cool-down, near / far with the creeping baseline, the summary window.
#include "body_sense.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
using namespace collector::body;

static const int64_t kStep = 20000;   // 50 Hz

static void still(Sense& s, int64_t& t, int64_t us, bool self = false)
{
    for (int64_t end = t + us; t < end; t += kStep) s.imu(0, 0, kG, 0.5f, 0, 0, t, self);
}
static void wobble(Sense& s, int64_t& t, int64_t us, float g = 0.15f, float dps = 40, bool self = false)
{
    for (int64_t end = t + us, i = 0; t < end; t += kStep, ++i) s.imu(0, 0, kG * (1 + (i % 2 ? g : -g)), dps, 0, 0, t, self);
}
static int drain(Sense& s, Ev* out, int cap)
{
    int n = 0; Event e;
    while (s.take_event(e)) if (n < cap) out[n++] = e.ev;
    return n;
}

int main()
{
    Ev ev[16];
    {   // lifted after 3 s on the desk, carried 2 s, put down: picked_up, then put_down 2.5 s after the last motion
        Sense s; int64_t t = 1000000;
        still(s, t, 3000000);
        assert(drain(s, ev, 16) == 0);
        wobble(s, t, 500000);
        assert(s.pending() == 0 && !s.held());                       // 0.5 s is not a run yet
        wobble(s, t, 1500000);
        assert(s.held() && drain(s, ev, 16) == 1 && ev[0] == Ev::picked_up);
        still(s, t, 2000000);
        assert(s.held() && s.pending() == 0);
        still(s, t, 1000000);
        Event e; assert(s.take_event(e) && e.ev == Ev::put_down && !s.held());
        assert(e.held_ms >= 4000 && e.held_ms <= 4700);
    }
    {   // the head turning by itself: no event, and it does not start the stillness clock over as motion would
        Sense s; int64_t t = 1000000;
        still(s, t, 3000000);
        wobble(s, t, 2000000, 0.2f, 80, true);
        assert(s.pending() == 0 && !s.held());
    }
    {   // a bump without stillness before it: moving all along is not "picked up"
        Sense s; int64_t t = 1000000;
        still(s, t, 1000000);
        wobble(s, t, 2000000);
        assert(s.pending() == 0 && !s.held());
    }
    {   // shaken: strong samples, once per cool-down
        Sense s; int64_t t = 1000000;
        still(s, t, 500000);
        wobble(s, t, 400000, 0.9f, 300);
        int n = drain(s, ev, 16), shakes = 0;
        for (int i = 0; i < n; ++i) shakes += ev[i] == Ev::shaken;
        assert(shakes == 1);
        wobble(s, t, 1000000, 0.9f, 300);
        n = drain(s, ev, 16); shakes = 0;
        for (int i = 0; i < n; ++i) shakes += ev[i] == Ev::shaken;
        assert(shakes == 0);                                          // inside the 3 s cool-down
        wobble(s, t, 2000000, 0.9f, 300);
        n = drain(s, ev, 16); shakes = 0;
        for (int i = 0; i < n; ++i) shakes += ev[i] == Ev::shaken;
        assert(shakes == 1);
    }
    {   // near / far around the baseline (the robot's numbers, 2026-11-19: still 0..4, noise to 7, a hand 10..74)
        Sense s; int64_t t = 0;
        for (int i = 0; i < 20; ++i) s.proximity(i % 5, t += 100000);
        for (int i = 0; i < 10; ++i) s.proximity(7, t += 100000);     // the head's own shadow: noise, no event
        assert(s.pending() == 0);
        for (int i = 0; i < 20; ++i) s.proximity(i % 3, t += 100000);
        s.proximity(14, t += 100000);
        assert(s.pending() == 0);                                     // one reading is not enough
        s.proximity(18, t += 100000);
        Event e; assert(s.take_event(e) && e.ev == Ev::near && e.prox == 18);
        for (int i = 0; i < 4; ++i) s.proximity(3, t += 100000);
        assert(s.pending() == 0);
        s.proximity(3, t += 100000);
        assert(s.take_event(e) && e.ev == Ev::far);
        for (int i = 0; i < 100; ++i) s.proximity(i, t += 100000);    // a slowly brighter background: baseline creeps
        assert(s.pending() == 0);
    }
    {   // the summary: once a second, gravity angles, still, the latest optical readings
        Sense s; int64_t t = 1000000;
        Summary m;
        assert(!s.take_summary(t, m));                                // starts the window
        for (int64_t end = t + 2000000; t < end; t += kStep) s.imu(0, kG * 0.5f, kG * 0.866f, 0, 0, 0, t, false);
        s.proximity(70, t - 500000); s.proximity(55, t); s.light(123.5f);
        const float ut[3] = {10, -20, 30}; s.magnetic(ut);
        assert(s.take_summary(t, m));
        assert(m.seq == 1 && m.imu && m.samples == 100 && m.still && !m.held && !m.self_moving);
        assert(std::fabs(m.roll_deg - 30) < 0.5f && std::fabs(m.pitch_deg) < 0.5f && std::fabs(m.acc_g - 1) < 0.01f);
        assert(m.prox == 55 && m.prox_max == 70 && m.lux == 123.5f && m.mag && m.mag_ut[1] == -20);
        assert(!s.take_summary(t + 500000, m));
        assert(s.take_summary(t + 1000000, m) && m.seq == 2 && !m.imu && m.samples == 0);
    }
    {   // the queue keeps the newest kEvQueue events
        Sense s; int64_t t = 0;
        for (int i = 0; i < 6; ++i) { s.proximity(0, t += 100000); s.proximity(50, t += 100000); s.proximity(50, t += 100000);
            for (int k = 0; k < 5; ++k) s.proximity(0, t += 100000); }
        assert(s.pending() == kEvQueue && s.dropped() == 4);
    }
    std::puts("body_sense_test ok");
    return 0;
}
