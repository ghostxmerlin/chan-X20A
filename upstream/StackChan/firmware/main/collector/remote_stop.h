/* SPDX-License-Identifier: MIT */
#pragma once
#include "startup_pose.h"

namespace collector {
// Access under the receiver mutex. Re-enabling does not clear a pending stop:
// the motor task must observe even a stop/re-enable between two task samples.
class RemoteStopLatch {
public:
    void request() { pending_ = true; }
    bool consume()
    {
        const bool result = pending_;
        pending_ = false;
        return result;
    }
private:
    bool pending_ = false;
};

// Called outside the receiver mutex, before processing post-enable input.
// Keep startup cancellation and both actuator stops in the same transition.
template<class Motion>
void apply_remote_stop(StartupPose& startup, Motion& motion, int64_t now)
{
    startup.step(now, false, false, false);
    motion.yawServo().cancelMotion();
    motion.pitchServo().cancelMotion();
}
}
