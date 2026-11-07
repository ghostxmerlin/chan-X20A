/* SPDX-License-Identifier: MIT */
#pragma once

namespace collector {
// Command interpolation only, not the physical servo PID or a payload rating.
// Critical damping with unit software mass: slower starts and stops. Shared by
// boot, saved-angle and remote commands; yaw retains its original speed mapping.
inline constexpr float kPitchMotionStiffness = 2.25f;
inline constexpr float kPitchMotionDamping = 3.0f;
inline constexpr const char* kPitchMotionProfile = "gentle_load";
template<class Servo> void configure_pitch_motion(Servo& servo)
{
    servo.setSpeedSpringOverride(kPitchMotionStiffness, kPitchMotionDamping);
}
}
