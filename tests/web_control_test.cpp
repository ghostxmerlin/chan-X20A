#include "../upstream/StackChan/firmware/main/collector/web_control.h"
#include <cassert>
#include <cstring>
using namespace collector;
int main() {
    assert(manual_yaw_idle(false, false)); // passive init: no trajectory was commanded
    assert(!manual_yaw_idle(true, false)); // physical/web command is still running
    assert(manual_yaw_idle(true, true));
    assert(web_yaw_slot(0) == 0 && web_yaw_slot(224) == 0 && web_yaw_slot(225) == 1 && web_yaw_slot(674) == 1 && web_yaw_slot(675) == 2);
    assert(web_yaw_slot(-224) == 0 && web_yaw_slot(-225) == -1 && web_yaw_slot(-855) == -2);
    ManualYaw q;
    assert(q.speed == 1000 && kWebYawStepTenths == 450 && kWebYawSlots == 2 && !q.remote_like);
    assert(q.submit_step(450, 1000, false, true, true, true, 405, 100, 100) && q.target == 900 && q.sequence == 1);
    q.cancel();
    assert(q.submit_step(-450, 1000, false, true, true, true, 405, 200, 200) && q.target == 0);
    q.cancel();
    assert(!q.submit_step(450, 1000, false, true, true, true, 855, 300, 300)); // last slot
    assert(q.submit_step(-450, 1000, false, true, true, true, 855, 300, 300) && q.target == 450);
    q.cancel();
    assert(!q.submit_step(0, 1000, false, true, true, true, 0, 400, 400));
    assert(q.submit_slot(0, 1000, false, true, true, true, 855, 400, 400) && q.target == 0);
    q.cancel();
    assert(!q.submit_slot(3, 1000, false, true, true, true, 0, 500, 500));
    assert(!q.submit_slot(-3, 1000, false, true, true, true, 0, 500, 500));
    // Diagnostic relative steps keep whole-degree and range checks.
    assert(!q.submit(0, kWebYawDefaultSpeed, false, true, true, true, 0, 600, 600));
    assert(!q.submit(55, kWebYawDefaultSpeed, false, true, true, true, 0, 600, 600));
    assert(!q.submit(310, kWebYawDefaultSpeed, false, true, true, true, 0, 600, 600));
    assert(!q.submit(300, kWebYawDefaultSpeed, false, true, true, true, 700, 600, 600)); // would leave the ±90° range
    assert(!q.submit(50, kWebYawMinSpeed - 1, false, true, true, true, 0, 600, 600));
    assert(!q.submit(50, kWebYawMaxSpeed + 1, false, true, true, true, 0, 600, 600));
    assert(!q.submit(50, kWebYawDefaultSpeed, false, false, true, true, 0, 600, 600));
    assert(!q.submit(50, kWebYawDefaultSpeed, false, true, false, true, 0, 600, 600));
    assert(!q.submit(50, kWebYawDefaultSpeed, false, true, true, false, 0, 600, 600));
    assert(!q.submit(50, kWebYawDefaultSpeed, false, true, true, true, 0, 1, 1000002));
    assert(!q.submit(50, kWebYawDefaultSpeed, false, true, true, true, 1250, 600, 600));
    assert(q.submit(300, kWebYawDefaultSpeed, true, true, true, true, 0, 600, 600) && q.remote_like && q.target == 300);
    q.pending = false; q.state = "idle"; q.sequence = 0; q.remote_like = false; // reset the probe
    assert(q.submit(-60, 100, false, true, true, true, 0, 700, 700) && !q.remote_like && q.target == -60 && q.speed == 100 && q.sequence == 1);
    q.pending = false; q.state = "idle"; q.sequence = 0;
    assert(q.submit(50, kWebYawDefaultSpeed, false, true, true, true, 100, 100, 100));
    assert(q.target == 150 && q.sequence == 1);
    assert(!q.submit(-50, kWebYawDefaultSpeed, false, true, true, true, 100, 100, 100));
    assert(!q.take(1000101)); assert(!q.pending); assert(!strcmp(q.state, "expired"));
    assert(q.submit(-50, kWebYawDefaultSpeed, false, true, true, true, 100, 2000000, 2000000));
    q.cancel(); assert(!q.take(2000100));
    assert(q.submit(-50, kWebYawDefaultSpeed, false, true, true, true, 100, 3000000, 3000000));
    assert(q.take(3000100)); assert(q.target == 50); assert(!q.take(3000200));
    q.finish(false); assert(!strcmp(q.state, "feedback_unavailable"));
    ManualYaw race;
    assert(race.submit(50, kWebYawDefaultSpeed, false, true, true, true, 0, 100, 100));
    // Physical input starts motion after admission, before the motor task consumes.
    assert(!race.take(110, false));
    assert(!race.pending); assert(!strcmp(race.state, "superseded"));
    assert(!race.take(120, true)); // never replay after physical movement settles
    assert(race.submit(50, kWebYawDefaultSpeed, false, true, true, true, 0, 200, 200));
    assert(race.take(210, true)); race.finish(true);
    assert(!race.submit(50, kWebYawDefaultSpeed, false, true, true, true, 0, 220, 220));
    race.complete(); assert(!strcmp(race.state, "trajectory_complete"));
    assert(race.submit(-50, kWebYawDefaultSpeed, false, true, true, true, 50, 300, 300));
    assert(race.take(310, true)); race.finish(true); race.cancel();
    assert(!strcmp(race.state, "cancelled"));
}
