/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "servo.h"
#include <hal/hal.h>
#include <cmath>

using namespace uitk;

namespace stackchan::motion {

static SpringOptions_t _default_spring_options = {
    .stiffness = 170.0,
    .damping   = 26.0,

    .mass     = 1.0,
    .velocity = 0.0,

    .restSpeed = 0.1,
    .restDelta = 0.1,

    .duration       = 0.0,
    .bounce         = 0.0,
    .visualDuration = 0.0,
};

void Servo::init()
{
    apply_default_spring_options();

    _angle_anim.teleport(getCurrentAngle());
    update();

    setTorqueEnabled(false);
}

void Servo::initPassive()
{
    setTorqueEnabled(false);
    apply_default_spring_options();
    _angle_anim.teleport(getCurrentAngle());
    _snap_to_target_on_rest = false;
}

void Servo::update()
{
    // Keep update in at most 50Hz
    if (GetHAL().millis() - _last_tick < 20) {
        return;
    }
    _last_tick = GetHAL().millis();

    // Apply animation
    if (!_angle_anim.done()) {
        _angle_anim.updateWithDelta(0.02f);  // Fixed delta time for consistency
        set_angle_impl(static_cast<int>(_angle_anim.directValue()));
    }

    // Snap to target angle when animation ends
    else if (_snap_to_target_on_rest) {
        _snap_to_target_on_rest = false;
        set_angle_impl(_angle_anim.end);
    }

    // Auto release torque on rest
    else if (_auto_torque_release_enabled && !isMoving()) {
        if (GetHAL().millis() - _last_torque_check_tick > 200) {
            if (getTorqueEnabled()) {
                setTorqueEnabled(false);
            }
            _last_torque_check_tick = GetHAL().millis();
        }
    }
}

void Servo::move(int angle)
{
    apply_default_spring_options();
    update_angle_anim_target(angle);
}

void Servo::moveWithSpringParams(int angle, float stiffness, float damping)
{
    _angle_anim.springOptions().visualDuration = 0.0f;  // Disable timing override
    _angle_anim.springOptions().stiffness      = stiffness;
    _angle_anim.springOptions().damping        = damping;

    update_angle_anim_target(angle);
}

bool Servo::setSpeedSpringOverride(float stiffness, float damping)
{
    if (!std::isfinite(stiffness) || !std::isfinite(damping) || stiffness <= 0 || damping <= 0) return false;
    _speed_spring_stiffness = stiffness;
    _speed_spring_damping = damping;
    return true;
}

void Servo::moveWithSpeed(int angle, int speed)
{
    if (_speed_spring_stiffness > 0) {
        moveWithSpringParams(angle, _speed_spring_stiffness, _speed_spring_damping);
        return;
    }
    auto spring_options = map_speed_to_spring_options(speed);
    moveWithSpringParams(angle, spring_options.stiffness, spring_options.damping);
}

bool Servo::canStartFromFeedback(const ServoFeedback& feedback, int angle, int lower_margin) const
{
    if (lower_margin < 0 || lower_margin > 20 || !feedback.valid || feedback.angle > _angle_limit.y) return false;
    // Preserve the original default helper: the target is clamped by move().
    if (!lower_margin) return feedback.angle >= _angle_limit.x;
    // Recovery is explicit and inward only; it cannot bypass a dynamic limit.
    return angle >= _angle_limit.x && angle <= _angle_limit.y && feedback.angle >= _angle_limit.x - lower_margin;
}

bool Servo::moveFromFeedbackWithSpeed(const ServoFeedback& feedback, int angle, int speed, int lower_margin)
{
    if (!canStartFromFeedback(feedback, angle, lower_margin)) return false;
    stop_motion_at_angle(feedback.angle);
    const bool previous_sync = _auto_angle_sync_enabled;
    _auto_angle_sync_enabled = false;
    moveWithSpeed(angle, speed);
    _auto_angle_sync_enabled = previous_sync;
    return true;
}

int Servo::getCurrentAngle()
{
    return _angle_anim.directValue();
}

bool Servo::isMoving()
{
    return _angle_anim.done() == false || is_moving_impl();
}

void Servo::apply_default_spring_options()
{
    auto& options          = _angle_anim.springOptions();
    options.visualDuration = 0.0f;  // Disable timing override
    options.stiffness      = _default_spring_options.stiffness;
    options.damping        = _default_spring_options.damping;
}

void Servo::update_angle_anim_target(int angle)
{
    angle = uitk::clamp(angle, _angle_limit.x, _angle_limit.y);

    if (_auto_angle_sync_enabled) {
        if (_speed_spring_stiffness > 0) stop_motion_at_angle(getCurrentAngle());
        else _angle_anim.teleport(getCurrentAngle());  // Use current angle as start
    }
    if (_speed_spring_stiffness > 0) {
        // update() advances by fixed 20ms steps. AnimateValue::operator= calls
        // value(), adding wall-clock time again at retarget and jumping ahead.
        // Start from the last commanded value and preserve the spring velocity.
        if (angle != _angle_anim.end) _angle_anim.retarget(_angle_anim.directValue(), angle);
    } else {
        _angle_anim = angle;
    }
    _snap_to_target_on_rest = true;
}

void Servo::stop_motion_at_angle(int angle)
{
    angle = uitk::clamp(angle, _angle_limit.x, _angle_limit.y);
    if (_speed_spring_stiffness > 0) _angle_anim.springOptions().velocity = 0.0f;
    _angle_anim.teleport(angle);
    // teleport/init alone leaves the generator's cached velocity intact.
    // Evaluate the zero-distance, zero-velocity curve without writing the bus
    // so cancelled motion cannot leak into a new feedback-based start.
    if (_speed_spring_stiffness > 0) _angle_anim.updateWithDelta(0.0f);
    _snap_to_target_on_rest = false;
}

uitk::SpringOptions_t Servo::map_speed_to_spring_options(int speed)
{
    speed = uitk::clamp(speed, 0, 1000);

    // 1. 计算 Stiffness (刚度)
    // 使用二次方映射: k = k_min + (speed/1000)^2 * k_range
    // 当 speed=500 时，k 约为 10 + 0.25 * 640 = 170
    float k_min           = 10.0f;
    float k_max           = 650.0f;
    float normalizedSpeed = speed / 1000.0f;
    float stiffness       = k_min + (normalizedSpeed * normalizedSpeed) * (k_max - k_min);

    // 2. 计算 Damping (阻尼)
    // 为了保持临界阻尼(无过冲，最快稳定)，公式为 d = 2 * sqrt(m * k)
    // 如果想要带一点点弹性感(bounce)，可以将系数从 2.0 降到 1.5~1.8
    float mass    = 1.0f;
    float damping = 2.0f * sqrtf(mass * stiffness);

    // 3. 构造选项
    uitk::SpringOptions_t options = _default_spring_options;
    options.stiffness             = stiffness;
    options.damping               = damping;
    options.mass                  = mass;

    // 4. 动态调整静止阈值
    // 高速时阈值大一点可以防止由于离散计算导致的微小抖动
    if (speed > 800) {
        options.restDelta = 0.5f;
        options.restSpeed = 0.5f;
    } else {
        options.restDelta = 0.1f;
        options.restSpeed = 0.1f;
    }

    return options;
}

}  // namespace stackchan::motion
