/* SPDX-License-Identifier: MIT */
#pragma once
#include "remote_protocol.h"

namespace collector {
constexpr bool kRemotePitchControlEnabled = false;
struct RemoteYawTarget { int yaw, speed; };

// Deliberately accepts only the horizontal actuator. The recorded pitch field
// cannot reach a pitch servo through this path, in either official remote mode.
template<class YawServo>
RemoteYawTarget apply_remote_yaw(YawServo& yaw_servo, const RemoteCommand& input, bool sync_start)
{
    const RemoteYawTarget target{std::clamp<int>(input.yaw, -1280, 1280),
                                 std::clamp<int>(input.speed, 0, kRemoteSpeedLimit)};
    yaw_servo.setAutoAngleSyncEnabled(sync_start);
    yaw_servo.moveWithSpeed(target.yaw, target.speed);
    return target;
}
}
