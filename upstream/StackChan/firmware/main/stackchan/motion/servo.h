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
    int transport_error = 0, servo_status = 0;
};

struct ServoDeadbandSnapshot {
    bool supported = false, attempted = false, valid = false;
    int64_t timestamp_us = 0; // Monotonic transaction start, not a motion timestamp.
    int transport_error = 0, servo_status = 0;
    int cw_raw = -1, ccw_raw = -1; // Independent register bytes; unknown until valid.
};

// Read-only SCSCL registers 26 and 27. The caller owns the bus and schedules
// this once; no retry, unlock, write, or change to the existing bus timeout.
template<class Bus>
ServoDeadbandSnapshot readServoDeadbandSnapshot(Bus& bus, uint8_t id, int64_t timestamp_us)
{
    ServoDeadbandSnapshot result;
    result.supported = true;
    result.attempted = true;
    result.timestamp_us = timestamp_us;
    uint8_t values[2]{};
    const int received = bus.Read(id, 26, values, sizeof(values));
    result.transport_error = bus.getLastError();
    result.servo_status = bus.getState();
    result.valid = received == 2 && result.transport_error == 0 && result.servo_status == 0;
    if (result.valid) {
        result.cw_raw = values[0];
        result.ccw_raw = values[1];
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
    virtual int getCurrentAngle();

    // A failed bus read must never be presented as a measured angle.
    virtual ServoFeedback readFeedback() { return {}; }
    // Passive counters copied by the bus-owning motor task; never queries UART.
    virtual ServoDiagnostics diagnostics() const { return {}; }
    // Explicit bus query; never call from HTTP or a snapshot reader.
    virtual ServoDeadbandSnapshot readDeadbandSnapshot() { return {}; }
    void cancelMotion() { setTorqueEnabled(false); stop_motion_at_angle(Servo::getCurrentAngle()); }
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
    }
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
    bool _snap_to_target_on_rest      = false;
    bool _auto_torque_release_enabled = true;
    bool _auto_angle_sync_enabled     = true;
    float _speed_spring_stiffness = 0.0f;
    float _speed_spring_damping = 0.0f;

    void apply_default_spring_options();
    void update_angle_anim_target(int angle);
    void stop_motion_at_angle(int angle);
    uitk::SpringOptions_t map_speed_to_spring_options(int speed);
};

}  // namespace stackchan::motion
