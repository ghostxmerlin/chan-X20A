#include "../upstream/StackChan/firmware/main/collector/web_control.h"
#include <cassert>
#include <cstring>
using namespace collector;
int main() {
    assert(manual_yaw_idle(false, false)); // passive init: no trajectory was commanded
    assert(!manual_yaw_idle(true, false)); // physical/web command is still running
    assert(manual_yaw_idle(true, true));
    ManualYaw q;
    assert(!q.submit(60, true, true, true, 0, 100, 100));
    assert(!q.submit(50, false, true, true, 0, 100, 100));
    assert(!q.submit(50, true, false, true, 0, 100, 100));
    assert(!q.submit(50, true, true, false, 0, 100, 100));
    assert(!q.submit(50, true, true, true, 0, 1, 1000002));
    assert(!q.submit(50, true, true, true, 1250, 100, 100));
    assert(q.submit(50, true, true, true, 100, 100, 100));
    assert(q.target == 150 && q.sequence == 1);
    assert(!q.submit(-50, true, true, true, 100, 100, 100));
    assert(!q.take(1000101)); assert(!q.pending); assert(!strcmp(q.state, "expired"));
    assert(q.submit(-50, true, true, true, 100, 2000000, 2000000));
    q.cancel(); assert(!q.take(2000100));
    assert(q.submit(-50, true, true, true, 100, 3000000, 3000000));
    assert(q.take(3000100)); assert(q.target == 50); assert(!q.take(3000200));
    q.finish(false); assert(!strcmp(q.state, "feedback_unavailable"));
    ManualYaw race;
    assert(race.submit(50, true, true, true, 0, 100, 100));
    // Physical input starts motion after admission, before the motor task consumes.
    assert(!race.take(110, false));
    assert(!race.pending); assert(!strcmp(race.state, "superseded"));
    assert(!race.take(120, true)); // never replay after physical movement settles
    assert(race.submit(50, true, true, true, 0, 200, 200));
    assert(race.take(210, true)); race.finish(true);
    assert(!race.submit(50, true, true, true, 0, 220, 220));
    race.complete(); assert(!strcmp(race.state, "trajectory_complete"));
    assert(race.submit(-50, true, true, true, 50, 300, 300));
    assert(race.take(310, true)); race.finish(true); race.cancel();
    assert(!strcmp(race.state, "cancelled"));
    assert(next_view_request(0, true) == 3);
    assert(next_view_request(3, false) == 4);
    assert(next_view_request(4, true) == 7); // quick off/on must remain a new request
}
