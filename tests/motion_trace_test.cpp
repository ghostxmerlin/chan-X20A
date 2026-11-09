#include "../upstream/StackChan/firmware/main/collector/motion_trace.h"
#include <cassert>
#include <iostream>
using namespace collector;
using stackchan::motion::ServoFeedback;
using stackchan::motion::ServoDiagnostics;
int main() {
    MotionTrace trace;
    ServoFeedback start; start.valid = true; start.angle = 12; start.position_raw = 465;
    start.load_valid = start.current_valid = true; start.load_raw = -120; start.current_raw = 37;
    start.voltage_raw = 74; start.temperature_raw = 41; start.moving_raw = 0;
    ServoDiagnostics diagnostics;
    diagnostics.last_position_write.valid = true; diagnostics.last_position_write.timestamp_us = 900; // before the command
    assert(!trace.active && trace.count == 0);
    trace.sample(start, 950); trace.note(diagnostics);
    assert(trace.count == 0 && !trace.last_goal_write.valid); // inactive trace records nothing
    trace.begin("web_yaw", 7, -38, start, 1000);
    assert(trace.active && trace.sequence == 7 && trace.target == -38 && trace.start.position_raw == 465);
    trace.note(diagnostics);
    assert(!trace.last_goal_write.valid); // stale write before the command is ignored
    diagnostics.last_position_write.timestamp_us = 1200;
    diagnostics.last_torque_disable.valid = true; diagnostics.last_torque_disable.timestamp_us = 800;
    trace.note(diagnostics);
    assert(trace.last_goal_write.timestamp_us == 1200 && !trace.last_torque_disable.valid);
    diagnostics.last_torque_disable.timestamp_us = 3500; trace.note(diagnostics);
    assert(trace.last_torque_disable.timestamp_us == 3500);
    ServoFeedback invalid; // failed read stays distinct from a position
    for (size_t i = 0; i < MotionTrace::kCapacity; ++i) {
        ServoFeedback f = start; f.position_raw = 465 - int(i % 17); f.angle = 12 - int(i % 17) * 3;
        trace.sample(i % 10 == 9 ? invalid : f, 1000 + 100 * int64_t(i));
    }
    assert(trace.count == MotionTrace::kCapacity && trace.active && !trace.truncated);
    assert(!trace.samples[9].valid && trace.samples[9].position_raw == -1 && trace.samples[8].valid && trace.samples[8].position_raw == 457);
    assert(trace.samples[8].load_raw == -120 && trace.samples[8].current_raw == 37 && trace.samples[8].voltage_raw == 74 &&
           trace.samples[8].temperature_raw == 41 && trace.samples[8].moving_raw == 0);
    assert(trace.samples[9].voltage_raw == -1 && trace.samples[9].moving_raw == -1); // invalid read keeps unknowns
    trace.sample(start, 50000); // beyond capacity: closes, never overwrites
    assert(!trace.active && trace.truncated && trace.count == MotionTrace::kCapacity && trace.closed_us == 50000);
    trace.note(diagnostics); trace.sample(start, 50100);
    assert(trace.count == MotionTrace::kCapacity);
    MotionTrace cancelled; cancelled.begin("pitch_setting", 3, 150, start, 10);
    cancelled.sample(start, 20); cancelled.close(30); cancelled.close(40);
    assert(!cancelled.active && cancelled.closed_us == 30 && cancelled.count == 1 && !cancelled.truncated);
    cancelled.begin("tracking", 4, 10, start, 100); // restart resets samples and cached transactions
    assert(cancelled.active && cancelled.count == 0 && !cancelled.last_goal_write.valid && cancelled.closed_us == 0);
    std::cout << "PASS: bounded motion trace records only while active, ignores pre-command transactions, keeps invalid reads distinct\n";
}
