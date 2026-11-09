/* SPDX-License-Identifier: MIT */
#include "../upstream/StackChan/firmware/main/collector/pitch_setting.h"
#include "../upstream/StackChan/firmware/main/collector/pitch_motion.h"
#include <cmath>
#include "../upstream/StackChan/firmware/main/collector/web_control.h"
#include "../upstream/StackChan/firmware/main/collector/startup_pose.h"
#include "../upstream/StackChan/firmware/main/collector/remote_stop.h"
#include "../upstream/StackChan/firmware/main/collector/remote_actuation.h"
#include "../upstream/StackChan/firmware/main/stackchan/motion/servo.h"
#include <hal/hal.h>
#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>

uint32_t test_tick_ms = 0;
TestHal& GetHAL() { static TestHal hal; return hal; }
using collector::StartupPose;
using Action = StartupPose::Action;
using stackchan::motion::ServoFeedback;

class Axis : public stackchan::motion::Servo {
public:
    std::vector<int> commands;
    int reads = 0, physical = 40;
    bool torque = false;
    Axis() { set_angle_limit({50, 850}); initPassive(); reads = 0; }
    int getCurrentAngle() override { ++reads; return std::clamp(physical, _angle_limit.x, _angle_limit.y); }
    // Ideal shaft for trajectory tests: measured angle equals the last command.
    ServoFeedback readFeedback() override
    {
        ServoFeedback feedback; feedback.valid = true;
        feedback.angle = commands.empty() ? physical : commands.back();
        return feedback;
    }
    void setTorqueEnabled(bool enabled) override { torque = enabled; }
    bool getTorqueEnabled() override { return torque; }
    void limits(int lower, int upper) { set_angle_limit({lower, upper}); }
    void passive() { initPassive(); }
    int command_start() { return Servo::getCurrentAngle(); }
protected:
    void set_angle_impl(int angle) override { commands.push_back(angle); torque = true; }
};

static void storage_and_request_generation()
{
    for (int target = -200; target <= 1100; ++target)
        assert(collector::valid_pitch_setting(target) == (target >= 50 && target <= 850));
    struct Storage {
        int set_error = 0, commit_error = 0, value = 0, sets = 0, commits = 0;
        int set(int target) { ++sets; value = target; return set_error; }
        int commit() { ++commits; return commit_error; }
    } storage;
    collector::PitchSettingRequests requests;
    requests.configure_boot(160);
    assert(requests.current().from_boot && requests.current().target == 160 && !requests.cancelled(requests.current()));
    storage.set_error = 17;
    assert(collector::persist_pitch_setting(storage, 150) == 17);
    assert(storage.sets == 1 && storage.commits == 0 && requests.current().sequence == 0);
    storage.set_error = 0; storage.commit_error = 23;
    assert(collector::persist_pitch_setting(storage, 150) == 23);
    assert(storage.commits == 1 && requests.current().sequence == 0);
    storage.commit_error = 0;
    assert(collector::persist_pitch_setting(storage, 150) == 0);
    assert(requests.submit_committed(storage.value, 100) == 1);
    const auto first = requests.current();
    assert(first.target == 150 && !first.from_boot && first.timestamp == 100);
    requests.stop(); // Stop and re-enable happen entirely between task samples.
    assert(requests.cancelled(first));
    assert(requests.cancelled(requests.current())); // Re-enable cannot revive it.
    assert(requests.submit_committed(130, 200) == 2);
    assert(!requests.cancelled(requests.current()) && requests.cancelled(first));
    requests.stop();
    assert(requests.cancelled(requests.current()));
    // Invalid submissions cannot replace even a cancelled request.
    assert(requests.submit_committed(49, 300) == 2 && requests.current().target == 130);
    collector::PitchSettingRequests bad_saved;
    bad_saved.configure_boot(900);
    assert(bad_saved.current().target == 130);
}

static void feedback_boundary()
{
    Axis axis;
    const auto deadband = axis.readDeadbandSnapshot();
    assert(!deadband.supported && !deadband.attempted && !deadband.valid);
    assert(deadband.cw_raw == -1 && deadband.ccw_raw == -1 && axis.reads == 0 && axis.commands.empty());
    ServoFeedback feedback;
    feedback.valid = true; feedback.angle = 40; feedback.position_raw = 123;
    assert(!axis.canStartFromFeedback(feedback, 130));
    assert(!axis.moveFromFeedbackWithSpeed(feedback, 130, 250));
    assert(axis.moveFromFeedbackWithSpeed(feedback, 130, 250, 20));
    assert(axis.command_start() == 50 && axis.reads == 0);
    assert(feedback.angle == 40 && feedback.position_raw == 123 && feedback.valid);
    for (int actual : {30, 40, 50, 130, 850}) {
        feedback.angle = actual;
        for (int target : {50, 130, 850}) {
            assert(axis.canStartFromFeedback(feedback, target, 20));
            assert(axis.moveFromFeedbackWithSpeed(feedback, target, 250, 20));
            assert(axis.command_start() == std::max(actual, 50));
            assert(feedback.angle == actual && axis.reads == 0);
        }
    }
    for (int actual : {29, 851}) {
        feedback.angle = actual;
        assert(!axis.moveFromFeedbackWithSpeed(feedback, 130, 250, 20));
    }
    feedback.angle = 40;
    for (int margin : {-1, 21}) assert(!axis.moveFromFeedbackWithSpeed(feedback, 130, 250, margin));
    for (int target : {49, 851}) assert(!axis.moveFromFeedbackWithSpeed(feedback, target, 250, 20));
    feedback.valid = false;
    assert(!axis.moveFromFeedbackWithSpeed(feedback, 130, 250, 20));
    feedback.valid = true; feedback.angle = 130;
    // Default callers retain prior target-clamping semantics.
    assert(axis.moveFromFeedbackWithSpeed(feedback, 900, 250));
    axis.limits(100, 200);
    feedback.angle = 90;
    assert(axis.moveFromFeedbackWithSpeed(feedback, 150, 250, 20));
    assert(axis.command_start() == 100 && feedback.angle == 90);
    assert(!axis.moveFromFeedbackWithSpeed(feedback, 210, 250, 20));
    feedback.angle = 79;
    assert(!axis.moveFromFeedbackWithSpeed(feedback, 150, 250, 20));
}

static void trajectory(const std::vector<unsigned>& schedule, int target, bool expect_complete)
{
    test_tick_ms = 0;
    Axis axis;
    StartupPose pose;
    ServoFeedback feedback; feedback.valid = true; feedback.angle = 40;
    unsigned iteration = 0;
    bool started = false, completed = false, timed_out = false;
    for (;;) {
        const auto action = pose.step(int64_t(test_tick_ms) * 1000, true,
            !started && axis.canStartFromFeedback(feedback, target, 20), axis.isTrajectoryComplete());
        if (action == Action::Start) {
            assert(!started);
            started = axis.moveFromFeedbackWithSpeed(feedback, target, StartupPose::kSpeed, 20);
            assert(started && axis.command_start() == 50);
        }
        if (action == Action::Cancel) { axis.cancelMotion(); timed_out = true; break; }
        if (action == Action::Start || action == Action::Update) axis.update();
        if (std::strcmp(pose.state(), "trajectory_complete") == 0) { completed = true; break; }
        assert(test_tick_ms < 16000);
        test_tick_ms += schedule[iteration++ % schedule.size()];
    }
    assert(completed == expect_complete && timed_out != expect_complete);
    assert(started && feedback.angle == 40 && axis.reads == 0);
    assert(!axis.commands.empty());
    for (int command : axis.commands) assert(command >= 50 && command <= target);
    const auto trajectory_ms = test_tick_ms;
    if (completed) {
        assert(axis.commands.back() == target);
        // Automatic rest release still happens, after the 500ms arrival hold.
        test_tick_ms += 300; axis.update();
        assert(axis.torque); // held: the goal was written less than 500ms ago
        test_tick_ms += 400; axis.update();
        assert(!axis.torque);
    } else {
        assert(std::strcmp(pose.state(), "trajectory_timeout") == 0 && !axis.torque);
        assert(pose.step(int64_t(test_tick_ms + 1000) * 1000, true, true, true) == Action::None);
    }
    std::cout << "pitch target=" << target / 10.0 << " schedule=";
    for (unsigned ms : schedule) std::cout << ms << ',';
    std::cout << " result=" << pose.state() << " trajectory_ms=" << trajectory_ms
              << " outputs=" << axis.commands.size() << '\n';
}

static void terminal_states()
{
    StartupPose missing;
    assert(missing.step(0, true, false, true) == Action::None);
    assert(missing.step(5000000, true, true, true) == Action::None);
    assert(std::strcmp(missing.state(), "feedback_unavailable") == 0);
    assert(missing.step(6000000, true, true, true) == Action::None);
    StartupPose stopped;
    assert(stopped.step(0, true, true, true) == Action::Start);
    assert(stopped.step(10000, false, false, false) == Action::None);
    assert(stopped.step(20000, true, true, true) == Action::None);
    StartupPose rejected;
    assert(rejected.step(0, true, true, true) == Action::Start);
    rejected.start_rejected();
    assert(rejected.step(20000, true, true, true) == Action::None);
    assert(std::strcmp(rejected.state(), "start_rejected") == 0);
    StartupPose limit;
    limit.target_unavailable();
    assert(limit.step(0, true, true, true) == Action::None);
    assert(std::strcmp(limit.state(), "target_unavailable") == 0);
}

static void passive_yaw_first_command()
{
    Axis axis; axis.limits(-1280, 1280); axis.passive();
    assert(axis.commands.empty());
    // Reproduce the real passive Servo state that blocked the first web command.
    assert(!axis.isTrajectoryComplete());
    collector::ManualYaw request;
    assert(request.submit(-50, collector::kWebYawDefaultSpeed, false,  true, true, collector::manual_yaw_idle(false, axis.isTrajectoryComplete()),
                          40, 100, 100));
    assert(request.take(110, collector::manual_yaw_idle(false, axis.isTrajectoryComplete())));
    ServoFeedback feedback; feedback.valid = true; feedback.angle = 40;
    assert(axis.moveFromFeedbackWithSpeed(feedback, request.target, 100));
    request.finish(true);
    assert(!collector::manual_yaw_idle(true, axis.isTrajectoryComplete()));
    for (int i = 0; i < 1000 && !axis.isTrajectoryComplete(); ++i) { test_tick_ms += 20; axis.update(); }
    assert(axis.isTrajectoryComplete()); request.complete();
    assert(!axis.commands.empty() && axis.commands.back() == -10);
    assert(!std::strcmp(request.state, "trajectory_complete"));
}

// Exercise actual command trajectories; this cannot model payload mechanics.
static int finish_gentle(Axis& axis, int start, int target)
{
    int previous = start, peak_step = 0;
    const auto began = test_tick_ms;
    for (int i = 0; i < 750 && !axis.isTrajectoryComplete(); ++i) {
        test_tick_ms += 20; axis.update();
        const int current = axis.commands.back();
        peak_step = std::max(peak_step, std::abs(current - previous));
        assert(current >= std::min(start, target) && current <= std::max(start, target));
        assert(target >= start ? current >= previous : current <= previous);
        previous = current;
    }
    assert(axis.isTrajectoryComplete() && axis.commands.back() == target);
    assert(test_tick_ms - began < 15000);
    std::cerr << "range " << start << " -> " << target << " peak " << peak_step << " time " << test_tick_ms - began << '\n';
    assert(peak_step <= 10); // <=1 degree per 20ms, including final snap.
    test_tick_ms += 700; axis.update();
    assert(!axis.torque); // ideal arrival: released after the 500ms hold
    std::cout << "gentle " << start / 10.0 << " -> " << target / 10.0
              << " peak_step=" << peak_step / 10.0 << "deg elapsed_ms=" << test_tick_ms - began << '\n';
    return peak_step;
}

static void loaded_pitch_motion()
{
    Axis pitch;
    // Configure only this axis; a second axis retains its original mapping.
    collector::configure_pitch_motion(pitch);
    assert(!pitch.setSpeedSpringOverride(0, 4.5f));
    assert(!pitch.setSpeedSpringOverride(3.0f, -1));
    assert(!pitch.setSpeedSpringOverride(INFINITY, 4.5f));
    assert(!pitch.setSpeedSpringOverride(3.0f, NAN));
    ServoFeedback feedback; feedback.valid = true; feedback.angle = 50;
    assert(pitch.moveFromFeedbackWithSpeed(feedback, 850, StartupPose::kSpeed));
    const int gentle_step = finish_gentle(pitch, 50, 850);
    collector::RemoteCommand input{1,0,471,600,false};
    input.center_v1 = true;
    assert(collector::apply_remote_pitch(pitch, input, false, 100).pitch == 100);
    finish_gentle(pitch, 850, 100);

    Axis normal; normal.physical = 50; normal.passive();
    normal.moveWithSpeed(850, 600);
    int previous = 50, normal_step = 0;
    for (int i = 0; i < 750 && !normal.isTrajectoryComplete(); ++i) {
        test_tick_ms += 20; normal.update();
        normal_step = std::max(normal_step, std::abs(normal.commands.back() - previous));
        previous = normal.commands.back();
    }
    assert(normal.isTrajectoryComplete() && normal_step > gentle_step * 5);

    // A committed web setting cancels remote motion and restarts from feedback.
    // Cancelled velocity must not send a new downward request upward first.
    for (bool from_setting : {true, false}) {
        Axis takeover; collector::configure_pitch_motion(takeover);
        feedback.angle = 50;
        assert(takeover.moveFromFeedbackWithSpeed(feedback, 850, 600));
        for (int i = 0; i < 30; ++i) { test_tick_ms += 20; takeover.update(); }
        collector::apply_remote_pitch(takeover, input, false, 100);
        for (int i = 0; i < 5; ++i) { test_tick_ms += 20; takeover.update(); }
        feedback.angle = takeover.commands.back();
        if (from_setting) {
            takeover.cancelMotion();
            assert(takeover.moveFromFeedbackWithSpeed(feedback, 100, StartupPose::kSpeed));
        } else {
            takeover.physical = feedback.angle;
            collector::apply_remote_pitch(takeover, input, true, 100);
        }
        finish_gentle(takeover, feedback.angle, 100);
    }

    // Reversal during movement retains a smooth bounded trajectory.
    input.pitch = 900;
    collector::apply_remote_pitch(pitch, input, false, 100);
    for (int i = 0; i < 25; ++i) { test_tick_ms += 20; pitch.update(); }
    input.pitch = 471;
    previous = pitch.commands.back();
    collector::apply_remote_pitch(pitch, input, false, 100);
    for (int i = 0; i < 750 && !pitch.isTrajectoryComplete(); ++i) {
        test_tick_ms += 20; pitch.update();
        const int current = pitch.commands.back();
        assert(current >= 50 && current <= 850);
        if (std::abs(current - previous) > 10) std::cerr << "reverse jump i=" << i << " prev=" << previous << " curr=" << current << '\n';
        assert(std::abs(current - previous) <= 10);
        previous = current;
    }
    assert(pitch.isTrajectoryComplete() && pitch.commands.back() == 100);
    input.pitch = 900;
    collector::apply_remote_pitch(pitch, input, false, 100);
    test_tick_ms += 20; pitch.update();
    pitch.cancelMotion();
    const auto commands_at_stop = pitch.commands.size();
    // Even an extra update after cancellation cannot restart the profiled axis.
    for (int i = 0; i < 100; ++i) { test_tick_ms += 20; pitch.update(); }
    assert(!pitch.torque && pitch.commands.size() == commands_at_stop);
    feedback.angle = 100;
    assert(pitch.moveFromFeedbackWithSpeed(feedback, 150, 1000));
    finish_gentle(pitch, 100, 150);
}

int main()
{
    smooth_ui_toolkit::ui_hal::on_get_tick([] { return test_tick_ms; });
    { // Restored physical pitch uses the real factory spring, limits and release.
        Axis axis; axis.physical = 300; axis.passive();
        collector::RemoteCommand input{1,0,350,100,false};
        const auto target = collector::apply_remote_pitch(axis, input, true);
        assert(target.pitch == 350 && target.speed == 100);
        for (int i=0; i<1500; ++i) { test_tick_ms += 20; axis.update(); }
        assert(axis.isTrajectoryComplete() && !axis.commands.empty());
        assert(axis.commands.back() == 350 && !axis.torque);
        input.center_v1=true; input.pitch=471; // release joystick back to saved 10 degrees
        assert(collector::apply_remote_pitch(axis,input,false,100).pitch == 100);
        for (int i=0; i<1500; ++i) { test_tick_ms += 20; axis.update(); }
        assert(axis.isTrajectoryComplete() && axis.commands.back() == 100 && !axis.torque);
        for (int angle : axis.commands) assert(angle >= 50 && angle <= 850);
    }
    loaded_pitch_motion();
    storage_and_request_generation(); feedback_boundary(); terminal_states(); passive_yaw_first_command();
    trajectory({20}, 150, true);
    trajectory({19, 21}, 130, true);
    trajectory({40}, 850, true);
    trajectory({60}, 850, true);
    trajectory({200}, 850, false);
    std::cout << "PASS: pitch persistence errors, stop epochs, real Servo recovery/spring/limits/timeout/torque release\n";
}
