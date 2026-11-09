/* SPDX-License-Identifier: MIT */
#pragma once
#include "../stackchan/motion/servo.h"
#include <cstddef>
#include <cstdint>

namespace collector {
// Bounded per-axis record of measured positions around one motion command.
// Samples are the motor task's existing 100 ms feedback reads, so the trace
// adds no UART traffic. Only motor_task writes it; HTTP serializes the copy
// under the remote mutex. Timestamps are esp_timer microseconds, comparable
// with the cached goal-write and torque-release transactions.
struct MotionTrace {
    static constexpr size_t kCapacity = 160; // 16 s at the 100 ms feedback cadence
    struct Sample {
        int64_t timestamp_us = 0; int position_raw = -1; int angle = 0; bool valid = false;
        int load_raw = 0, current_raw = 0, voltage_raw = -1, temperature_raw = -1, moving_raw = -1;
    };
    const char* source = "none";
    uint32_t sequence = 0;
    int target = 0;
    int64_t started_us = 0, closed_us = 0;
    stackchan::motion::ServoFeedback start;
    bool active = false, truncated = false;
    size_t count = 0;
    Sample samples[kCapacity];
    stackchan::motion::ServoDiagnostics::Trace last_goal_write, last_torque_disable;
    void begin(const char* from, uint32_t seq, int target_angle, const stackchan::motion::ServoFeedback& feedback, int64_t now)
    {
        source = from; sequence = seq; target = target_angle; started_us = now; closed_us = 0;
        start = feedback; active = true; truncated = false; count = 0;
        last_goal_write = {}; last_torque_disable = {};
    }
    void sample(const stackchan::motion::ServoFeedback& feedback, int64_t now)
    {
        if (!active) return;
        if (count >= kCapacity) { truncated = true; active = false; closed_us = now; return; }
        auto& entry = samples[count++];
        entry.timestamp_us = now; entry.valid = feedback.valid;
        entry.position_raw = feedback.position_raw; entry.angle = feedback.angle;
        entry.load_raw = feedback.load_raw; entry.current_raw = feedback.current_raw;
        entry.voltage_raw = feedback.voltage_raw; entry.temperature_raw = feedback.temperature_raw;
        entry.moving_raw = feedback.moving_raw;
    }
    // Copy the bus owner's cached transactions that belong to this command.
    void note(const stackchan::motion::ServoDiagnostics& diagnostics)
    {
        if (!active) return;
        const auto& write = diagnostics.last_position_write;
        if (write.valid && write.timestamp_us >= static_cast<uint64_t>(started_us)) last_goal_write = write;
        const auto& release = diagnostics.last_torque_disable;
        if (release.valid && release.timestamp_us >= static_cast<uint64_t>(started_us)) last_torque_disable = release;
    }
    void close(int64_t now) { if (active) { active = false; closed_us = now; } }
};
}
