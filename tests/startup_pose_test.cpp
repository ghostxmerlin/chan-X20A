#include "../upstream/StackChan/firmware/main/collector/startup_pose.h"
#include <cassert>
#include <cstring>
#include <iostream>
using collector::StartupPose;
using Action = StartupPose::Action;
int main()
{
    StartupPose boot;
    assert(StartupPose::kPitch == 130);
    assert(boot.step(0, true, false, false, true) == Action::None);
    assert(boot.step(100000, true, false, true, true) == Action::Start);
    // Intermittent observation failures do not interrupt an already valid trajectory.
    for (int i = 2; i < 20; ++i) assert(boot.step(i * 100000, true, false, false, false) == Action::Update);
    assert(boot.step(2000000, true, false, false, true) == Action::Update);
    assert(std::strcmp(boot.state(), "trajectory_complete") == 0);
    assert(boot.step(2100000, true, false, true, true) == Action::Update); // no second Start
    assert(boot.step(2200000, false, false, true, false) == Action::None);
    assert(boot.step(2300000, true, false, true, true) == Action::None); // manual re-enable doesn't home
    StartupPose remote;
    assert(remote.step(0, true, true, true, true) == Action::None); // first packet beats boot pose
    assert(remote.step(100000, true, false, true, true) == Action::None);
    StartupPose interrupted;
    assert(interrupted.step(0, true, false, true, true) == Action::Start);
    assert(interrupted.step(100000, true, true, false, false) == Action::None); // remote preempts in-flight
    StartupPose unavailable;
    assert(unavailable.step(9000000, true, false, false, true) == Action::None);
    assert(unavailable.step(14000000, true, false, true, true) == Action::None); // no surprise delayed homing
    assert(std::strcmp(unavailable.state(), "feedback_unavailable") == 0);
    assert(unavailable.step(14100000, true, false, true, true) == Action::None);
    std::cout << "PASS: boot pose, invalid observation, remote preemption, stop/re-enable, deadline and no replay\n";
}
