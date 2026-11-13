// Boot auto-tracking (user, 2026-11-13): host test for collector/boot_tracking.h.
#include "../upstream/StackChan/firmware/main/collector/boot_tracking.h"
#include <cassert>
#include <cstring>
using namespace collector;
using A = BootTracking::Action;
constexpr int64_t s = 1000000;

int main()
{
    {   // M12 online after 3 s: arm, wait for the servos, then track once.
        BootTracking b;
        assert(b.tick(1 * s, false, false) == A::None);
        assert(b.tick(3 * s, true, false) == A::Arm);
        b.armed(true);
        assert(b.tick(3 * s + 100000, true, false) == A::None);
        assert(b.tick(4 * s, true, true) == A::Track);
        b.tracked(true);
        assert(b.done() && !std::strcmp(b.result(), "tracking_on"));
        assert(b.tick(5 * s, true, true) == A::None);         // never again
    }
    {   // Servos already armed: no arm step, track at once.
        BootTracking b;
        assert(b.tick(2 * s, true, true) == A::Track);
        b.tracked(true);
        assert(b.done());
    }
    {   // No M12: give up quietly after 30 s.
        BootTracking b;
        assert(b.tick(29 * s, false, false) == A::None && !b.done());
        assert(b.tick(30 * s, false, false) == A::None && b.done());
        assert(!std::strcmp(b.result(), "no_m12"));
        assert(b.tick(31 * s, true, true) == A::None);         // a late M12 does not start it
    }
    {   // A spoken word first (for example "down"): cancelled, never turns tracking on.
        BootTracking b;
        b.cancel();
        assert(b.tick(3 * s, true, true) == A::None && !std::strcmp(b.result(), "cancelled_by_user"));
    }
    {   // Arm refused (for example servo power fault).
        BootTracking b;
        assert(b.tick(3 * s, true, false) == A::Arm);
        b.armed(false);
        assert(b.done() && !std::strcmp(b.result(), "arm_refused") && b.tick(4 * s, true, true) == A::None);
    }
    {   // Servos never ready: stop after the 10 s window.
        BootTracking b;
        assert(b.tick(3 * s, true, false) == A::Arm);
        b.armed(true);
        assert(b.tick(12 * s, true, false) == A::None && !b.done());
        assert(b.tick(13 * s, true, false) == A::None && !std::strcmp(b.result(), "servos_not_ready"));
    }
    {   // A refused tracking request is retried until it succeeds.
        BootTracking b;
        assert(b.tick(3 * s, true, true) == A::Track);
        b.tracked(false);
        assert(!b.done() && b.tick(3 * s + 100000, true, true) == A::Track);
        b.tracked(true);
        assert(b.done());
    }
    return 0;
}
