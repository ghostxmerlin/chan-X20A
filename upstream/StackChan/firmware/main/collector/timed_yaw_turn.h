/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector {
// Only the existing motor task calls tick; requests never touch UART. Time is
// measured on the device, so a closed page or unplugged USB cannot prolong a turn.
struct TimedYawTurn {
    static constexpr int kPwm = 400, kDefaultMs = 1200, kMinMs = 100, kMaxMs = 2000;
    enum class Phase { Idle, Queued, Rotating, Stopping, Complete, Cancelled, Expired, Failed, PowerCut };
    Phase phase = Phase::Idle;
    uint32_t sequence = 0;
    int duration_ms = kDefaultMs, direction = 1, next_direction = 1;
    int64_t requested_us = 0, started_us = 0, last_stop_us = 0, stopping_us = 0;
    bool cancel_requested = false, failed = false;
    bool busy() const { return phase == Phase::Queued || phase == Phase::Rotating || phase == Phase::Stopping || phase == Phase::PowerCut; }
    bool submit(int ms, bool allowed, int64_t now) {
        if (!allowed || busy() || ms < kMinMs || ms > kMaxMs) return false;
        duration_ms = ms; requested_us = now; started_us = last_stop_us = stopping_us = 0;
        cancel_requested = failed = false; direction = next_direction; ++sequence; phase = Phase::Queued; return true;
    }
    void cancel() { if (busy()) cancel_requested = true; }
    template<class Servo> void tick(Servo& servo, int64_t now, bool enabled) {
        if (!busy()) return;
        if (phase == Phase::PowerCut) {
            if (now - last_stop_us >= 200000) { servo.cutRotationPower(); last_stop_us = now; }
            return; // latched: only a reboot may enable VM again
        }
        if (phase == Phase::Queued) {
            if (!enabled || cancel_requested) { phase = Phase::Cancelled; return; }
            if (now < requested_us || now - requested_us >= 1000000) { phase = Phase::Expired; return; }
            // Cancels the old spring before changing modes. No update() until a
            // subsequent explicit position request; stopping never writes a goal.
            servo.cancelMotion();
            started_us = now; next_direction = -direction;
            if (servo.beginTimedRotation(direction * kPwm)) phase = Phase::Rotating;
            else { failed = true; phase = Phase::Stopping; }
        }
        if (phase == Phase::Rotating && (!enabled || cancel_requested || now < started_us || now - started_us >= int64_t(duration_ms) * 1000))
            phase = Phase::Stopping;
        if (phase == Phase::Stopping && (!last_stop_us || now - last_stop_us >= 20000)) {
            if (!stopping_us) stopping_us = now;
            if (now - stopping_us >= 300000) {
                servo.cutRotationPower(); phase = Phase::PowerCut; last_stop_us = now; return;
            }
            last_stop_us = now;
            // Failed stop/limit-restore acknowledgements keep ownership and retry.
            // Never allow another move or tuning request to race an uncertain stop.
            if (servo.endTimedRotation()) {
                servo.cancelMotion();
                phase = failed ? Phase::Failed : (!enabled || cancel_requested ? Phase::Cancelled : Phase::Complete);
            }
        }
    }
    const char* state() const {
        switch (phase) {
            case Phase::Queued: return "queued"; case Phase::Rotating: return "rotating";
            case Phase::Stopping: return "stopping"; case Phase::Complete: return "estimated_complete";
            case Phase::Cancelled: return "cancelled"; case Phase::Expired: return "expired";
            case Phase::PowerCut: return "power_cut";
            case Phase::Failed: return "failed"; default: return "idle";
        }
    }
};
}
