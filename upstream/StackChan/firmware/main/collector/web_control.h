/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector {
// Passive initialization may leave AnimateValue.done() false without any requested motion.
inline bool manual_yaw_idle(bool command_started, bool trajectory_complete) { return !command_started || trajectory_complete; }
// Owned under the remote mutex. Only motor_task may consume a request.
struct ManualYaw {
    uint32_t sequence = 0;
    int target = 0;
    int64_t requested_us = 0;
    bool pending = false;
    const char* state = "idle";
    bool submit(int delta, bool enabled, bool valid, bool settled, int measured, int64_t stamp, int64_t now) {
        if ((delta != -50 && delta != 50) || !enabled || !valid || !settled || pending ||
            state == moving || stamp <= 0 || now < stamp || now-stamp >= 1000000 ||
            measured < -1280 || measured > 1280 || measured+delta < -1280 || measured+delta > 1280) return false;
        target = measured + delta; requested_us = now; pending = true; ++sequence; state = "queued"; return true;
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
