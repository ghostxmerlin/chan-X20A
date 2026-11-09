#include "../upstream/StackChan/firmware/main/collector/servo_tuning.h"
#include <cassert>
#include <iostream>
using namespace collector;
using Op = ServoTuneRequest::Op;
int main() {
    ServoTuneRequest r;
    assert(!servo_tune_request_valid(r)); // Op::None
    r.op = Op::Refresh; assert(servo_tune_request_valid(r));
    r.axis = 2; assert(!servo_tune_request_valid(r)); r.axis = 1; assert(servo_tune_request_valid(r));
    r.op = Op::Restore; assert(servo_tune_request_valid(r));
    r.op = Op::Eeprom; assert(!servo_tune_request_valid(r)); // nothing flagged
    r.eeprom.has_p = true; r.eeprom.p = 255; assert(!servo_tune_request_valid(r));
    r.eeprom.p = 30; assert(servo_tune_request_valid(r));
    r.eeprom.has_punch = true; r.eeprom.punch = 1001; assert(!servo_tune_request_valid(r)); r.eeprom.punch = 200; assert(servo_tune_request_valid(r));
    r.eeprom.has_dead_cw = true; r.eeprom.dead_cw = 17; assert(!servo_tune_request_valid(r)); r.eeprom.dead_cw = 2; assert(servo_tune_request_valid(r));
    r.eeprom.has_max_torque = true; r.eeprom.max_torque = -1; assert(!servo_tune_request_valid(r)); r.eeprom.max_torque = 1000; assert(servo_tune_request_valid(r));
    const auto f = factory_servo_eeprom();
    assert(f.has_p && f.p == 15 && f.d == 15 && f.i == 0 && f.punch == 30 && f.dead_cw == 1 && f.dead_ccw == 1 && f.max_torque == 1000 && servo_eeprom_tuning_valid(f));
    ServoTuneRequest rt; rt.op = Op::Runtime; assert(!servo_tune_request_valid(rt));
    rt.has_goal = true; rt.goal_time_ms = 10000; assert(!servo_tune_request_valid(rt)); rt.goal_time_ms = 0; rt.goal_speed = 200; assert(servo_tune_request_valid(rt));
    rt.has_spring = true; rt.spring_stiffness = 50; rt.spring_damping = 0; assert(!servo_tune_request_valid(rt)); // both or neither
    rt.spring_damping = 14; assert(servo_tune_request_valid(rt)); rt.spring_stiffness = rt.spring_damping = 0; assert(servo_tune_request_valid(rt)); // clear
    rt.has_release = true; rt.hold_min_ms = 800; rt.hold_max_ms = 500; assert(!servo_tune_request_valid(rt)); rt.hold_max_ms = 3000; rt.tolerance_tenths = 301; assert(!servo_tune_request_valid(rt)); rt.tolerance_tenths = 6; assert(servo_tune_request_valid(rt));
    std::cout << "PASS: servo tuning request validation and factory preset\n";
}
