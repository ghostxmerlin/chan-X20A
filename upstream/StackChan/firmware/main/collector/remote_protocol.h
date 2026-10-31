/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>
#include <algorithm>

namespace collector {
// Official remote/code/main/joystick/joystick_handle.c, 8-byte little-endian payload.
// These are mapped angles, NOT raw joystick/remote-IMU readings. No sender sequence/heartbeat.
struct RemoteCommand {
    uint8_t target_id = 0;
    int16_t yaw = 0, pitch = 0, speed = 0; // angles in 0.1 degree
    bool auxiliary = false;
};
inline bool decode_remote(const uint8_t* bytes, size_t size, RemoteCommand& out)
{
    if (!bytes || size != 8 || bytes[7] > 1) return false;
    auto i16 = [bytes](int i) { return static_cast<int16_t>(uint16_t(bytes[i]) | uint16_t(bytes[i + 1]) << 8); };
    RemoteCommand value{bytes[0], i16(1), i16(3), i16(5), bytes[7] != 0};
    // The official joystick mapping extrapolates beyond its calibration range.
    // Preserve signed input angles for recording; apply mechanical limits only
    // at the actuator boundary (a real controller sent pitch=950).
    if (value.speed < 0 || value.speed > 1000) return false;
    out = value;
    return true;
}
inline bool remote_for_receiver(const RemoteCommand& value, uint8_t receiver)
{
    return value.target_id == 0 || value.target_id == receiver;
}
// Match the factory spring mapping; both official remote modes send speed 600.
constexpr int kRemoteSpeedLimit = 1000;
inline RemoteCommand limit_remote_motion(RemoteCommand value)
{
    value.yaw = std::clamp<int>(value.yaw, -1280, 1280);
    value.pitch = std::clamp<int>(value.pitch, 50, 850);
    value.speed = std::clamp<int>(value.speed, 0, kRemoteSpeedLimit);
    return value;
}
}
