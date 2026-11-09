#include "../upstream/StackChan/firmware/main/collector/web_control.h"
#include <cassert>
#include <cstring>
using namespace collector;
int main() {
    assert(manual_yaw_idle(false, false)); // passive init: no trajectory was commanded
    assert(!manual_yaw_idle(true, false)); // physical/web command is still running
    assert(manual_yaw_idle(true, true));
    assert(web_yaw_slot(0) == 0 && web_yaw_slot(99) == 0 && web_yaw_slot(100) == 1 && web_yaw_slot(155) == 1 && web_yaw_slot(299) == 1 && web_yaw_slot(300) == 2);
    assert(web_yaw_slot(-99) == 0 && web_yaw_slot(-100) == -1 && web_yaw_slot(-455) == -2 && web_yaw_slot(-700) == -4);
    ManualYaw q;
    assert(q.speed == kWebYawDefaultSpeed && kWebYawDefaultSpeed == 1000 && kWebYawStepTenths == 200 && kWebYawSlots == 3 && !q.remote_like);
    // Button steps: one 20-degree slot from the nearest slot of the measured angle.
    assert(q.submit_step(200, kWebYawDefaultSpeed, false, true, true, true, 155, 100, 100) && q.target == 400 && q.sequence == 1);
    q.pending = false; q.state = "idle";
    assert(q.submit_step(-200, kWebYawDefaultSpeed, false, true, true, true, 155, 200, 200) && q.target == 0);
    q.pending = false; q.state = "idle";
    assert(!q.submit_step(200, kWebYawDefaultSpeed, false, true, true, true, 555, 300, 300)); // 3rd slot reached: no 4th
    assert(q.submit_step(-200, kWebYawDefaultSpeed, false, true, true, true, 555, 300, 300) && q.target == 400);
    q.pending = false; q.state = "idle";
    assert(!q.submit_step(0, kWebYawDefaultSpeed, false, true, true, true, 0, 400, 400));
    assert(q.submit_slot(0, kWebYawDefaultSpeed, false, true, true, true, 555, 400, 400) && q.target == 0);
    q.pending = false; q.state = "idle";
    assert(!q.submit_slot(4, kWebYawDefaultSpeed, false, true, true, true, 0, 500, 500));
    assert(!q.submit_slot(-4, kWebYawDefaultSpeed, false, true, true, true, 0, 500, 500));
    // Diagnostic relative steps keep whole-degree and range checks.
    assert(!q.submit(0, kWebYawDefaultSpeed, false, true, true, true, 0, 600, 600));
    assert(!q.submit(55, kWebYawDefaultSpeed, false, true, true, true, 0, 600, 600));
    assert(!q.submit(310, kWebYawDefaultSpeed, false, true, true, true, 0, 600, 600));
    assert(!q.submit(300, kWebYawDefaultSpeed, false, true, true, true, 400, 600, 600)); // would leave the ±60° range
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
