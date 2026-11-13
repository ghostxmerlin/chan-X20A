/* SPDX-License-Identifier: MIT */
#pragma once
#include "timed_yaw_turn.h"
#include "web_control.h"
#include <cstdint>

namespace collector {
// Spoken yaw words (user, 2026-11-13). Factory ids from M5Stack unit_asr (commandList):
// 0x01 up, 0x02 down, 0x03 left, 0x04 turn left, 0x05 right, 0x06 turn right, 0x08 front, 0x0A back.
// 0x07 forward / 0x09 backward keep the heart / X reaction and do not move.
enum class AsrMotionWord : uint8_t { None, TrackingOn, TrackingOff, Front, Back, Left, TurnLeft, Right, TurnRight };
inline AsrMotionWord asr_motion_word(uint8_t id, bool has_value)
{
    if (has_value) return AsrMotionWord::None;
    switch (id) {
    case 0x01: return AsrMotionWord::TrackingOn;
    case 0x02: return AsrMotionWord::TrackingOff;
    case 0x03: return AsrMotionWord::Left;
    case 0x04: return AsrMotionWord::TurnLeft;
    case 0x05: return AsrMotionWord::Right;
    case 0x06: return AsrMotionWord::TurnRight;
    case 0x08: return AsrMotionWord::Front;
    case 0x0A: return AsrMotionWord::Back;
    default: return AsrMotionWord::None;
    }
}
// Relative 45-degree notches, the robot's own left negative like the web page's left button.
inline int asr_motion_notches(AsrMotionWord word)
{
    switch (word) {
    case AsrMotionWord::Left: return -1;
    case AsrMotionWord::TurnLeft: return -2;
    case AsrMotionWord::Right: return 1;
    case AsrMotionWord::TurnRight: return 2;
    default: return 0;
    }
}
inline const char* asr_motion_action(AsrMotionWord word)
{
    switch (word) {
    case AsrMotionWord::TrackingOn: return "tracking_on";
    case AsrMotionWord::TrackingOff: return "tracking_off";
    case AsrMotionWord::Front: return "centre";
    case AsrMotionWord::Back: return "turn_around";
    case AsrMotionWord::Left: return "yaw_left_45";
    case AsrMotionWord::TurnLeft: return "yaw_left_90";
    case AsrMotionWord::Right: return "yaw_right_45";
    case AsrMotionWord::TurnRight: return "yaw_right_90";
    default: return nullptr;
    }
}

// One locked copy of the servo owner's state (remote_yaw_status). Angles in 0.1 degree.
struct AsrYawStatus {
    bool control = false;            // servo control requested, motor initialised and enabled
    bool tracking = false;           // face tracking requested, from any source
    YawFacing facing = YawFacing::Front;
    bool turn_busy = false, turn_complete = false;
    uint32_t turn_seq = 0;
    bool manual_busy = false;        // a web/ASR position move is queued or moving
    bool manual_failed = false;      // the latest one was cancelled, expired, superseded or refused
    uint32_t manual_seq = 0;
    bool settled = false;            // yaw trajectory complete and no timed turn
    bool feedback = false;           // valid position sample younger than 1 s
    int angle = 0;
};
struct AsrMotionOutcome { const char* action = nullptr; const char* result = nullptr; };

// Turns one spoken word into existing motion requests. It never touches the UART: every request
// goes through the remote_* entry points, which keep the limits, stall/fault protection and the
// single motor task. Only the ASR task calls command() and tick(); a word is never queued.
// Port: status(), tracking(bool), notches(int, uint32_t&), centre(uint32_t&), turn(uint32_t&).
class AsrYawPlan {
public:
    // "Near the centre": the 45-degree notches land 3-4 degrees short, centre within ~4 degrees.
    static constexpr int kCentreToleranceTenths = 100;
    // A centre that stalled further out than this does not start a turn.
    static constexpr int kCentreMissTenths = 150;
    static constexpr int64_t kSettleUs = 500000, kStageTimeoutUs = 6000000;
    enum class Stage : uint8_t { Idle, CentreThenTurn, TurnThenCentre };

    template<class Port> AsrMotionOutcome command(AsrMotionWord word, Port& port, int64_t now)
    {
        const char* action = asr_motion_action(word);
        if (!action) return {};
        uint32_t seq = 0;
        if (word == AsrMotionWord::TrackingOff) return {action, port.tracking(false) ? "accepted" : "unavailable"};
        if (stage_ != Stage::Idle) return {action, "busy"};
        if (word == AsrMotionWord::TrackingOn) return {action, port.tracking(true) ? "accepted" : "unavailable"};
        const AsrYawStatus s = port.status();
        if (s.turn_busy || s.manual_busy) return {action, "busy"};
        if (!s.control) return {action, "motor_off"};
        if (const int n = asr_motion_notches(word)) {
            if (s.facing == YawFacing::Back) return {action, "facing_back"};
            if (!s.feedback) return {action, "no_feedback"};
            const int current = web_yaw_slot(s.angle);
            int slot = current + n;
            if (slot < -kWebYawSlots) slot = -kWebYawSlots;
            if (slot > kWebYawSlots) slot = kWebYawSlots;
            if (n > 0 ? slot <= current : slot >= current) return {action, "at_limit"};
            return {action, port.notches(n, seq) ? "accepted" : "rejected"};
        }
        if (word == AsrMotionWord::Front) {
            if (s.facing == YawFacing::Back) {
                if (!port.turn(seq)) return {"return_turn", "rejected"};
                begin(Stage::TurnThenCentre, seq, s.manual_seq, now);
                return {"return_turn", "accepted"};
            }
            if (!s.feedback) return {action, "no_feedback"};
            return {action, port.centre(seq) ? "accepted" : "rejected"};
        }
        // Back.
        if (s.facing == YawFacing::Back) return {action, "already_back"};
        if (s.facing == YawFacing::Unknown) return {action, "facing_unknown"};
        if (!s.feedback) return {action, "no_feedback"};
        if (s.angle > kCentreToleranceTenths || s.angle < -kCentreToleranceTenths) {
            if (!port.centre(seq)) return {"centre_then_turn", "rejected"};
            begin(Stage::CentreThenTurn, seq, seq, now);
            return {"centre_then_turn", "accepted"};
        }
        return {action, port.turn(seq) ? "accepted" : "rejected"};
    }

    // Returns the plan's final result on the tick that ends it, otherwise nullptr.
    template<class Port> const char* tick(Port& port, int64_t now)
    {
        if (stage_ == Stage::Idle) return nullptr;
        const AsrYawStatus s = port.status();
        if (!s.control) return finish("stopped");
        if (s.tracking) return finish("superseded");
        if (now - started_us_ > kStageTimeoutUs) return finish("timeout");
        uint32_t seq = 0;
        if (stage_ == Stage::CentreThenTurn) {
            if (s.manual_seq != manual_seq_ || s.turn_busy) return finish("superseded");
            if (s.manual_failed) return finish("stopped");
            if (s.manual_busy || !s.settled) { settled_us_ = 0; return nullptr; }
            if (!settled_us_) settled_us_ = now;
            if (now - settled_us_ < kSettleUs || !s.feedback) return nullptr;
            if (s.angle > kCentreMissTenths || s.angle < -kCentreMissTenths) return finish("centre_missed");
            return port.turn(seq) ? finish("turn_started") : nullptr; // retried until the timeout
        }
        // TurnThenCentre: the return turn, then a centre from the measured angle.
        if (s.turn_seq != turn_seq_ || s.manual_seq != manual_seq_) return finish("superseded");
        if (s.turn_busy) return nullptr;
        if (!s.turn_complete) return finish("stopped");
        if (!settled_us_) settled_us_ = now;
        if (now - settled_us_ < kSettleUs || !s.feedback || s.manual_busy) return nullptr;
        if (s.angle <= kCentreToleranceTenths && s.angle >= -kCentreToleranceTenths) return finish("front");
        return port.centre(seq) ? finish("centring") : nullptr;
    }

    Stage stage() const { return stage_; }
    const char* stage_name() const
    {
        return stage_ == Stage::CentreThenTurn ? "centre_then_turn" : stage_ == Stage::TurnThenCentre ? "turn_then_centre" : "idle";
    }
    const char* last_result() const { return last_result_; }

private:
    void begin(Stage stage, uint32_t seq, uint32_t manual_seq, int64_t now)
    {
        stage_ = stage; turn_seq_ = stage == Stage::TurnThenCentre ? seq : 0; manual_seq_ = manual_seq;
        started_us_ = now; settled_us_ = 0; last_result_ = "pending";
    }
    const char* finish(const char* result) { stage_ = Stage::Idle; last_result_ = result; return result; }
    Stage stage_ = Stage::Idle;
    uint32_t turn_seq_ = 0, manual_seq_ = 0;
    int64_t started_us_ = 0, settled_us_ = 0;
    const char* last_result_ = "";
};
}
