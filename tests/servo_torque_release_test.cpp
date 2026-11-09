/* SPDX-License-Identifier: MIT */
// Real Servo spring + update loop with a substituted clock. Models the loaded
// yaw servo observed on the device: position reads are explicit feedback, the
// servo reports moving=0, and small goal steps do not move the shaft.
#include "../upstream/StackChan/firmware/main/stackchan/motion/servo.h"
#include "../upstream/StackChan/firmware/main/collector/pitch_motion.h"
#include <hal/hal.h>
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <vector>

uint32_t test_tick_ms = 0;
TestHal& GetHAL() { static TestHal h; return h; }
using namespace stackchan::motion;

struct LoadedAxis : Servo {
    struct Write { uint32_t ms; int angle; };
    std::vector<Write> writes;
    int physical = 12; // tenths: raw465 with zero 461, as on the device
    bool torque = false, feedback_valid = true, moving_flag = false;
    int feedback_reads = 0, releases = 0;
    uint32_t release_ms = 0, final_write_ms = 0;
    LoadedAxis() { set_angle_limit({-1280, 1280}); initPassive(); feedback_reads = 0; }
    int getCurrentAngle() override { return physical; } // factory fallback, not evidence
    ServoFeedback readFeedback() override
    {
        ++feedback_reads;
        ServoFeedback f; f.valid = feedback_valid; f.angle = physical; f.position_raw = 461 + physical * 16 / 5 / 10;
        return f;
    }
    void setTorqueEnabled(bool enabled) override
    {
        if (torque && !enabled) { ++releases; release_ms = test_tick_ms; }
        torque = enabled;
    }
    bool getTorqueEnabled() override { ++torque_reads; return torque; }
    int torque_reads = 0, moving_reads = 0; bool torque_read_fails = false;
    int readTorqueState() override { ++torque_reads; return torque_read_fails ? -1 : torque ? 1 : 0; }
    void tick(uint32_t advance = 20) { test_tick_ms += advance; update(); }
    // Run the spring until its final snap write, then return that time.
    uint32_t run_to_final(int target, uint32_t limit_ms = 8000)
    {
        const uint32_t began = test_tick_ms;
        while (test_tick_ms - began < limit_ms) {
            tick();
            if (!writes.empty() && writes.back().angle == target && isTrajectoryComplete()) return final_write_ms;
        }
        assert(!"trajectory never wrote its final target");
        return 0;
    }
protected:
    void set_angle_impl(int angle) override { writes.push_back({test_tick_ms, angle}); torque = true; final_write_ms = test_tick_ms; }
    bool is_moving_impl() override { ++moving_reads; return moving_flag; }
};



static ServoFeedback feedback_at(int angle)
{
    ServoFeedback f; f.valid = true; f.angle = angle; f.position_raw = 461 + angle * 16 / 5 / 10; return f;
}

// 2026-11-09: once release at rest is confirmed (torque read back as 0) the axis stops polling
// ReadMove / ReadTorqueEnable until the next goal write; a failed torque read never counts as
// released. The pitch axis had been read ~20 times a second forever after its boot pose.
static void rest_polling_stops_after_confirmed_release()
{
    test_tick_ms = 100000;
    LoadedAxis axis; axis.physical = 0; axis.feedback_valid = true;
    axis.setReleasePolicy(500, 1500, 60);
    axis.moveFromFeedbackWithSpeed(feedback_at(0), 200, 1000);
    axis.physical = 200;
    for (int i = 0; i < 200; ++i) axis.tick();          // trajectory, hold, release, confirm
    assert(axis.releases == 1 && !axis.torque);
    const int moving = axis.moving_reads, torque = axis.torque_reads, feedback = axis.feedback_reads;
    for (int i = 0; i < 500; ++i) axis.tick();          // 10 s at rest
    assert(axis.moving_reads == moving && axis.torque_reads == torque && axis.feedback_reads == feedback);
    // The next goal resumes the rest checks.
    axis.moveFromFeedbackWithSpeed(feedback_at(200), 0, 1000); axis.physical = 0;
    for (int i = 0; i < 200; ++i) axis.tick();
    assert(axis.releases == 2 && axis.moving_reads > moving);
    // A torque read that fails is not a confirmed release: polling continues.
    LoadedAxis noisy; noisy.physical = 0; noisy.setReleasePolicy(500, 1500, 60);
    noisy.moveFromFeedbackWithSpeed(feedback_at(0), 200, 1000); noisy.physical = 200;
    for (int i = 0; i < 200; ++i) noisy.tick();
    noisy.torque_read_fails = true; const int before = noisy.torque_reads;
    for (int i = 0; i < 100; ++i) noisy.tick();
    if (noisy.releases == 0) assert(noisy.torque_reads > before);   // still trying to confirm
    noisy.torque_read_fails = false;
    for (int i = 0; i < 50; ++i) noisy.tick();
    const int settled = noisy.torque_reads;
    for (int i = 0; i < 100; ++i) noisy.tick();
    assert(noisy.torque_reads == settled && !noisy.torque);
    std::cout << "PASS: rest polling stops after a confirmed release and resumes on the next goal\n";
}

static void web_yaw_hold_when_shaft_lags()
{
    // Device case: raw465 -> -3.8 degrees, speed 100, shaft never follows.
    test_tick_ms = 1000; LoadedAxis yaw;
    assert(yaw.moveFromFeedbackWithSpeed(feedback_at(12), -38, 100));
    const auto final_ms = yaw.run_to_final(-38);
    assert(yaw.torque && yaw.releases == 0);
    for (int i = 0; i < 200 && yaw.releases == 0; ++i) yaw.tick();
    assert(yaw.releases == 1);
    const auto held = yaw.release_ms - final_ms;
    std::cout << "lagging shaft: final write at +" << final_ms - 1000 << "ms, torque released " << held << "ms later\n";
    // The stock loop released ~20-40ms after the final goal (device: 35.4ms).
    assert(held >= 500);  // keep torque long enough for a loaded shaft to move
    assert(held <= 2200); // but never hold indefinitely when it cannot arrive
    assert(yaw.writes.back().angle == -38 && yaw.writes.back().ms == final_ms); // no extra goal writes
    assert(yaw.feedback_reads >= 1); // release decision consulted measured position
    std::cout << "PASS: lagging shaft keeps torque >=500ms and still releases by 2.2s\n";
}

static void arrival_releases_after_minimum_hold()
{
    for (int arrived : {-38, -28, -37}) { // exact, 1.0 degree short, 0.1 short
        test_tick_ms = 1000; LoadedAxis yaw;
        assert(yaw.moveFromFeedbackWithSpeed(feedback_at(12), -38, 100));
        const auto final_ms = yaw.run_to_final(-38);
        yaw.tick(100); yaw.tick(100);
        yaw.physical = arrived; // shaft reaches the goal 200ms after the final write
        for (int i = 0; i < 200 && yaw.releases == 0; ++i) yaw.tick();
        const auto held = yaw.release_ms - final_ms;
        assert(yaw.releases == 1 && held >= 500 && held <= 760);
    }
    std::cout << "PASS: measured arrival within 1 degree releases torque after the 500ms minimum hold\n";
}

static void outside_tolerance_or_invalid_feedback_holds_to_limit()
{
    for (int variant = 0; variant < 2; ++variant) {
        test_tick_ms = 1000; LoadedAxis yaw;
        assert(yaw.moveFromFeedbackWithSpeed(feedback_at(12), -38, 100));
        const auto final_ms = yaw.run_to_final(-38);
        if (variant == 0) yaw.physical = -25;          // 1.3 degrees short: not arrived
        else { yaw.physical = -38; yaw.feedback_valid = false; } // cannot verify
        for (int i = 0; i < 200 && yaw.releases == 0; ++i) yaw.tick();
        const auto held = yaw.release_ms - final_ms;
        assert(yaw.releases == 1 && held >= 2000 && held <= 2220);
    }
    std::cout << "PASS: unverified arrival holds torque to the 2s limit, then releases\n";
}

static void moving_flag_defers_release()
{
    test_tick_ms = 1000; LoadedAxis yaw; yaw.moving_flag = true;
    assert(yaw.moveFromFeedbackWithSpeed(feedback_at(12), -38, 100));
    const auto final_ms = yaw.run_to_final(-38);
    yaw.physical = -38;
    for (int i = 0; i < 150; ++i) yaw.tick(); // 3s of moving=1
    assert(yaw.releases == 0 && yaw.torque);
    yaw.moving_flag = false;
    for (int i = 0; i < 20 && yaw.releases == 0; ++i) yaw.tick();
    assert(yaw.releases == 1 && yaw.release_ms > final_ms + 3000);
    std::cout << "PASS: a servo reporting moving=1 is never released\n";
}

static void cancel_clears_hold_and_idle_axis_reads_nothing()
{
    test_tick_ms = 1000; LoadedAxis yaw;
    assert(yaw.moveFromFeedbackWithSpeed(feedback_at(12), -38, 100));
    for (int i = 0; i < 10; ++i) yaw.tick();
    assert(yaw.torque);
    yaw.cancelMotion();
    assert(!yaw.torque && yaw.releases == 1);
    const auto writes = yaw.writes.size(); const int reads = yaw.feedback_reads;
    for (int i = 0; i < 150; ++i) yaw.tick();
    assert(yaw.writes.size() == writes && yaw.feedback_reads == reads && yaw.releases == 1);
    test_tick_ms = 1000; LoadedAxis idle; idle.cancelMotion(); // settled, torque off, nothing commanded
    for (int i = 0; i < 150; ++i) idle.tick();
    assert(idle.writes.empty() && idle.feedback_reads == 0 && idle.releases == 0 && !idle.torque);
    std::cout << "PASS: cancel releases immediately and clears the hold; a settled idle axis issues no reads\n";
}

static void new_command_during_hold_restarts_hold()
{
    test_tick_ms = 1000; LoadedAxis yaw;
    assert(yaw.moveFromFeedbackWithSpeed(feedback_at(12), -38, 100));
    yaw.run_to_final(-38);
    yaw.tick(100); yaw.tick(100);
    assert(yaw.torque && yaw.releases == 0 && yaw.isTrajectoryComplete());
    yaw.physical = -38;
    assert(yaw.moveFromFeedbackWithSpeed(feedback_at(-38), 12, 600)); // back to the right
    const auto second_final = yaw.run_to_final(12);
    assert(yaw.releases == 0);
    yaw.physical = 12;
    for (int i = 0; i < 200 && yaw.releases == 0; ++i) yaw.tick();
    const auto held = yaw.release_ms - second_final;
    assert(yaw.releases == 1 && held >= 500 && held <= 760);
    std::cout << "PASS: a second command during the hold keeps torque and restarts the hold\n";
}

static void pitch_profile_uses_same_release_rule()
{
    test_tick_ms = 1000; LoadedAxis pitch; collector::configure_pitch_motion(pitch);
    pitch.physical = 100;
    assert(pitch.moveFromFeedbackWithSpeed(feedback_at(100), 150, 250));
    const auto final_ms = pitch.run_to_final(150, 20000);
    pitch.physical = 141; // 0.9 degrees short, gravity sag
    for (int i = 0; i < 200 && pitch.releases == 0; ++i) pitch.tick();
    const auto held = pitch.release_ms - final_ms;
    assert(pitch.releases == 1 && held >= 500 && held <= 760);
    std::cout << "PASS: gentle_load pitch profile shares the arrival-gated release\n";
}

int main()
{
    web_yaw_hold_when_shaft_lags();
    arrival_releases_after_minimum_hold();
    outside_tolerance_or_invalid_feedback_holds_to_limit();
    moving_flag_defers_release();
    cancel_clears_hold_and_idle_axis_reads_nothing();
    new_command_during_hold_restarts_hold();
    pitch_profile_uses_same_release_rule();
    rest_polling_stops_after_confirmed_release();
    std::cout << "PASS: torque release waits for measured arrival or a bounded hold\n";
}
