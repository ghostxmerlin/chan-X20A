/* SPDX-License-Identifier: MIT */
#pragma once
// Nod / shake (collector-0.5.10, robot command line "nod" / "shake"; owner default 2026-11-18): a small slow gesture
// that returns to where it started and never touches NVS (the pitch setting path saves to NVS, so it is not used).
// Each cycle goes start - amp, start + amp (nod: down first; shake: the robot's own left first), then back to start.
// Waypoints are clamped to the axis limits; a gesture with less than kMinSpanTenths of room is refused (no_room).
// 0.5.11 (owner 2026-11-19 "点头没反应，摇头太慢幅度太小"): the saved pitch sits on its 5 deg floor, so "down 8" went
// nowhere and the nod was a 3.7 -> 5 -> 11.7 deg lift. Now: with less than half the amplitude of room on one side the
// cycle goes the full amplitude the other way and back (from the floor: up, back, up, back); bigger and faster.
// Each leg ends when its trajectory is complete or after kLegUs, the whole gesture after kMaxUs.
// Pure logic, driven by tests/head_gesture_test.cpp; remote.cpp's motor task runs one at a time.
#include <cstdint>

namespace collector {
struct GesturePlan {
    static constexpr int kMaxPoints = 5;
    int points[kMaxPoints] = {};
    int count = 0;
};
constexpr int kNodAmpTenths = 120;             // +-12 degrees (0.5.10: 8)
constexpr int kShakeAmpTenths = 150;           // +-15 degrees (0.5.10: 8)
constexpr int kMinSpanTenths = 60;             // less room than 6 degrees from low to high point: no_room
constexpr int kShakeSpeed = 1000;              // yaw spring mapping input, as the web moves (0.5.10: 400)
// Pitch legs: a gentler spring than the default speed map, faster than the 2.25 / 3.0 pitch profile (critical
// damping, about 0.6 s per leg; 0.5.10: 40 / 12.6, about 0.8 s).
constexpr float kNodStiffness = 70.0f, kNodDamping = 16.7f;

inline bool plan_gesture(int start, int amp, int cycles, int lower, int upper, GesturePlan& out)
{
    out = GesturePlan{};
    if (cycles < 1 || cycles > 2 || amp <= 0 || lower >= upper) return false;
    const auto clamp = [&](int v) { return v < lower ? lower : v > upper ? upper : v; };
    const int s = clamp(start), lo = clamp(s - amp), hi = clamp(s + amp);    // from the clamped start (0.5.11)
    if (hi - lo < kMinSpanTenths) return false;
    if (s - lo < amp / 2 || hi - s < amp / 2) {    // one side has no room: the full swing the other way and back
        const int far = s - lo < amp / 2 ? hi : lo;
        for (int c = 0; c < cycles; ++c) { out.points[out.count++] = far; out.points[out.count++] = s; }
        return true;
    }
    for (int c = 0; c < cycles; ++c) { out.points[out.count++] = lo; out.points[out.count++] = hi; }
    out.points[out.count++] = s;
    return true;
}

class HeadGesture {
public:
    static constexpr int64_t kLegUs = 1500000, kMaxUs = 9000000;
    enum class Action : uint8_t { None, Move, Done };

    void begin(const GesturePlan& plan, int64_t now)
    {
        plan_ = plan; next_ = 0; active_ = plan.count > 0; leg_us_ = 0; started_us_ = now; result_ = "running";
    }
    // Every motor tick while active. Move: start a leg to `target` now. Done: the last leg finished (result "done")
    // or the gesture ran out of time ("timeout"); the axis keeps its normal hold / release after that.
    Action step(int64_t now, bool trajectory_complete, int& target)
    {
        if (!active_) return Action::None;
        if (now - started_us_ > kMaxUs) return finish("timeout");
        const bool leg_over = !leg_us_ || trajectory_complete || now - leg_us_ > kLegUs;
        if (!leg_over) return Action::None;
        if (next_ >= plan_.count) return finish("done");
        target = plan_.points[next_++];
        leg_us_ = now ? now : 1;
        return Action::Move;
    }
    // A stop, a newer move on the axis, the motor going off: the gesture ends where the head is.
    void cancel(const char* why) { if (active_) finish(why); }
    bool active() const { return active_; }
    const char* result() const { return result_; }
    int legs_done() const { return next_; }

private:
    Action finish(const char* why) { active_ = false; result_ = why; return Action::Done; }
    GesturePlan plan_;
    int next_ = 0;
    bool active_ = false;
    int64_t leg_us_ = 0, started_us_ = 0;
    const char* result_ = "";
};
}  // namespace collector
