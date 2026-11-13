/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal.h"
#include "drivers/FTServo_Arduino/src/SCSCL.h"
#include <stackchan/stackchan.h>
#include <smooth_ui_toolkit.hpp>
#include <mooncake_log.h>
#include <settings.h>
#include <stackchan/motion/timed_rotation_io.h>
#include <esp_timer.h>

using namespace smooth_ui_toolkit;
using namespace stackchan::motion;

static SCSCL _scs_bus;
static bool _rotation_power_fault = false; // shared VM; never auto re-enable after a stop fault

struct ServoConfig_t {
    int id             = -1;
    int defaultZeroPos = 0;
    Vector2i angleLimit;
    Vector2i rawPosLimit;
    std::string settingNs;
    std::string settingZeroPositionKey;
    bool enablePwmMode = false;
    bool enableStallProtection = false;
    bool persistDefaults = true;
};

class ScsServo : public Servo {
public:
    static inline const std::string _tag = "ScsServo";

    ScsServo(const ServoConfig_t& config) : _config(config), _runtime_raw_pos_limit(config.rawPosLimit)
    {
    }

    void init() override
    {
        reset_runtime_limits();
        set_angle_limit(_config.angleLimit);
        get_zero_pos_from_nvs();
        if (!_config.persistDefaults) {
            // ESP soft reset need not reset the separately powered SCS0009.
            // Never trust the software Mode default when wheel mode survived.
            const auto timeout = _scs_bus.IOTimeOut; _scs_bus.IOTimeOut = 10;
            // Cold boot: VM is switched on only 200 ms before this check, and a
            // servo that has not started yet does not answer. Silence is not wheel
            // mode: wait up to ~2 s for an answer (2026-11-13 white screen, when
            // silence cut VM for good). Only a real wheel-mode answer cuts power.
            bool replied = false;
            for (int attempt = 0; attempt < 20 && !replied; ++attempt) {
                const int low = _scs_bus.readWord(_config.id, 9);
                replied = low >= 0 && TimedRotationIO::good(_scs_bus);
                if (!replied) vTaskDelay(pdMS_TO_TICKS(100));
            }
            if (replied) {
                setTorqueEnabled(false);
                _position_mode_verified = TimedRotationIO::positionMode(_scs_bus, _config.id);
                if (!_position_mode_verified) _position_mode_verified = TimedRotationIO::positionMode(_scs_bus, _config.id);
            }
            _scs_bus.IOTimeOut = timeout;
            if (replied && !_position_mode_verified) cutRotationPower();
            else if (!replied) mclog::tagWarn(_tag, "id: {} no reply at boot; motion stays off, servo power left on", _config.id);
        }
        if (_config.persistDefaults) Servo::init();
        else initPassive();
    }

    void get_zero_pos_from_nvs()
    {
        _zero_pos     = _config.defaultZeroPos;
        bool is_valid = false;

        {
            Settings settings(_config.settingNs, false);
            int nvs_zero_pos = settings.GetInt(_config.settingZeroPositionKey, -1);

            // Limit check
            if (nvs_zero_pos >= _config.rawPosLimit.x && nvs_zero_pos <= _config.rawPosLimit.y) {
                _zero_pos = nvs_zero_pos;
                is_valid  = true;
                mclog::tagInfo(_tag, "id: {} get zero pos: {} from settings", _config.id, _zero_pos);
            } else {
                is_valid = false;
                mclog::tagWarn(_tag, "id: {} get invalid zero pos: {} from settings", _config.id, nvs_zero_pos);
            }
        }

        if (!is_valid) {
            _zero_pos = _config.defaultZeroPos;
            mclog::tagInfo(_tag, "id: {} override zero pos to default: {}", _config.id, _zero_pos);

            if (_config.persistDefaults) {
                Settings settings(_config.settingNs, true);
                settings.SetInt(_config.settingZeroPositionKey, _zero_pos);
            }
        }
    }

    void set_angle_impl(int angle) override
    {
        if (_rotation_power_fault || !_position_mode_verified) return;
        int mapped_angle = _zero_pos + angle * 16 / 5 / 10;  // 一步对应 0.3125度, 0.3125 = 5/16
        mapped_angle     = uitk::clamp(mapped_angle, _runtime_raw_pos_limit.x, _runtime_raw_pos_limit.y);

        // mclog::tagInfo(_tag, "id: {} mapped angle: {}", _id, mapped_angle);

        if (update_stall_protection(mapped_angle)) {
            return;
        }

        check_mode(Mode::Position);
        _scs_bus.WritePos(_config.id, mapped_angle, _goal_profile.time_ms, _goal_profile.speed);
    }

    void setGoalProfile(const ServoGoalProfile& profile) override
    {
        _goal_profile.time_ms = uitk::clamp(profile.time_ms, 0, 9999);
        _goal_profile.speed   = uitk::clamp(profile.speed, 0, 1000);
    }
    ServoGoalProfile goalProfile() const override { return _goal_profile; }

    ServoEepromWriteResult writeEepromTuning(const ServoEepromTuning& tuning) override
    {
        ServoEepromWriteResult result;
        result.supported = result.attempted = true;
        result.timestamp_us = esp_timer_get_time();
        result.before = readDeadbandSnapshot();
        const auto previous_timeout = _scs_bus.IOTimeOut;
        _scs_bus.IOTimeOut = 10;
        auto note = [&](int reg, int ok) { ++result.writes; if (ok != 1) { ++result.failed_writes; if (result.failed_register < 0) result.failed_register = reg; } };
        result.unlocked = _scs_bus.unLockEprom(_config.id) == 1;
        if (result.unlocked) {
            if (tuning.has_max_torque) note(16, _scs_bus.writeWord(_config.id, 16, uitk::clamp(tuning.max_torque, 0, 1000)));
            if (tuning.has_p) note(21, _scs_bus.writeByte(_config.id, 21, uitk::clamp(tuning.p, 0, 254)));
            if (tuning.has_d) note(22, _scs_bus.writeByte(_config.id, 22, uitk::clamp(tuning.d, 0, 254)));
            if (tuning.has_i) note(23, _scs_bus.writeByte(_config.id, 23, uitk::clamp(tuning.i, 0, 254)));
            if (tuning.has_punch) note(24, _scs_bus.writeWord(_config.id, 24, uitk::clamp(tuning.punch, 0, 1000)));
            if (tuning.has_dead_cw) note(26, _scs_bus.writeByte(_config.id, 26, uitk::clamp(tuning.dead_cw, 0, 16)));
            if (tuning.has_dead_ccw) note(27, _scs_bus.writeByte(_config.id, 27, uitk::clamp(tuning.dead_ccw, 0, 16)));
            result.locked = _scs_bus.LockEprom(_config.id) == 1;
        }
        _scs_bus.IOTimeOut = previous_timeout;
        result.after = readDeadbandSnapshot();
        mclog::tagInfo(_tag, "id: {} eeprom tuning writes: {} failed: {} unlocked: {} locked: {}", _config.id,
                       result.writes, result.failed_writes, result.unlocked, result.locked);
        return result;
    }

    int getCurrentAngle() override
    {
        int current_pos = _scs_bus.ReadPos(_config.id);
        if (!is_raw_pos_valid(current_pos)) {
            int fallback_angle = uitk::clamp(Servo::getCurrentAngle(), getAngleLimit().x, getAngleLimit().y);
            mclog::tagWarn(_tag, "id: {} ignore invalid current pos: {}, fallback angle: {}", _config.id, current_pos,
                           fallback_angle);
            return fallback_angle;
        }

        int angle = raw_pos_to_angle(current_pos);
        angle     = uitk::clamp(angle, getAngleLimit().x, getAngleLimit().y);
        // mclog::tagInfo(_tag, "id: {} current pos: {} angle: {}", _id, current_pos, angle);
        return angle;
    }

    ServoFeedback readFeedback() override
    {
        ServoFeedback result;
        // Collector observation is best-effort and must not monopolize the
        // factory motion loop. Keep its read timeout local to this transaction.
        const auto previous_timeout = _scs_bus.IOTimeOut;
        _scs_bus.IOTimeOut = 2;
        // One 15-byte read of registers 56..70: position, speed, load, voltage,
        // temperature, moving and current. Same cadence as the former 2-byte read.
        const bool block = _scs_bus.FeedBack(_config.id) > 0;
        result.transport_error = _scs_bus.getLastError();
        result.servo_status = _scs_bus.getState();
        _scs_bus.IOTimeOut = previous_timeout;
        if (block) {
            result.position_raw = _scs_bus.ReadPos(-1);
            result.speed_raw = _scs_bus.ReadSpeed(-1);
            result.load_raw = _scs_bus.ReadLoad(-1);
            result.voltage_raw = _scs_bus.ReadVoltage(-1);
            result.temperature_raw = _scs_bus.ReadTemper(-1);
            result.moving_raw = _scs_bus.ReadMove(-1);
            result.current_raw = _scs_bus.ReadCurrent(-1);
        }
        result.valid = !_rotation_power_fault && (_position_mode_verified || _timed_rotation.saved) && block && result.transport_error == 0 && result.servo_status == 0 && is_raw_pos_valid(result.position_raw);
        if (result.valid) {
            result.angle = raw_pos_to_angle(result.position_raw);
            result.load_valid = result.current_valid = true;
        }
        return result;
    }

    ServoDiagnostics diagnostics() const override { return _scs_bus.getDiagnostics(_config.id); }

    ServoDeadbandSnapshot readDeadbandSnapshot() override
    {
        return readServoDeadbandSnapshot(_scs_bus, static_cast<uint8_t>(_config.id), esp_timer_get_time());
    }

    ServoTorqueSnapshot readTorqueSnapshot() override
    {
        return readServoTorqueSnapshot(_scs_bus, static_cast<uint8_t>(_config.id), esp_timer_get_time());
    }

    bool is_moving_impl() override
    {
        int moving = _scs_bus.ReadMove(_config.id);
        // mclog::tagInfo(_tag, "id: {} moving: {}", _id, moving);
        return moving != 0;
    }

    void setTorqueEnabled(bool enabled) override
    {
        if (enabled && (_rotation_power_fault || !_position_mode_verified)) return;
        Servo::setTorqueEnabled(enabled);
        _scs_bus.EnableTorque(_config.id, enabled ? 1 : 0);
        // mclog::tagInfo(_tag, "id: {} set torque: {}", _id, enabled);
    }

    int readTorqueState() override
    {
        const int torque_enable = _scs_bus.ReadToqueEnable(_config.id); // -1 when the read failed
        return torque_enable < 0 ? -1 : torque_enable > 0 ? 1 : 0;
    }

    bool getTorqueEnabled() override
    {
        int torque_enable = _scs_bus.ReadToqueEnable(_config.id);
        // mclog::tagInfo(_tag, "id: {} torque enable: {}", _id, torque_enable);
        return torque_enable > 0;
    }

    void setCurrentAngleAsZero() override
    {
        int current_pos = _scs_bus.ReadPos(_config.id);
        if (!is_raw_pos_valid(current_pos)) {
            mclog::tagWarn(_tag, "id: {} ignore invalid zero calibration pos: {}, keep zero pos: {}", _config.id,
                           current_pos, _zero_pos);
            return;
        }

        _zero_pos = current_pos;
        reset_runtime_limits();

        Settings settings(_config.settingNs, true);
        settings.SetInt(_config.settingZeroPositionKey, _zero_pos);

        mclog::tagInfo(_tag, "id: {} set zero pos: {} to settings", _config.id, _zero_pos);
    }

    void resetZeroCalibration() override
    {
        _zero_pos = _config.defaultZeroPos;
        reset_runtime_limits();

        Settings settings(_config.settingNs, true);
        settings.SetInt(_config.settingZeroPositionKey, _zero_pos);

        mclog::tagInfo(_tag, "id: {} set zero pos: {} to settings", _config.id, _zero_pos);
    }

    void rotate(int velocity) override
    {
        velocity = uitk::clamp(velocity, -1000, 1000);

        if (!_config.enablePwmMode) {
            return;
        }

        int mapped_velocity = map_range(velocity, 0, 1000, 0, 1023);

        check_mode(Mode::PWM);
        _scs_bus.WritePWM(_config.id, mapped_velocity);
    }

    bool beginTimedRotation(int velocity) override
    {
        if (!_config.enablePwmMode || _rotation_power_fault || !GetHAL().servoPowerControlReady()) return false;
        const auto timeout = _scs_bus.IOTimeOut;
        _scs_bus.IOTimeOut = 10;
        _position_mode_verified = false;
        const bool ok = _timed_rotation.begin(_scs_bus, _config.id, velocity);
        _scs_bus.IOTimeOut = timeout;
        return ok;
    }
    bool endTimedRotation() override
    {
        if (!_config.enablePwmMode) return false;
        const auto timeout = _scs_bus.IOTimeOut;
        _scs_bus.IOTimeOut = 10;
        const bool ok = _timed_rotation.end(_scs_bus, _config.id);
        if (ok) _position_mode_verified = TimedRotationIO::positionMode(_scs_bus, _config.id);
        _scs_bus.IOTimeOut = timeout;
        if (ok && _position_mode_verified) _current_mode = Mode::Position;
        return ok && _position_mode_verified;
    }

    void cutRotationPower() override
    {
        _rotation_power_fault = true;
        _position_mode_verified = false;
        GetHAL().setServoPowerEnabled(false);
    }
    bool rotationPowerFaulted() const override { return _rotation_power_fault; }

private:
    bool _position_mode_verified = true;
    TimedRotationIO _timed_rotation;
    enum class Mode { Position = 0, PWM = 1 };

    ServoConfig_t _config;
    Vector2i _runtime_raw_pos_limit;
    ServoGoalProfile _goal_profile;
    int _zero_pos      = 0;
    Mode _current_mode = Mode::Position;

    static constexpr uint32_t kStallFeedbackIntervalMs = 50;
    static constexpr int kStallMinTargetDeltaRaw       = 8;
    static constexpr int kStallMaxPositionDeltaRaw     = 1;
    static constexpr int kStallCurrentRiseThreshold    = 80;
    static constexpr int kStallLoadRiseThreshold       = 150;
    static constexpr int kStallCurrentAbsThreshold     = 350;
    static constexpr int kStallLoadAbsThreshold        = 650;
    static constexpr int kStallConfirmSamples          = 2;

    uint32_t _last_stall_check_tick = 0;
    int _last_stall_raw_pos         = 0;
    int _last_stall_current_abs     = 0;
    int _last_stall_load_abs        = 0;
    int _last_stall_direction       = 0;
    int _stall_confirm_count        = 0;
    bool _last_stall_feedback_valid = false;

    static int abs_int(int value)
    {
        return value < 0 ? -value : value;
    }

    bool is_raw_pos_valid(int raw_pos) const
    {
        return raw_pos >= _config.rawPosLimit.x && raw_pos <= _config.rawPosLimit.y;
    }

    int raw_pos_to_angle(int raw_pos) const
    {
        return (raw_pos - _zero_pos) * 5 * 10 / 16;
    }

    void reset_runtime_limits()
    {
        _runtime_raw_pos_limit = _config.rawPosLimit;
        set_angle_limit(_config.angleLimit);
        reset_stall_detection();
    }

    void reset_stall_detection()
    {
        _last_stall_feedback_valid = false;
        _last_stall_direction      = 0;
        _stall_confirm_count       = 0;
    }

    bool update_stall_protection(int target_raw_pos)
    {
        if (!_config.enableStallProtection) {
            return false;
        }

        const uint32_t now = GetHAL().millis();
        if (now - _last_stall_check_tick < kStallFeedbackIntervalMs) {
            return false;
        }
        _last_stall_check_tick = now;

        if (_scs_bus.FeedBack(_config.id) < 0) {
            reset_stall_detection();
            return false;
        }

        const int current_raw_pos = _scs_bus.ReadPos(-1);
        const int current_abs     = abs_int(_scs_bus.ReadCurrent(-1));
        const int load_abs        = abs_int(_scs_bus.ReadLoad(-1));

        if (!is_raw_pos_valid(current_raw_pos)) {
            reset_stall_detection();
            return false;
        }

        const int target_delta = target_raw_pos - current_raw_pos;
        if (abs_int(target_delta) < kStallMinTargetDeltaRaw) {
            reset_stall_detection();
            return false;
        }

        const int direction = target_delta > 0 ? 1 : -1;
        if (_last_stall_feedback_valid && direction == _last_stall_direction) {
            const int pos_delta = abs_int(current_raw_pos - _last_stall_raw_pos);
            const bool position_stuck = pos_delta <= kStallMaxPositionDeltaRaw;
            const bool current_spike  = current_abs >= kStallCurrentAbsThreshold ||
                                       current_abs - _last_stall_current_abs >= kStallCurrentRiseThreshold;
            const bool load_spike = load_abs >= kStallLoadAbsThreshold ||
                                    load_abs - _last_stall_load_abs >= kStallLoadRiseThreshold;

            if (position_stuck && (current_spike || load_spike)) {
                _stall_confirm_count++;
            } else if (pos_delta > kStallMaxPositionDeltaRaw) {
                _stall_confirm_count = 0; 
            }
        } else {
            _stall_confirm_count = 0;
        }

        _last_stall_raw_pos         = current_raw_pos;
        _last_stall_current_abs     = current_abs;
        _last_stall_load_abs        = load_abs;
        _last_stall_direction       = direction;
        _last_stall_feedback_valid = true;

        if (_stall_confirm_count < kStallConfirmSamples) {
            return false;
        }

        handle_stall(current_raw_pos, direction, current_abs, load_abs);
        return true;
    }

    void handle_stall(int raw_pos, int direction, int current_abs, int load_abs)
    {
        int angle = raw_pos_to_angle(raw_pos);
        angle     = uitk::clamp(angle, _config.angleLimit.x, _config.angleLimit.y);

        auto angle_limit = getAngleLimit();
        if (direction > 0) {
            if (raw_pos < _runtime_raw_pos_limit.y) {
                _runtime_raw_pos_limit.y = raw_pos;
            }
            if (angle < angle_limit.y) {
                angle_limit.y = angle;
            }
        } else {
            if (raw_pos > _runtime_raw_pos_limit.x) {
                _runtime_raw_pos_limit.x = raw_pos;
            }
            if (angle > angle_limit.x) {
                angle_limit.x = angle;
            }
        }
        set_angle_limit(angle_limit);
        stop_motion_at_angle(angle);
        reset_stall_detection();

        check_mode(Mode::Position);
        _scs_bus.WritePos(_config.id, raw_pos, 20, 0);

        mclog::tagWarn(_tag,
                       "id: {} stall detected, raw: {}, angle: {}, dir: {}, current: {}, load: {}, limit: [{}, {}]",
                       _config.id, raw_pos, angle, direction, current_abs, load_abs, angle_limit.x, angle_limit.y);
    }

    void check_mode(Mode targetMode)
    {
        if (targetMode == _current_mode) {
            return;
        }

        _scs_bus.SwitchMode(_config.id, static_cast<uint8_t>(targetMode));
        _current_mode = targetMode;
    }
};

void Hal::servo_init(bool persistDefaults)
{
    mclog::tagInfo("HAL-Servo", "init");

    _scs_bus.enableDiagnostics(1, 2);
    _scs_bus.begin(UART_NUM_1, 1000000, 6, 7);

    ServoConfig_t yaw_servo_config;
    yaw_servo_config.id                     = 1;
    yaw_servo_config.defaultZeroPos         = 460;
    yaw_servo_config.angleLimit             = Vector2i(-1280, 1280);
    yaw_servo_config.rawPosLimit            = Vector2i(0, 1000);
    yaw_servo_config.settingNs              = "servo";
    yaw_servo_config.settingZeroPositionKey = "zero_pos_1";
    yaw_servo_config.enablePwmMode          = true;
    yaw_servo_config.persistDefaults        = persistDefaults;

    ServoConfig_t pitch_servo_config;
    pitch_servo_config.id                     = 2;
    pitch_servo_config.defaultZeroPos         = 620;
    pitch_servo_config.angleLimit             = persistDefaults ? Vector2i(30, 870) : Vector2i(50, 850);
    pitch_servo_config.rawPosLimit            = Vector2i(0, 1000);
    pitch_servo_config.settingNs              = "servo";
    pitch_servo_config.settingZeroPositionKey = "zero_pos_2";
    pitch_servo_config.enableStallProtection  = true;
    pitch_servo_config.persistDefaults        = persistDefaults;

    auto yaw_servo   = std::make_unique<ScsServo>(yaw_servo_config);
    auto pitch_servo = std::make_unique<ScsServo>(pitch_servo_config);
    auto motion      = std::make_unique<Motion>(std::move(yaw_servo), std::move(pitch_servo));
    motion->init();

    GetStackChan().attachMotion(std::move(motion));
}
