/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace collector {
// One pitch attempt per instance. The boot instance may be explicitly replaced
// by a committed user setting; a stop never restarts an old attempt.
class StartupPose {
public:
    enum class Action { None, Start, Update, Cancel };
    static constexpr int kPitch = 50 + (850 - 50) / 10; // 10% of 5..85 degrees
    static constexpr int kSpeed = 250;
    static constexpr int64_t kMoveTimeoutUs = 15000000;
    Action step(int64_t now_us, bool enabled, bool valid_pitch, bool trajectory_done)
    {
        if (!enabled) { state_ = State::Cancelled; return Action::None; }
        if (state_ == State::Waiting) {
            if (started_at_ < 0) started_at_ = now_us;
            if (now_us - started_at_ >= 5000000) state_ = State::Unavailable;
            else if (valid_pitch) { state_ = State::Moving; moving_at_ = now_us; return Action::Start; }
        }
        if (state_ == State::Moving) {
            if (trajectory_done) state_ = State::Complete;
            else if (now_us - moving_at_ >= kMoveTimeoutUs) { state_ = State::Timeout; return Action::Cancel; }
        }
        // Continue factory updates so automatic torque release still runs at rest.
        return state_ == State::Moving || state_ == State::Complete ? Action::Update : Action::None;
    }
    void start_rejected() { state_ = State::Rejected; }
    void target_unavailable() { state_ = State::TargetUnavailable; }
    const char* state() const
    {
        switch (state_) {
            case State::Waiting: return "waiting_feedback";
            case State::Moving: return "moving";
            case State::Complete: return "trajectory_complete";
            case State::Unavailable: return "feedback_unavailable";
            case State::Cancelled: return "cancelled";
            case State::Timeout: return "trajectory_timeout";
            case State::Rejected: return "start_rejected";
            case State::TargetUnavailable: return "target_unavailable";
        }
        return "unknown";
    }
private:
    enum class State { Waiting, Moving, Complete, Unavailable, Cancelled, Timeout, Rejected, TargetUnavailable };
    State state_ = State::Waiting;
    int64_t started_at_ = -1, moving_at_ = -1;
};
}
