/* SPDX-License-Identifier: MIT */
#pragma once
// Boot auto-tracking (user, 2026-11-13): once after power-on, if the M12 comes online,
// arm the servos and turn face tracking on (which also switches the M12 to detection).
// Without an M12 nothing happens. It runs once: any spoken tracking or motion word before it
// fires cancels it, and it never turns tracking back on after "down" or "close".
// Pure logic; asr_task owns the instance and performs the two actions.
#include <cstdint>

namespace collector {

class BootTracking {
public:
    enum class Action : uint8_t { None, Arm, Track };
    static constexpr int64_t kM12WaitUs = 30000000;   // give the M12 30 s from boot to report
    static constexpr int64_t kReadyWaitUs = 10000000; // then 10 s for the servos to become ready

    // m12_online: heartbeat seen; control_ready: servos armed, initialised and enabled.
    Action tick(int64_t now, bool m12_online, bool control_ready)
    {
        if (state_ == State::Done) return Action::None;
        if (state_ == State::WaitM12) {
            if (!m12_online) {
                if (now >= kM12WaitUs) finish("no_m12");
                return Action::None;
            }
            state_ = State::WaitReady; since_ = now;
            if (!control_ready) return Action::Arm;
        }
        if (control_ready) return Action::Track;
        if (now - since_ >= kReadyWaitUs) finish("servos_not_ready");
        return Action::None;
    }
    void armed(bool ok) { if (!ok) finish("arm_refused"); }
    // A refused tracking request is retried on the next tick until the ready window ends.
    void tracked(bool ok) { if (ok) finish("tracking_on"); }
    void cancel() { if (state_ != State::Done) finish("cancelled_by_user"); }
    bool done() const { return state_ == State::Done; }
    const char* result() const { return result_; }

private:
    enum class State : uint8_t { WaitM12, WaitReady, Done };
    void finish(const char* why) { state_ = State::Done; result_ = why; }
    State state_ = State::WaitM12;
    int64_t since_ = 0;
    const char* result_ = "waiting_m12";
};

} // namespace collector
