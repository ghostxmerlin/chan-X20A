/* SPDX-License-Identifier: MIT */
#pragma once
#include "remote_protocol.h"
#include "pitch_setting.h"

namespace collector {
constexpr bool kRemotePitchControlEnabled = false; // Loaded head: physical up/down temporarily blocked.
struct RemoteYawTarget { int yaw, speed; };
struct RemotePitchTarget { int pitch, speed; };
// Current sender: (Y_CENTER 1960 - Y_MIN 310) * 900 / (Y_MAX 3460 - Y_MIN 310).
// Keep raw telemetry intact. A small tolerance also absorbs sender quantization.
constexpr int kRemotePitchCenter = 471, kRemotePitchCenterTolerance = 5;
inline int remote_pitch_input(int raw)
{
    raw = std::clamp(raw, 0, 900);
    return raw >= kRemotePitchCenter - kRemotePitchCenterTolerance &&
           raw <= kRemotePitchCenter + kRemotePitchCenterTolerance ? kRemotePitchCenter : raw;
}
inline RemotePitchTarget remote_pitch_target(const RemoteCommand& input, int neutral = kPitchSettingDefault)
{
    int target = std::clamp<int>(input.pitch, kPitchSettingMinimum, kPitchSettingMaximum);
    if (input.center_v1) {
        neutral = std::clamp(neutral, kPitchSettingMinimum, kPitchSettingMaximum);
        const int raw = remote_pitch_input(input.pitch);
        const int low = kRemotePitchCenter - kRemotePitchCenterTolerance;
        const int high = kRemotePitchCenter + kRemotePitchCenterTolerance;
        target = raw == kRemotePitchCenter ? neutral : raw > high ?
            neutral + (raw - high) * (kPitchSettingMaximum - neutral) / (900 - high) :
            neutral - (low - raw) * (neutral - kPitchSettingMinimum) / low;
    }
    return {target, std::clamp<int>(input.speed, 0, kRemoteSpeedLimit)};
}

// Motor-task owned. Unchanged center-protocol heartbeats must not undo a saved
// pose. The first heartbeat establishes a baseline; a vertical change takes over.
class RemotePitchControl {
public:
    bool accept(const RemoteCommand& input, bool enabled, int64_t packet_us, int64_t setting_us)
    {
        enabled = enabled && kRemotePitchControlEnabled;
        // Compare input, not mapped targets: a saved-neutral change must not
        // turn the next unchanged heartbeat into a new physical command.
        const RemotePitchTarget target{input.center_v1 ? remote_pitch_input(input.pitch) :
            std::clamp<int>(input.pitch, kPitchSettingMinimum, kPitchSettingMaximum),
            std::clamp<int>(input.speed, 0, kRemoteSpeedLimit)};
        const bool changed = !input.center_v1 || (seen_ &&
            (target.pitch != last_.pitch || (active_ && target.speed != last_.speed)));
        seen_ = true; last_ = target;
        if (!enabled) active_ = false;
        if (!changed || !enabled || packet_us <= setting_us) return false;
        active_ = true; return true;
    }
    void release() { active_ = false; }
    bool active() const { return active_; }
private:
    bool seen_ = false, active_ = false;
    RemotePitchTarget last_{};
};

template<class PitchServo>
RemotePitchTarget apply_remote_pitch(PitchServo& servo, const RemoteCommand& input, bool sync_start, int neutral = kPitchSettingDefault)
{
    const auto target = remote_pitch_target(input, neutral);
    servo.setAutoAngleSyncEnabled(sync_start);
    servo.moveWithSpeed(target.pitch, target.speed);
    return target;
}

// Robot-facing coordinates: reverse the factory remote's observer-facing yaw.
// Widen before negating so INT16_MIN is safe. Keep raw packets unchanged.
inline RemoteYawTarget remote_yaw_target(const RemoteCommand& input)
{
    return {std::clamp(-static_cast<int>(input.yaw), -1280, 1280),
            std::clamp<int>(input.speed, 0, kRemoteSpeedLimit)};
}

// Each axis has its own arbitration so a heartbeat cannot undo a web setting.
template<class YawServo>
RemoteYawTarget apply_remote_yaw(YawServo& yaw_servo, const RemoteCommand& input, bool sync_start)
{
    const auto target = remote_yaw_target(input);
    yaw_servo.setAutoAngleSyncEnabled(sync_start);
    yaw_servo.moveWithSpeed(target.yaw, target.speed);
    return target;
}
}
