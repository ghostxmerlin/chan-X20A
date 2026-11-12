/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector {
// Passive initialization may leave AnimateValue.done() false without any requested motion.
inline bool manual_yaw_idle(bool command_started, bool trajectory_complete) { return !command_started || trajectory_complete; }
// Web yaw moves in 45-degree slots around center, two per side (±90°), at
// the fastest spring mapping: the SCS0009 stalls at 4-5° of error under this
// head, so small precise steps are pointless while larger swings move reliably.
constexpr int kWebYawDefaultSpeed = 1000, kWebYawMinSpeed = 50, kWebYawMaxSpeed = 1000;
constexpr int kWebYawStepTenths = 450, kWebYawSlots = 2;
constexpr int kWebYawRangeTenths = kWebYawSlots * kWebYawStepTenths;
// Whole-degree relative steps up to ±30° remain for diagnostics (same range clamp).
constexpr int kWebYawMaxDeltaTenths = 300;
// Yaw release policy: arrival within 6° counts (typical 4-5° shortfall), hold at most 1.5 s.
constexpr uint32_t kWebYawReleaseMinHoldMs = 500, kWebYawReleaseMaxHoldMs = 1500;
constexpr int kWebYawReleaseToleranceTenths = 60;
// Nearest slot index for a measured angle (tenths); ties round away from zero.
inline int web_yaw_slot(int measured)
{
    const int magnitude = (measured < 0 ? -measured : measured) + kWebYawStepTenths / 2;
    const int slot = magnitude / kWebYawStepTenths;
    return measured < 0 ? -slot : slot;
}
// Owned under the remote mutex. Only motor_task may consume a request.
struct ManualYaw {
    uint32_t sequence = 0;
    int target = 0, speed = kWebYawDefaultSpeed;
    int64_t requested_us = 0;
    bool pending = false;
    // remote_like replays the former physical remote's call sequence
    // (setAutoAngleSyncEnabled(true) + moveWithSpeed) instead of the
    // feedback-reset web path; same servo writes otherwise.
    bool remote_like = false;
    const char* state = "idle";
    // Absolute target within the ±90° web range; all entry points funnel here.
    bool submit_target(int target_angle, int requested_speed, bool remote, bool enabled, bool valid, bool settled, int measured, int64_t stamp, int64_t now) {
        if (target_angle < -kWebYawRangeTenths || target_angle > kWebYawRangeTenths ||
            requested_speed < kWebYawMinSpeed || requested_speed > kWebYawMaxSpeed ||
            !enabled || !valid || !settled || pending ||
            state == moving || stamp <= 0 || now < stamp || now-stamp >= 1000000 ||
            measured < -1280 || measured > 1280) return false;
        target = target_angle; speed = requested_speed; remote_like = remote; requested_us = now; pending = true; ++sequence; state = "queued"; return true;
    }
    // Diagnostic relative step in whole degrees.
    bool submit(int delta, int requested_speed, bool remote, bool enabled, bool valid, bool settled, int measured, int64_t stamp, int64_t now) {
        if (delta == 0 || delta % 10 != 0 || delta < -kWebYawMaxDeltaTenths || delta > kWebYawMaxDeltaTenths) return false;
        return submit_target(measured + delta, requested_speed, remote, enabled, valid, settled, measured, stamp, now);
    }
    // Page button: one slot from the slot nearest to the measured angle; false at the range end.
    bool submit_step(int direction, int requested_speed, bool remote, bool enabled, bool valid, bool settled, int measured, int64_t stamp, int64_t now) {
        if (direction == 0 || !valid) return false;
        const int slot = web_yaw_slot(measured) + (direction > 0 ? 1 : -1);
        if (slot < -kWebYawSlots || slot > kWebYawSlots) return false;
        return submit_target(slot * kWebYawStepTenths, requested_speed, remote, enabled, valid, settled, measured, stamp, now);
    }
    bool submit_slot(int slot, int requested_speed, bool remote, bool enabled, bool valid, bool settled, int measured, int64_t stamp, int64_t now) {
        if (slot < -kWebYawSlots || slot > kWebYawSlots) return false;
        return submit_target(slot * kWebYawStepTenths, requested_speed, remote, enabled, valid, settled, measured, stamp, now);
    }
    bool take(int64_t now, bool settled = true) {
        if (!pending) return false;
        pending = false;
        if (!settled) { state = "superseded"; return false; }
        if (now < requested_us || now-requested_us >= 1000000) { state = "expired"; return false; }
        return true;
    }
    void finish(bool accepted) { state = accepted ? moving : "feedback_unavailable"; }
    void cancel() { if (pending || state == moving) state = "cancelled"; pending = false; }
    void complete() { if (state == moving) state = "trajectory_complete"; }
    static constexpr const char* moving = "moving";
};
}
