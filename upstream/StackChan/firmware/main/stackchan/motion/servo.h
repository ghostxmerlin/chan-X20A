/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include <smooth_ui_toolkit.hpp>
#include <uitk/short_namespace.hpp>
#include <cstdint>
#include "../../hal/drivers/FTServo_Arduino/src/SCSDiagnostics.h"

namespace stackchan::motion {

using ServoDiagnostics = SCSDiagnostics;

struct ServoFeedback {
    bool valid = false;
    bool load_valid = false, current_valid = false;
    int angle = 0, position_raw = -1, load_raw = 0, current_raw = 0;
    // Present speed/voltage/temperature/moving from the same status block, -1 when not read.
    int speed_raw = 0, voltage_raw = -1, temperature_raw = -1, moving_raw = -1;
    int transport_error = 0, servo_status = 0;
};

struct ServoDeadbandSnapshot {
    bool supported = false, attempted = false, valid = false;
    int64_t timestamp_us = 0; // Monotonic transaction start, not a motion timestamp.
    int transport_error = 0, servo_status = 0;
    int cw_raw = -1, ccw_raw = -1; // Independent register bytes; unknown until valid.
    int min_angle_raw = -1, max_angle_raw = -1, phase_raw = -1, received_bytes = 0;
    // Remaining SCSCL EEPROM bytes of the same 9..27 block; -1 until valid.
    int max_temperature_raw = -1, max_voltage_raw = -1, min_voltage_raw = -1, max_torque_raw = -1;
    int unloading_raw = -1, led_alarm_raw = -1, p_raw = -1, d_raw = -1, i_raw = -1, punch_raw = -1;
};

// Operator-adjustable SCSCL EEPROM control parameters (registers 16..27).
// A field is written only when its has_ flag is set; values are register units.
struct ServoEepromTuning {
    bool has_p = false, has_d = false, has_i = false, has_punch = false;
    bool has_dead_cw = false, has_dead_ccw = false, has_max_torque = false;
    int p = 0, d = 0, i = 0, punch = 0, dead_cw = 0, dead_ccw = 0, max_torque = 0;
};
struct ServoEepromWriteResult {
    bool supported = false, attempted = false, unlocked = false, locked = false;
    int writes = 0, failed_writes = 0, failed_register = -1;
    int64_t timestamp_us = 0;
    ServoDeadbandSnapshot before, after;
};
// RAM-only goal profile: SCSCL goal time (ms) and goal speed (steps/s) sent
// with every position write. Factory firmware uses 20 ms / 0 (servo maximum).
struct ServoGoalProfile { int time_ms = 20; int speed = 0; };

struct ServoTorqueSnapshot {
    bool supported = false, attempted = false, valid = false;
    int64_t timestamp_us = 0; // Monotonic transaction start, not measured physical torque.
    int transport_error = 0, servo_status = 0, received_bytes = 0, raw = -1;
    int goal_position_raw = -1, goal_time_raw = -1, goal_speed_raw = -1;
};

// Explicit read-only register probe, scheduled solely by the bus owner. Keep
// unknown register bytes distinct from transport/status errors and from zero.
template<class Bus>
ServoTorqueSnapshot readServoTorqueSnapshot(Bus& bus, uint8_t id, int64_t timestamp_us)
{
    ServoTorqueSnapshot result;
    result.supported = true;result.attempted = true;result.timestamp_us = timestamp_us;
    const auto previous_timeout = bus.IOTimeOut;
    bus.IOTimeOut = 2;
    uint8_t values[8]{};
    result.received_bytes = bus.Read(id, 40, values, sizeof(values));
    result.transport_error = bus.getLastError();result.servo_status = bus.getState();
    bus.IOTimeOut = previous_timeout;
    result.valid = result.received_bytes == 8 &&
                   result.transport_error == 0 && result.servo_status == 0;
    if (result.valid) {
        result.raw = values[0];
        // SCSCL uses high-byte-first words, matching its default End=1.
        result.goal_position_raw = (uint16_t(values[2]) << 8) | values[3];
        result.goal_time_raw = (uint16_t(values[4]) << 8) | values[5];
        result.goal_speed_raw = (uint16_t(values[6]) << 8) | values[7];
    }
    return result;
}

// Invoked only by the motor task: at most three read transactions for each
// accepted tracking command. HTTP serializes this cached state only.
struct ServoTorqueCommandProbe {
    uint32_t command_count = 0;
    int target = 0;
    ServoFeedback start_feedback;
    ServoTorqueSnapshot before, after_first_write, before_cancel;
    ServoDiagnostics::Trace first_position_write;
    template<class Axis>
    void begin(Axis& axis, uint32_t count, int target_angle, const ServoFeedback& feedback)
    {
        *this = ServoTorqueCommandProbe{};
        command_count = count;target = target_angle;start_feedback = feedback;
        baseline_write_us = axis.diagnostics().last_position_write.timestamp_us;
        active = true;awaiting_write = true;
        before = axis.readTorqueSnapshot();
    }
    template<class Axis>
    void afterUpdate(Axis& axis)
    {
        if (!active || !awaiting_write) return;
        const auto write = axis.diagnostics().last_position_write;
        if (!write.valid || write.timestamp_us <= baseline_write_us) return;
        first_position_write = write;
        awaiting_write = false;
        after_first_write = axis.readTorqueSnapshot();
    }
    template<class Axis>
    void beforeCancel(Axis& axis)
    {
        if (!active) return;
        active = false;awaiting_write = false;
        before_cancel = axis.readTorqueSnapshot();
    }
private:
    uint64_t baseline_write_us = 0;
    bool active = false, awaiting_write = false;
};

// One read-only SCSCL configuration block: angle limits 9..12, phase 18,
// deadbands 26/27. No retry/unlock/write; restore the bus timeout after reading.
template<class Bus>
ServoDeadbandSnapshot readServoDeadbandSnapshot(Bus& bus, uint8_t id, int64_t timestamp_us)
{
    ServoDeadbandSnapshot result;
    result.supported = true;
    result.attempted = true;
    result.timestamp_us = timestamp_us;
    uint8_t values[19]{};
    const auto previous_timeout = bus.IOTimeOut;
    bus.IOTimeOut = 2;
    result.received_bytes = bus.Read(id, 9, values, sizeof(values));
    result.transport_error = bus.getLastError();
    result.servo_status = bus.getState();
    bus.IOTimeOut = previous_timeout;
    result.valid = result.received_bytes == 19 && result.transport_error == 0 && result.servo_status == 0;
    if (result.valid) {
        result.min_angle_raw = (uint16_t(values[0]) << 8) | values[1];
        result.max_angle_raw = (uint16_t(values[2]) << 8) | values[3];
        result.max_temperature_raw = values[4];  // 13
        result.max_voltage_raw = values[5];      // 14
        result.min_voltage_raw = values[6];      // 15
        result.max_torque_raw = (uint16_t(values[7]) << 8) | values[8]; // 16..17
        result.phase_raw = values[9];            // 18
        result.unloading_raw = values[10];       // 19
        result.led_alarm_raw = values[11];       // 20
        result.p_raw = values[12];               // 21
        result.d_raw = values[13];               // 22
        result.i_raw = values[14];               // 23
        result.punch_raw = (uint16_t(values[15]) << 8) | values[16]; // 24..25
        result.cw_raw = values[17];
        result.ccw_raw = values[18];
    }
    return result;
}

/**
 * @brief
 *
 */
class Servo {
public:
    virtual ~Servo() = default;

    /**
     * @brief
     *
     */
    virtual void init();

    /**
     * @brief
     *
     */
    virtual void update();

    /**
     * @brief Move to angle
     *
     * @param angle
     */
    void move(int angle);

    /**
     * @brief Move to angle with custom spring params
     *
     * @param angle
     * @param stiffness
     * @param damping
     */
    void moveWithSpringParams(int angle, float stiffness = 170.0f, float damping = 26.0f);

    /**
     * @brief Move to angle with speed mapping
     *
     * @param angle
     * @param speed (0-1000)
     */
    void moveWithSpeed(int angle, int speed);

    // Optional per-axis command profile. Configure before motion; this does not
    // tune the servo's internal PID, calibration, limits, or holding torque.
    bool setSpeedSpringOverride(float stiffness, float damping);

    /**
     * @brief Rotate servo with given velocity
     *
     * @param velocity (-1000, 1000)
     */
    virtual void rotate(int velocity)
    {
    }

    /**
     * @brief Get servo current angle
     *
     * @return int
     */
    // Checked, bounded rotation entry points for the collector motor owner.
    virtual bool beginTimedRotation(int) { return false; }
    virtual bool endTimedRotation() { return false; }
    virtual void cutRotationPower() {}
    virtual bool rotationPowerFaulted() const { return false; }

    virtual int getCurrentAngle();

    // A failed bus read must never be presented as a measured angle.
    virtual ServoFeedback readFeedback() { return {}; }
    // Passive counters copied by the bus-owning motor task; never queries UART.
    virtual ServoDiagnostics diagnostics() const { return {}; }
    // Explicit bus query; never call from HTTP or a snapshot reader.
    virtual ServoDeadbandSnapshot readDeadbandSnapshot() { return {}; }
    virtual ServoTorqueSnapshot readTorqueSnapshot() { return {}; }
    // Bus-owner only: unlock, write the flagged registers, lock, re-read 9..27.
    virtual ServoEepromWriteResult writeEepromTuning(const ServoEepromTuning&) { return {}; }
    virtual void setGoalProfile(const ServoGoalProfile&) {}
    virtual ServoGoalProfile goalProfile() const { return {}; }
    // Automatic release policy (see release_allowed); RAM only.
    void setReleasePolicy(uint32_t min_hold_ms, uint32_t max_hold_ms, int tolerance_tenths);
    uint32_t releaseMinHoldMs() const { return _release_min_hold_ms; }
    uint32_t releaseMaxHoldMs() const { return _release_max_hold_ms; }
    int releaseToleranceTenths() const { return _release_tolerance_tenths; }
    void clearSpeedSpringOverride() { _speed_spring_stiffness = 0.0f; _speed_spring_damping = 0.0f; }
    float speedSpringStiffness() const { return _speed_spring_stiffness; }
    float speedSpringDamping() const { return _speed_spring_damping; }
    // An explicit cancel releases immediately; it also ends any arrival hold.
    bool holdPending() const { return _hold_pending; }
    void cancelMotion() { setTorqueEnabled(false); stop_motion_at_angle(Servo::getCurrentAngle()); _hold_pending = false; }
    // Start an automatic pose from a validated sample, without a second UART
    // read that could substitute an internal fallback angle for the start point.
    // An explicit margin permits at most 2 degrees below the current lower
    // limit, using that limit as the command start. Feedback remains unchanged.
    bool canStartFromFeedback(const ServoFeedback& feedback, int angle, int lower_margin = 0) const;
    bool moveFromFeedbackWithSpeed(const ServoFeedback& feedback, int angle, int speed, int lower_margin = 0);
    bool isTrajectoryComplete() { return _angle_anim.done() && !_snap_to_target_on_rest; }

    /**
     * @brief
     *
     * @return uitk::Vector2i
     */
    virtual uitk::Vector2i getAngleLimit() const
    {
        return _angle_limit;
    }

    /**
     * @brief
     *
     * @return true
     * @return false
     */
    bool isMoving();

    /**
     * @brief
     *
     * @param enabled
     */
    virtual void setTorqueEnabled(bool enabled)
    {
        if (enabled) _rest_released = false;
    }
    // -1 unknown (read failed), 0 off, 1 on. Only a confirmed 0 ends rest polling.
    virtual int readTorqueState() { return getTorqueEnabled() ? 1 : 0; }
    virtual bool getTorqueEnabled()
    {
        return false;
    }

    /**
     * @brief Auto release torque on rest
     *
     * @param enabled
     */
    void setAutoTorqueReleaseEnabled(bool enabled)
    {
        _auto_torque_release_enabled = enabled;
    }

    /**
     * @brief Enables or disables automatic synchronization of the animation start point
     *        with the current physical angle.
     *
     * @param enabled
     *        - If true: Prevents sudden "jumps" when the servo is moved manually by
     *          external forces, but may cause stuttering during high-frequency updates
     *          as it resets the animation's velocity.
     *        - If false: Maintains animation momentum and velocity for smooth,
     *          continuous motion, but may cause a "snap" if the actual angle differs
     *          significantly from the internal state.
     */
    void setAutoAngleSyncEnabled(bool enabled)
    {
        _auto_angle_sync_enabled = enabled;
    }

    /**
     * @brief
     *
     */
    virtual void setCurrentAngleAsZero()
    {
    }

    /**
     * @brief
     *
     */
    virtual void resetZeroCalibration()
    {
    }

protected:
    // Prepare interpolation without writing a target or enabling torque.
    void initPassive();

    Servo()
    {
    }

    void set_angle_limit(uitk::Vector2i angleLimit)
    {
        _angle_limit = angleLimit;
    }

    /**
     * @brief Servo set angle implementation
     *
     * @param angle
     */
    virtual void set_angle_impl(int angle) = 0;
    virtual bool is_moving_impl()
    {
        return false;
    }

protected:
    uitk::Vector2i _angle_limit;
    uitk::AnimateValue _angle_anim;

    uint32_t _last_tick               = 0;
    uint32_t _last_torque_check_tick  = 0;
    // Automatic rest release waits for measured arrival after the last goal
    // write instead of releasing on the first idle tick. A loaded shaft needs
    // time to close its final error; the hold is bounded so an unreachable
    // goal or a failed read still ends in the factory release.
    uint32_t _release_min_hold_ms = 500;
    uint32_t _release_max_hold_ms = 2000;
    int _release_tolerance_tenths = 10; // 1.0 degree, about 3 encoder steps
    uint32_t _last_goal_write_tick    = 0;
    bool _hold_pending                = false;
    bool _rest_released               = false; // release at rest confirmed; no bus polling until the next goal
    bool _snap_to_target_on_rest      = false;
    bool _auto_torque_release_enabled = true;
    bool _auto_angle_sync_enabled     = true;
    float _speed_spring_stiffness = 0.0f;
    float _speed_spring_damping = 0.0f;

    void apply_default_spring_options();
    void note_goal_write();
    bool release_allowed(uint32_t now);
    void update_angle_anim_target(int angle);
    void stop_motion_at_angle(int angle);
    uitk::SpringOptions_t map_speed_to_spring_options(int speed);
};

}  // namespace stackchan::motion
