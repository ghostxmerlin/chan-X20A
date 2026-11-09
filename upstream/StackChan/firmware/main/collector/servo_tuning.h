/* SPDX-License-Identifier: MIT */
#pragma once
#include "../stackchan/motion/servo.h"
#include <cstdint>

namespace collector {
// One queued operator request for a servo axis. HTTP validates and queues it
// under the remote mutex; only motor_task (the bus owner) applies it, when the
// axis has no trajectory in flight. Values are register / profile units.
struct ServoTuneRequest {
    enum class Op : uint8_t { None, Eeprom, Runtime, Refresh, Restore };
    int axis = 0; // 0 yaw, 1 pitch
    Op op = Op::None;
    stackchan::motion::ServoEepromTuning eeprom;
    bool has_goal = false, has_spring = false, has_release = false;
    int goal_time_ms = 20, goal_speed = 0;
    float spring_stiffness = 0, spring_damping = 0; // 0/0 clears the override
    uint32_t hold_min_ms = 500, hold_max_ms = 2000;
    int tolerance_tenths = 10;
    uint32_t sequence = 0;
    int64_t requested_us = 0;
};

// Factory values read back from both SCS0009 servos on 2026-11-08.
inline stackchan::motion::ServoEepromTuning factory_servo_eeprom()
{
    stackchan::motion::ServoEepromTuning t;
    t.has_p = t.has_d = t.has_i = t.has_punch = t.has_dead_cw = t.has_dead_ccw = t.has_max_torque = true;
    t.p = 15; t.d = 15; t.i = 0; t.punch = 30; t.dead_cw = 1; t.dead_ccw = 1; t.max_torque = 1000;
    return t;
}

inline bool servo_eeprom_tuning_valid(const stackchan::motion::ServoEepromTuning& t)
{
    const bool any = t.has_p || t.has_d || t.has_i || t.has_punch || t.has_dead_cw || t.has_dead_ccw || t.has_max_torque;
    if (!any) return false;
    if (t.has_p && (t.p < 0 || t.p > 254)) return false;
    if (t.has_d && (t.d < 0 || t.d > 254)) return false;
    if (t.has_i && (t.i < 0 || t.i > 254)) return false;
    if (t.has_punch && (t.punch < 0 || t.punch > 1000)) return false;
    if (t.has_dead_cw && (t.dead_cw < 0 || t.dead_cw > 16)) return false;
    if (t.has_dead_ccw && (t.dead_ccw < 0 || t.dead_ccw > 16)) return false;
    if (t.has_max_torque && (t.max_torque < 0 || t.max_torque > 1000)) return false;
    return true;
}

inline bool servo_tune_request_valid(const ServoTuneRequest& r)
{
    if (r.axis != 0 && r.axis != 1) return false;
    switch (r.op) {
        case ServoTuneRequest::Op::Eeprom: return servo_eeprom_tuning_valid(r.eeprom);
        case ServoTuneRequest::Op::Refresh:
        case ServoTuneRequest::Op::Restore: return true;
        case ServoTuneRequest::Op::Runtime:
            if (!r.has_goal && !r.has_spring && !r.has_release) return false;
            if (r.has_goal && (r.goal_time_ms < 0 || r.goal_time_ms > 9999 || r.goal_speed < 0 || r.goal_speed > 1000)) return false;
            if (r.has_spring && ((r.spring_stiffness < 0) || (r.spring_damping < 0) ||
                                 ((r.spring_stiffness > 0) != (r.spring_damping > 0)) ||
                                 r.spring_stiffness > 2000 || r.spring_damping > 500)) return false;
            if (r.has_release && (r.hold_min_ms > 10000 || r.hold_max_ms > 20000 || r.hold_max_ms < r.hold_min_ms ||
                                  r.tolerance_tenths < 0 || r.tolerance_tenths > 300)) return false;
            return true;
        default: return false;
    }
}
}
