/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace collector {
// One pitch attempt per boot. Stop cancels it; yaw-only remote input is unrelated.
class StartupPose {
public:
    enum class Action { None, Start, Update };
    static constexpr int kPitch = 50 + (850 - 50) / 10; // 10% of 5..85 degrees
    static constexpr int kSpeed = 250;
    Action step(int64_t now_us, bool enabled, bool valid_pitch, bool trajectory_done)
    {
        if (!enabled) { state_ = State::Cancelled; return Action::None; }
        if (state_ == State::Waiting) {
            if (started_at_ < 0) started_at_ = now_us;
            if (now_us - started_at_ >= 5000000) state_ = State::Unavailable;
            else if (valid_pitch) { state_ = State::Moving; return Action::Start; }
        }
        if (state_ == State::Moving && trajectory_done) state_ = State::Complete;
        // Continue factory updates so automatic torque release still runs at rest.
        return state_ == State::Moving || state_ == State::Complete ? Action::Update : Action::None;
    }
    const char* state() const
    {
        switch (state_) {
            case State::Waiting: return "waiting_feedback";
            case State::Moving: return "moving";
            case State::Complete: return "trajectory_complete";
            case State::Unavailable: return "feedback_unavailable";
            case State::Cancelled: return "cancelled";
        }
        return "unknown";
    }
private:
    enum class State { Waiting, Moving, Complete, Unavailable, Cancelled };
    State state_ = State::Waiting;
    int64_t started_at_ = -1;
};
}
