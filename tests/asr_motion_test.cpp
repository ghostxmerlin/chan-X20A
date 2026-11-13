// Spoken yaw words (user, 2026-11-13): mapping and the two-stage plan, against a fake servo owner.
#include "../upstream/StackChan/firmware/main/collector/asr_motion.h"
#include "../upstream/StackChan/firmware/main/collector/asr_protocol.h"
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
using namespace collector;

namespace {
// Accepts like remote.cpp: refuses while busy or without control; records every call.
struct Port {
    AsrYawStatus s;
    bool accept = true, tracking_ok = true;
    std::vector<std::string> calls;
    uint32_t next_manual = 0, next_turn = 0;
    AsrYawStatus status() { return s; }
    bool tracking(bool on) {
        calls.push_back(on ? "tracking_on" : "tracking_off");
        if (!tracking_ok) return false;
        s.tracking = on; return true;
    }
    bool notches(int n, uint32_t& seq) {
        calls.push_back("notches " + std::to_string(n));
        if (!accept || !s.control || s.turn_busy || s.manual_busy) return false;
        seq = s.manual_seq = ++next_manual; s.manual_busy = true; s.settled = false; s.tracking = false; return true;
    }
    bool centre(uint32_t& seq) {
        calls.push_back("centre");
        if (!accept || !s.control || s.turn_busy || s.manual_busy) return false;
        seq = s.manual_seq = ++next_manual; s.manual_busy = true; s.settled = false; s.tracking = false; return true;
    }
    bool turn(uint32_t& seq) {
        calls.push_back("turn");
        if (!accept || !s.control || s.turn_busy || s.manual_busy) return false;
        seq = s.turn_seq = ++next_turn; s.turn_busy = true; s.turn_complete = false; s.settled = false; s.tracking = false; return true;
    }
    // Simulated motor task outcomes.
    void manual_done(int angle) { s.manual_busy = false; s.settled = true; s.feedback = true; s.angle = angle; }
    void turn_done(bool complete, int angle, bool feedback = true) {
        s.turn_busy = false; s.turn_complete = complete; s.settled = true; s.feedback = feedback; s.angle = angle;
        s.facing = complete ? (s.facing == YawFacing::Front ? YawFacing::Back : s.facing == YawFacing::Back ? YawFacing::Front : YawFacing::Unknown)
                            : YawFacing::Unknown;
    }
};
Port ready(int angle = 0) {
    Port p; p.s.control = true; p.s.feedback = true; p.s.settled = true; p.s.angle = angle; return p;
}
bool is(const AsrMotionOutcome& o, const char* action, const char* result) {
    return o.action && o.result && !std::strcmp(o.action, action) && !std::strcmp(o.result, result);
}
} // namespace

int main()
{
    {   // Factory ids (M5Stack unit_asr commandList) for the eight words; every other id is not a motion word.
        assert(asr_motion_word(0x01, false) == AsrMotionWord::TrackingOn && !std::strcmp(asr_word(0x01), "up"));
        assert(asr_motion_word(0x02, false) == AsrMotionWord::TrackingOff && !std::strcmp(asr_word(0x02), "down"));
        assert(asr_motion_word(0x03, false) == AsrMotionWord::Left && !std::strcmp(asr_word(0x03), "left"));
        assert(asr_motion_word(0x04, false) == AsrMotionWord::TurnLeft && !std::strcmp(asr_word(0x04), "turn left"));
        assert(asr_motion_word(0x05, false) == AsrMotionWord::Right && !std::strcmp(asr_word(0x05), "right"));
        assert(asr_motion_word(0x06, false) == AsrMotionWord::TurnRight && !std::strcmp(asr_word(0x06), "turn right"));
        assert(asr_motion_word(0x08, false) == AsrMotionWord::Front && !std::strcmp(asr_word(0x08), "front"));
        assert(asr_motion_word(0x0A, false) == AsrMotionWord::Back && !std::strcmp(asr_word(0x0A), "back"));
        // forward/backward keep the heart / X; open/close, turn on/off, start/stop, next, volume untouched.
        for (int id = 0; id < 256; ++id) {
            const bool motion = (id >= 0x01 && id <= 0x06) || id == 0x08 || id == 0x0A;
            assert((asr_motion_word(uint8_t(id), false) != AsrMotionWord::None) == motion);
            assert(asr_motion_word(uint8_t(id), true) == AsrMotionWord::None); // value replies never move
        }
        assert(asr_motion_notches(AsrMotionWord::Left) == -1 && asr_motion_notches(AsrMotionWord::TurnLeft) == -2);
        assert(asr_motion_notches(AsrMotionWord::Right) == 1 && asr_motion_notches(AsrMotionWord::TurnRight) == 2);
        assert(asr_motion_notches(AsrMotionWord::Front) == 0 && asr_motion_notches(AsrMotionWord::Back) == 0);
        assert(asr_reaction(0x07) == AsrReaction::none && asr_reaction(0x09) == AsrReaction::none);
        assert(asr_reaction(0x08) == AsrReaction::none && asr_reaction(0x0A) == AsrReaction::none);
    }
    AsrYawPlan plan;
    {   // up / down: the tracking switch, nothing else.
        Port p = ready();
        assert(is(plan.command(AsrMotionWord::TrackingOn, p, 1), "tracking_on", "accepted") && p.s.tracking);
        assert(is(plan.command(AsrMotionWord::TrackingOff, p, 2), "tracking_off", "accepted") && !p.s.tracking);
        p.tracking_ok = false;
        assert(is(plan.command(AsrMotionWord::TrackingOn, p, 3), "tracking_on", "unavailable"));
        assert(p.calls.size() == 3 && plan.stage() == AsrYawPlan::Stage::Idle);
        assert(plan.command(AsrMotionWord::None, p, 4).action == nullptr);
    }
    {   // left / right: one 45° notch from the nearest notch; turn left / right: two notches, clipped at ±90°.
        Port p = ready(0);
        assert(is(plan.command(AsrMotionWord::Left, p, 1), "yaw_left_45", "accepted") && p.calls.back() == "notches -1");
        p.manual_done(-430);
        assert(is(plan.command(AsrMotionWord::TurnLeft, p, 2), "yaw_left_90", "accepted") && p.calls.back() == "notches -2");
        p.manual_done(-870);
        assert(is(plan.command(AsrMotionWord::Left, p, 3), "yaw_left_45", "at_limit"));
        assert(is(plan.command(AsrMotionWord::TurnLeft, p, 4), "yaw_left_90", "at_limit"));
        assert(p.calls.back() == "notches -2"); // no call at the limit
        assert(is(plan.command(AsrMotionWord::TurnRight, p, 5), "yaw_right_90", "accepted") && p.calls.back() == "notches 2");
        p.manual_done(20);
        assert(is(plan.command(AsrMotionWord::Right, p, 6), "yaw_right_45", "accepted"));
        p.manual_done(440);
        assert(is(plan.command(AsrMotionWord::TurnRight, p, 7), "yaw_right_90", "accepted")); // clipped by remote to +90°
        p.manual_done(880);
        assert(is(plan.command(AsrMotionWord::Right, p, 8), "yaw_right_45", "at_limit"));
    }
    {   // Busy: a word during a move or a turn is dropped, never queued; down still switches tracking off.
        Port p = ready(0);
        assert(is(plan.command(AsrMotionWord::Right, p, 1), "yaw_right_45", "accepted"));
        const auto calls = p.calls.size();
        assert(is(plan.command(AsrMotionWord::Left, p, 2), "yaw_left_45", "busy"));
        assert(is(plan.command(AsrMotionWord::Front, p, 3), "centre", "busy"));
        assert(is(plan.command(AsrMotionWord::Back, p, 4), "turn_around", "busy"));
        assert(p.calls.size() == calls);
        assert(is(plan.command(AsrMotionWord::TrackingOff, p, 5), "tracking_off", "accepted"));
        p.manual_done(440); p.s.turn_busy = true;
        assert(is(plan.command(AsrMotionWord::Right, p, 6), "yaw_right_45", "busy"));
    }
    {   // Guards stay in the servo owner: motor off and missing feedback refuse without a call.
        Port p = ready(0); p.s.control = false;
        assert(is(plan.command(AsrMotionWord::Left, p, 1), "yaw_left_45", "motor_off") && p.calls.empty());
        assert(is(plan.command(AsrMotionWord::Back, p, 1), "turn_around", "motor_off") && p.calls.empty());
        p.s.control = true; p.s.feedback = false;
        assert(is(plan.command(AsrMotionWord::Left, p, 2), "yaw_left_45", "no_feedback"));
        assert(is(plan.command(AsrMotionWord::Front, p, 2), "centre", "no_feedback"));
        assert(is(plan.command(AsrMotionWord::Back, p, 2), "turn_around", "no_feedback") && p.calls.empty());
        p.s.feedback = true; p.accept = false;   // remote.cpp refused (e.g. stale feedback, tuning)
        assert(is(plan.command(AsrMotionWord::Left, p, 3), "yaw_left_45", "rejected"));
        assert(is(plan.command(AsrMotionWord::Front, p, 3), "centre", "rejected"));
        assert(is(plan.command(AsrMotionWord::Back, p, 3), "turn_around", "rejected"));
        assert(plan.stage() == AsrYawPlan::Stage::Idle);
    }
    {   // front while facing front: plain centre (also from a 45°/90° notch).
        Port p = ready(870);
        assert(is(plan.command(AsrMotionWord::Front, p, 1), "centre", "accepted") && p.calls.back() == "centre");
        assert(plan.stage() == AsrYawPlan::Stage::Idle);
    }
    {   // back near the centre: one timed turn, no second stage. back again: already back, nothing moves.
        Port p = ready(30);
        assert(is(plan.command(AsrMotionWord::Back, p, 1), "turn_around", "accepted") && p.calls.back() == "turn");
        assert(plan.stage() == AsrYawPlan::Stage::Idle);
        p.turn_done(true, 0, false); // facing back, sensor in its blind zone
        assert(p.s.facing == YawFacing::Back);
        const auto calls = p.calls.size();
        assert(is(plan.command(AsrMotionWord::Back, p, 2), "turn_around", "already_back"));
        // A notch or tracking from behind would aim the head with no valid position: refused.
        assert(is(plan.command(AsrMotionWord::Left, p, 3), "yaw_left_45", "facing_back"));
        assert(is(plan.command(AsrMotionWord::TurnRight, p, 3), "yaw_right_90", "facing_back"));
        assert(p.calls.size() == calls);
        // front from behind: the return turn, then the centre once the turn reports complete and settles.
        int64_t t = 10000000;
        assert(is(plan.command(AsrMotionWord::Front, p, t), "return_turn", "accepted") && p.calls.back() == "turn");
        assert(plan.stage() == AsrYawPlan::Stage::TurnThenCentre && !std::strcmp(plan.stage_name(), "turn_then_centre"));
        assert(is(plan.command(AsrMotionWord::Front, p, t + 1), "centre", "busy"));
        assert(plan.tick(p, t + 100000) == nullptr);           // still rotating
        p.turn_done(true, 250);                                  // came back 25° short
        assert(p.s.facing == YawFacing::Front);
        assert(plan.tick(p, t + 1500000) == nullptr);           // settle wait starts
        assert(plan.tick(p, t + 1900000) == nullptr && p.calls.back() == "turn");
        const char* done = plan.tick(p, t + 2000001);
        assert(done && !std::strcmp(done, "centring") && p.calls.back() == "centre");
        assert(plan.stage() == AsrYawPlan::Stage::Idle && !std::strcmp(plan.last_result(), "centring"));
    }
    {   // front from behind that lands near the centre needs no extra centre move.
        Port p = ready(0); p.s.facing = YawFacing::Back;
        assert(is(plan.command(AsrMotionWord::Front, p, 1), "return_turn", "accepted"));
        p.turn_done(true, -40);
        assert(plan.tick(p, 100000) == nullptr);
        const char* done = plan.tick(p, 600001);
        assert(done && !std::strcmp(done, "front") && p.calls.back() == "turn");
    }
    {   // A stopped return turn (motor disabled mid-turn) leaves the facing unknown and ends the plan.
        Port p = ready(0); p.s.facing = YawFacing::Back;
        assert(is(plan.command(AsrMotionWord::Front, p, 1), "return_turn", "accepted"));
        p.turn_done(false, 0);
        const char* done = plan.tick(p, 100000);
        assert(done && !std::strcmp(done, "stopped") && p.s.facing == YawFacing::Unknown);
        // Unknown: back refuses, front tries a centre through the normal feedback guard.
        assert(is(plan.command(AsrMotionWord::Back, p, 200000), "turn_around", "facing_unknown"));
        assert(is(plan.command(AsrMotionWord::Front, p, 200000), "centre", "accepted"));
    }
    {   // Return turn: feedback never comes back -> timeout, no centre written.
        Port p = ready(0); p.s.facing = YawFacing::Back;
        assert(is(plan.command(AsrMotionWord::Front, p, 1), "return_turn", "accepted"));
        p.turn_done(true, 0, false);
        for (int64_t t = 100000; t < AsrYawPlan::kStageTimeoutUs; t += 100000) assert(plan.tick(p, t) == nullptr);
        const char* done = plan.tick(p, AsrYawPlan::kStageTimeoutUs + 2);
        assert(done && !std::strcmp(done, "timeout") && p.calls.back() == "turn");
    }
    {   // back from a notch: centre first, settle, then the turn (rotations start near the validated centre).
        Port p = ready(-440);
        assert(is(plan.command(AsrMotionWord::Back, p, 1), "centre_then_turn", "accepted") && p.calls.back() == "centre");
        assert(plan.stage() == AsrYawPlan::Stage::CentreThenTurn);
        assert(plan.tick(p, 1000000) == nullptr);              // centre still moving
        p.manual_done(30);
        assert(plan.tick(p, 1500000) == nullptr && plan.tick(p, 1900000) == nullptr && p.calls.back() == "centre");
        const char* done = plan.tick(p, 2000001);
        assert(done && !std::strcmp(done, "turn_started") && p.calls.back() == "turn");
    }
    {   // centre-then-turn aborts when the centre missed, was cancelled, or someone else took the axis.
        Port p = ready(870);
        assert(is(plan.command(AsrMotionWord::Back, p, 1), "centre_then_turn", "accepted"));
        p.manual_done(300);   // stalled 30° off centre
        plan.tick(p, 100000);
        const char* done = plan.tick(p, 700000);
        assert(done && !std::strcmp(done, "centre_missed") && p.calls.back() == "centre");

        Port q = ready(870);
        assert(is(plan.command(AsrMotionWord::Back, q, 1), "centre_then_turn", "accepted"));
        q.s.manual_busy = false; q.s.manual_failed = true;   // stop pressed: manual state cancelled
        done = plan.tick(q, 100000);
        assert(done && !std::strcmp(done, "stopped") && q.calls.back() == "centre");

        Port r = ready(870);
        assert(is(plan.command(AsrMotionWord::Back, r, 1), "centre_then_turn", "accepted"));
        r.manual_done(0); r.s.manual_seq = 99;               // the web page sent another step
        done = plan.tick(r, 100000);
        assert(done && !std::strcmp(done, "superseded") && r.calls.back() == "centre");

        Port s = ready(870);
        assert(is(plan.command(AsrMotionWord::Back, s, 1), "centre_then_turn", "accepted"));
        s.manual_done(0); s.s.tracking = true;                // open / remote / web switched tracking on
        done = plan.tick(s, 100000);
        assert(done && !std::strcmp(done, "superseded") && s.calls.back() == "centre");

        Port u = ready(870);
        assert(is(plan.command(AsrMotionWord::Back, u, 1), "centre_then_turn", "accepted"));
        u.manual_done(0); u.s.control = false;                // servo disabled; must not turn after re-enable
        done = plan.tick(u, 100000);
        assert(done && !std::strcmp(done, "stopped"));
        u.s.control = true;
        assert(plan.tick(u, 2000000) == nullptr && u.calls.back() == "centre");
    }
    {   // While a plan is pending every other motion word and up are busy; down is not.
        Port p = ready(-440);
        assert(is(plan.command(AsrMotionWord::Back, p, 1), "centre_then_turn", "accepted"));
        p.manual_done(0);
        assert(is(plan.command(AsrMotionWord::Right, p, 2), "yaw_right_45", "busy"));
        assert(is(plan.command(AsrMotionWord::TrackingOn, p, 2), "tracking_on", "busy"));
        assert(is(plan.command(AsrMotionWord::TrackingOff, p, 2), "tracking_off", "accepted"));
        plan.tick(p, 100000);
        assert(!std::strcmp(plan.tick(p, 600001), "turn_started"));
    }
    {   // Words take over tracking through the servo owner (manual_override), exactly like the web buttons.
        Port p = ready(0); p.s.tracking = true;
        assert(is(plan.command(AsrMotionWord::Left, p, 1), "yaw_left_45", "accepted") && !p.s.tracking);
        p.manual_done(-450); p.s.tracking = true;
        assert(is(plan.command(AsrMotionWord::Back, p, 2), "centre_then_turn", "accepted") && !p.s.tracking);
    }
    {   // Names for logs and telemetry.
        assert(!std::strcmp(asr_motion_action(AsrMotionWord::Front), "centre"));
        assert(!std::strcmp(asr_motion_action(AsrMotionWord::Back), "turn_around"));
        assert(!std::strcmp(yaw_facing_name(YawFacing::Unknown), "unknown"));
        AsrYawPlan idle; assert(!std::strcmp(idle.stage_name(), "idle") && !std::strcmp(idle.last_result(), ""));
    }
    return 0;
}
