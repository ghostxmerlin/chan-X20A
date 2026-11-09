/* SPDX-License-Identifier: MIT */
#include "remote.h"
#include "remote_protocol.h"
#include "remote_modes.h"
#include "face_tracking.h"
#include "m12_unit.h"
#include "startup_pose.h"
#include "remote_stop.h"
#include "pitch_setting.h"
#include "pitch_motion.h"
#include "web_control.h"
#include "motion_trace.h"
#include "servo_tuning.h"
#include <hal/hal.h>
#include <stackchan/stackchan.h>
#include <espnow.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <esp_log.h>
#include <nvs.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <vector>

namespace collector {
namespace {
constexpr uint8_t kReceiver = 1;
constexpr size_t kHistory = 64;
struct Packet {
    RemoteCommand command;
    uint32_t seq = 0;
    int64_t timestamp = 0;
    int rssi = 0, channel = 0;
    std::array<uint8_t, 6> source{};
};
std::array<Packet, kHistory> history;
std::mutex mutex;
// Serialize stop and explicit setting commits without blocking receive/feedback
// on flash writes. No motor/UART operation takes this storage mutex.
std::mutex pitch_storage_mutex;
PitchSettingRequests pitch_requests;
int pitch_configured = kPitchSettingDefault, boot_pitch_target = kPitchSettingDefault;
bool pitch_persisted = false, pitch_has_command_start = false;
int pitch_command_start = 0;
esp_err_t pitch_storage_error = ESP_OK;
const char* pitch_state = "waiting_feedback";
uint32_t sequence = 0, rejected = 0, wrong_target = 0;
std::array<uint8_t, 8> rejected_payload{};
size_t rejected_size = 0;
int64_t rejected_timestamp = 0;
int64_t feedback_timestamp = 0, yaw_feedback_timestamp = 0;
// Collector is also the remote receiver app: it does not need an Android lease.
bool control_requested = true;
RemoteStopLatch stop_latch;
ManualYaw manual_yaw;
MotionTrace yaw_trace, pitch_trace;
// Operator tuning: one pending request, applied only by motor_task.
ServoTuneRequest pending_tune;
bool tune_pending = false;
uint32_t tune_sequence = 0, tune_applied_sequence[2] = {0, 0};
const char* tune_state[2] = {"idle", "idle"};
stackchan::motion::ServoEepromWriteResult last_tune_result[2];
FaceTracking tracking;
RemoteModeInput mode_input;
std::atomic_bool web_yaw_active{false};
std::atomic_bool ready{false}, motor_ready{false}, enabled{false};
stackchan::motion::ServoFeedback yaw_feedback, pitch_feedback;
stackchan::motion::ServoDiagnostics yaw_diagnostics, pitch_diagnostics;
stackchan::motion::ServoDeadbandSnapshot yaw_deadband, pitch_deadband;
stackchan::motion::ServoTorqueCommandProbe yaw_tracking_torque;
int64_t diagnostics_timestamp = 0;
bool yaw_trajectory_complete = false, pitch_trajectory_complete = false;
bool yaw_command_started = false;
const char* motor_state = "starting";
const char* startup_state = "waiting_feedback";

RemoteCenterInput center_input;
RemoteReaction reaction = RemoteReaction::None;
uint32_t reaction_seq = 0;
int64_t reaction_timestamp = 0;

void load_pitch_setting()
{
    nvs_handle_t handle;
    int16_t stored = kPitchSettingDefault;
    esp_err_t error = nvs_open("collector", NVS_READONLY, &handle);
    if (error == ESP_OK) {
        error = nvs_get_i16(handle, "pitch_deg10", &stored);
        nvs_close(handle);
    }
    if (error == ESP_OK && valid_pitch_setting(stored)) {
        pitch_configured = stored;
        pitch_persisted = true;
    } else if (error != ESP_ERR_NVS_NOT_FOUND) {
        pitch_storage_error = error == ESP_OK ? ESP_ERR_INVALID_ARG : error;
        ESP_LOGW("CollectorRemote", "Pitch setting load failed: %s; default %.1f deg, no storage change",
                 esp_err_to_name(pitch_storage_error), kPitchSettingDefault / 10.0f);
    }
    boot_pitch_target = pitch_configured;
    pitch_requests.configure_boot(boot_pitch_target);
}

esp_err_t received(uint8_t* source, void* bytes, size_t size, wifi_pkt_rx_ctrl_t* rx)
{
    if (!source || !bytes || !rx) return ESP_ERR_INVALID_ARG;
    RemoteCommand command;
    std::lock_guard<std::mutex> lock(mutex);
    if (!decode_remote(static_cast<uint8_t*>(bytes), size, command)) {
        ++rejected;
        rejected_size = size;
        rejected_timestamp = esp_timer_get_time();
        rejected_payload.fill(0);
        memcpy(rejected_payload.data(), bytes, std::min(size, rejected_payload.size()));
        return ESP_OK;
    }
    if (!remote_for_receiver(command, kReceiver)) { ++wrong_target; return ESP_OK; }
    Packet packet;
    packet.command = command;
    packet.seq = ++sequence;
    packet.timestamp = esp_timer_get_time();
    packet.rssi = rx->rssi;
    packet.channel = rx->channel;
    memcpy(packet.source.data(), source, 6);
    history[sequence % kHistory] = packet;
    // Screen input is consumed here, independently of motor_task and feedback.
    center_input.accept(command, packet.source, packet.timestamp);
    // Events only: no axis writes, mode changes or network work on ESP-NOW RX.
    mode_input.accept(command, packet.source, packet.timestamp);
    return ESP_OK;
}

void apply_servo_tune(stackchan::motion::Motion& motion, const ServoTuneRequest& tune)
{
    using Op = ServoTuneRequest::Op;
    auto& servo = tune.axis == 0 ? motion.yawServo() : motion.pitchServo();
    const char* state = "applied";
    stackchan::motion::ServoEepromWriteResult result;
    stackchan::motion::ServoDeadbandSnapshot snapshot;
    bool have_snapshot = false;
    switch (tune.op) {
        case Op::Eeprom:
        case Op::Restore:
            result = servo.writeEepromTuning(tune.op == Op::Restore ? factory_servo_eeprom() : tune.eeprom);
            state = (result.unlocked && result.locked && result.failed_writes == 0) ? "written" : "write_failed";
            snapshot = result.after; have_snapshot = result.after.attempted;
            break;
        case Op::Runtime:
            if (tune.has_goal) servo.setGoalProfile({tune.goal_time_ms, tune.goal_speed});
            if (tune.has_spring) {
                if (tune.spring_stiffness > 0) servo.setSpeedSpringOverride(tune.spring_stiffness, tune.spring_damping);
                else servo.clearSpeedSpringOverride();
            }
            if (tune.has_release) servo.setReleasePolicy(tune.hold_min_ms, tune.hold_max_ms, tune.tolerance_tenths);
            break;
        case Op::Refresh:
            snapshot = servo.readDeadbandSnapshot(); have_snapshot = true; state = "refreshed";
            break;
        default: state = "invalid";
    }
    ESP_LOGI("CollectorRemote", "Servo tuning %lu axis %d op %d -> %s", static_cast<unsigned long>(tune.sequence), tune.axis, static_cast<int>(tune.op), state);
    std::lock_guard<std::mutex> lock(mutex);
    if (have_snapshot) (tune.axis == 0 ? yaw_deadband : pitch_deadband) = snapshot;
    if (tune.op == Op::Eeprom || tune.op == Op::Restore) last_tune_result[tune.axis] = result;
    tune_state[tune.axis] = state; tune_applied_sequence[tune.axis] = tune.sequence;
}

void motor_task(void*)
{
    // Boot pitch remains one-shot. Physical directions only emit mode events;
    // neither enable/disable tracking nor remote heartbeats restart this pose.
    bool was_enabled = false;
    int64_t last_feedback = 0;
    bool yaw_deadband_attempted = false, pitch_deadband_attempted = false;
    StartupPose startup;
    PitchSettingRequest pitch_request;
    {
        std::lock_guard<std::mutex> lock(mutex);
        pitch_request = pitch_requests.current();
        if (!pitch_request.from_boot) startup_state = "superseded";
    }
    TickType_t wake = xTaskGetTickCount();
    while (true) {
        const int64_t now = esp_timer_get_time();
        bool active;
        bool stop_pending = false;
        bool pitch_cancelled = false;
        PitchSettingRequest latest_pitch;
        {
            std::lock_guard<std::mutex> lock(mutex);
            active = control_requested;
        }
        if (!motor_ready) {
            if (active) {
                { std::lock_guard<std::mutex> lock(mutex); motor_state = "initializing"; }
                GetHAL().initCollectorMotion();
                configure_pitch_motion(GetStackChan().motion().pitchServo());
                GetStackChan().motion().yawServo().setReleasePolicy(kWebYawReleaseMinHoldMs, kWebYawReleaseMaxHoldMs, kWebYawReleaseToleranceTenths);
                motor_ready = true;
                // Re-check the requested state and feedback before any motion.
            } else {
                std::lock_guard<std::mutex> lock(mutex); motor_state = "not_initialized";
            }
            vTaskDelay(pdMS_TO_TICKS(20));wake = xTaskGetTickCount();continue;
        }
        auto& motion = GetStackChan().motion();
        stackchan::motion::ServoFeedback startup_feedback;
        stackchan::motion::ServoFeedback sampled_yaw;
        if (now - last_feedback >= 100000) {
            const auto yaw = motion.yawServo().readFeedback();
            const int64_t yaw_sample_us = esp_timer_get_time();
            const auto pitch = motion.pitchServo().readFeedback();
            std::lock_guard<std::mutex> lock(mutex);
            yaw_feedback = yaw; pitch_feedback = pitch;
            yaw_feedback_timestamp = yaw_sample_us;
            feedback_timestamp = esp_timer_get_time();
            yaw_trace.sample(yaw, yaw_sample_us);
            pitch_trace.sample(pitch, feedback_timestamp);
            last_feedback = now;
            startup_feedback = pitch;
            sampled_yaw = yaw;
            // Feedback is observation data. Factory receiver control does not
            // disable itself because an optional position read failed.
        }
        {
            // A stop may arrive while the UART reads are in progress.
            std::lock_guard<std::mutex> lock(mutex);
            active = control_requested;
            stop_pending = stop_latch.consume();
            latest_pitch = pitch_requests.current();
            pitch_cancelled = pitch_requests.cancelled(latest_pitch);
        }
        if (stop_pending) {
            { std::lock_guard<std::mutex> lock(mutex); yaw_tracking_torque.beforeCancel(motion.yawServo()); yaw_trace.close(now); pitch_trace.close(now); }
            apply_remote_stop(startup, motion, now);
            was_enabled = false;
            yaw_command_started = false;
        }
        {
            // Operator tuning runs only on an idle axis; a request that cannot run
            // within 10 s expires instead of writing during motion.
            ServoTuneRequest tune; bool run = false;
            {
                std::lock_guard<std::mutex> lock(mutex);
                if (tune_pending) {
                    const bool idle = pending_tune.axis == 0
                        ? (!manual_yaw.pending && !tracking.active() &&
                           manual_yaw_idle(yaw_command_started, motion.yawServo().isTrajectoryComplete()))
                        : (strcmp(startup.state(), "moving") != 0 && latest_pitch.sequence == pitch_request.sequence);
                    if (idle) { tune = pending_tune; tune_pending = false; run = true; tune_state[tune.axis] = "applying"; }
                    else if (now - pending_tune.requested_us > 10000000) { tune_pending = false; tune_state[pending_tune.axis] = "expired_axis_busy"; }
                }
            }
            if (run) apply_servo_tune(motion, tune);
        }
        if (latest_pitch.sequence != pitch_request.sequence) {
            // Only a successfully committed, explicit request replaces pitch.
            // An intervening stop cancels earlier queued requests as well.
            motion.pitchServo().cancelMotion();
            { std::lock_guard<std::mutex> lock(mutex); pitch_trace.close(now); }
            if (pitch_request.from_boot) {
                std::lock_guard<std::mutex> lock(mutex); startup_state = "superseded";
            }
            pitch_request = latest_pitch;
            startup = StartupPose{};
            { std::lock_guard<std::mutex> lock(mutex); pitch_has_command_start = false; }
        }
        const auto pitch_limits = motion.pitchServo().getAngleLimit();
        if (active && !pitch_cancelled && strcmp(startup.state(), "target_unavailable") != 0 &&
            (pitch_request.target < pitch_limits.x || pitch_request.target > pitch_limits.y)) {
            motion.pitchServo().cancelMotion();
            startup.target_unavailable();
        }
        const bool valid_start = motion.pitchServo().canStartFromFeedback(
            startup_feedback, pitch_request.target, kPitchSettingLowerMargin);
        auto pose_action = startup.step(now, active && !pitch_cancelled, valid_start,
                                       motion.pitchServo().isTrajectoryComplete());
        if (pose_action == StartupPose::Action::Start) {
            if (motion.pitchServo().moveFromFeedbackWithSpeed(startup_feedback, pitch_request.target,
                                                             StartupPose::kSpeed, kPitchSettingLowerMargin)) {
                const int command_start = std::clamp(startup_feedback.angle, pitch_limits.x, pitch_limits.y);
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    if (pitch_requests.current().sequence == pitch_request.sequence) {
                        pitch_command_start = command_start;
                        pitch_has_command_start = true;
                    }
                    pitch_trace.begin(pitch_request.from_boot ? "boot_pitch" : "pitch_setting", pitch_request.sequence,
                                      pitch_request.target, startup_feedback, now);
                }
                ESP_LOGI("CollectorRemote", "Pitch %s %lu: measured %.1f, command-start %.1f -> target %.1f deg, speed %d",
                         pitch_request.from_boot ? "boot" : "setting", static_cast<unsigned long>(pitch_request.sequence),
                         startup_feedback.angle / 10.0f, command_start / 10.0f, pitch_request.target / 10.0f, StartupPose::kSpeed);
            } else {
                startup.start_rejected();
                pose_action = StartupPose::Action::None;
            }
        } else if (pose_action == StartupPose::Action::Cancel) {
            motion.pitchServo().cancelMotion();
            std::lock_guard<std::mutex> lock(mutex); pitch_trace.close(now);
        }
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (pitch_request.from_boot) startup_state = startup.state();
            if (pitch_requests.current().sequence == pitch_request.sequence) pitch_state = startup.state();
        }
        if (!active) {
            if (was_enabled) {
                { std::lock_guard<std::mutex> lock(mutex); yaw_tracking_torque.beforeCancel(motion.yawServo()); yaw_trace.close(now); pitch_trace.close(now); }
                motion.yawServo().cancelMotion();
                motion.pitchServo().cancelMotion();
            }
            enabled = false;
            { std::lock_guard<std::mutex> lock(mutex); motor_state = "disarmed"; }
        } else {
            enabled = true;
            // The M12 snapshot is passive; mode requests belong to interaction.
            const auto camera = m12_snapshot();
            {
                std::lock_guard<std::mutex> lock(mutex);
                TrackingInput input;
                input.now_us = esp_timer_get_time();
                input.control_enabled = control_requested;
                input.feedback_valid = yaw_feedback.valid;
                input.feedback_us = yaw_feedback_timestamp;
                input.measured = yaw_feedback.angle;
                const auto limits = motion.yawServo().getAngleLimit();
                input.lower = limits.x; input.upper = limits.y;
                input.settled = manual_yaw_idle(yaw_command_started, motion.yawServo().isTrajectoryComplete());
                input.mode = static_cast<int>(camera.function);
                input.mode_revision = m12_mode_request();
                input.applied_mode_revision = camera.mode_request_revision;
                input.requested_mode = static_cast<int>(m12_requested_function(input.mode_revision));
                input.mode_requested_us = camera.requested_us;
                input.frame_seq = camera.frame_seq; input.frame_us = camera.frame_us;
                input.frame_mode = camera.frame_recognition ? 2 : 1;
                if (camera.valid) {
                    int largest = -1;
                    for (size_t i = 0; i < camera.count; ++i) {
                        const auto& face = camera.faces[i];
                        const int area = face.w * face.h;
                        if (area <= largest) continue;
                        largest = area;
                        input.x = face.x; input.y = face.y; input.w = face.w; input.h = face.h;
                    }
                    input.face_count = camera.count;
                }
                const auto action = tracking.step(input);
                if (action == FaceTracking::Action::Cancel) {
                    // cancelMotion removes torque; it is not a physical hold.
                    yaw_tracking_torque.beforeCancel(motion.yawServo());
                    motion.yawServo().cancelMotion();
                    yaw_trace.close(input.now_us);
                    yaw_command_started = false;
                } else if (action == FaceTracking::Action::Move) {
                    if (apply_tracking_move(motion.yawServo(), yaw_feedback, tracking.target())) {
                        yaw_tracking_torque.begin(motion.yawServo(), tracking.commands(), tracking.target(), yaw_feedback);
                        yaw_trace.begin("tracking", tracking.commands(), tracking.target(), yaw_feedback, input.now_us);
                        yaw_command_started = true;
                        web_yaw_active = false;
                    } else {
                        yaw_tracking_torque.beforeCancel(motion.yawServo());
                        motion.yawServo().cancelMotion();
                        yaw_command_started = false;
                        tracking.command_rejected(input);
                    }
                }
            }
            {
                std::lock_guard<std::mutex> lock(mutex);
                // Recheck stop after feedback reads; do not move from HTTP or a second UART owner.
                if (tracking.take_cancel()) {
                    yaw_tracking_torque.beforeCancel(motion.yawServo());
                    motion.yawServo().cancelMotion();
                    yaw_trace.close(esp_timer_get_time());
                    yaw_command_started = false;
                }
                if (control_requested && manual_yaw.take(esp_timer_get_time(), manual_yaw_idle(yaw_command_started, motion.yawServo().isTrajectoryComplete()))) {
                    const bool fresh = yaw_feedback.valid && yaw_feedback_timestamp > 0 &&
                        esp_timer_get_time() - yaw_feedback_timestamp < 1000000;
                    const auto limits = motion.yawServo().getAngleLimit();
                    bool accepted = fresh && manual_yaw.target >= limits.x && manual_yaw.target <= limits.y;
                    if (accepted && manual_yaw.remote_like) {
                        // Diagnostic replay of the former physical remote entry (7700e7d
                        // apply_remote_yaw): physical sync, then the stock spring.
                        motion.yawServo().setAutoAngleSyncEnabled(true);
                        motion.yawServo().moveWithSpeed(manual_yaw.target, manual_yaw.speed);
                    } else if (accepted) {
                        accepted = motion.yawServo().moveFromFeedbackWithSpeed(yaw_feedback, manual_yaw.target, manual_yaw.speed);
                    }
                    manual_yaw.finish(accepted); web_yaw_active = accepted;
                    if (accepted) {
                        yaw_command_started = true;
                        yaw_trace.begin(manual_yaw.remote_like ? "web_yaw_remote_like" : "web_yaw", manual_yaw.sequence, manual_yaw.target, yaw_feedback, esp_timer_get_time());
                    }
                }
            }
            {
                // Serialize explicit stop with the final update, not only with
                // admission. No stale tracking action can write after stop wins.
                std::lock_guard<std::mutex> lock(mutex);
                if (control_requested && (tracking.active() || web_yaw_active)) {
                    motion.yawServo().update();
                    yaw_tracking_torque.afterUpdate(motion.yawServo());
                    motor_state = tracking.active() ? "tracking" : "web_yaw";
                } else {
                    motor_state = strcmp(startup.state(), "moving") == 0 ?
                        (pitch_request.from_boot ? "startup_pose" : "pitch_setting") : "waiting_for_input";
                }
            }
            // Retain spring updates and factory torque release for either owner.
            if (pose_action == StartupPose::Action::Start || pose_action == StartupPose::Action::Update)
                motion.pitchServo().update();
        }
        // One read-only configuration probe per axis, after boot has settled and
        // a fresh valid position arrived. No retries, EEPROM writes or motion.
        // The motor task remains the sole UART owner; HTTP only reads the copy.
        // Passive init leaves the yaw animation "not done" until its first
        // update, so gate on the web/tracking idle predicate instead.
        if (now >= 5000000 && !yaw_deadband_attempted && sampled_yaw.valid &&
            manual_yaw_idle(yaw_command_started, motion.yawServo().isTrajectoryComplete())) {
            const auto snapshot = motion.yawServo().readDeadbandSnapshot();
            yaw_deadband_attempted = true;
            std::lock_guard<std::mutex> lock(mutex);
            yaw_deadband = snapshot;
        }
        if (now >= 5000000 && !pitch_deadband_attempted && startup_feedback.valid &&
            motion.pitchServo().isTrajectoryComplete()) {
            const auto snapshot = motion.pitchServo().readDeadbandSnapshot();
            pitch_deadband_attempted = true;
            std::lock_guard<std::mutex> lock(mutex);
            pitch_deadband = snapshot;
        }
        {
            // These are copies of existing transactions, never new UART reads.
            // Only motor_task owns the bus; HTTP reads the protected snapshots.
            std::lock_guard<std::mutex> lock(mutex);
            yaw_diagnostics = motion.yawServo().diagnostics();
            pitch_diagnostics = motion.pitchServo().diagnostics();
            yaw_trace.note(yaw_diagnostics);
            pitch_trace.note(pitch_diagnostics);
            yaw_trajectory_complete = manual_yaw_idle(yaw_command_started, motion.yawServo().isTrajectoryComplete());
            if (yaw_trajectory_complete) manual_yaw.complete();
            pitch_trajectory_complete = motion.pitchServo().isTrajectoryComplete();
            diagnostics_timestamp = esp_timer_get_time();
        }
        was_enabled = active;
        xTaskDelayUntil(&wake, pdMS_TO_TICKS(20));
    }
}
void add_packet(ArduinoJson::JsonObject object, const Packet& packet)
{
    object["seq"] = packet.seq;
    object["timestamp_us"] = packet.timestamp;
    object["target_id"] = packet.command.target_id;
    object["yaw_deg"] = packet.command.yaw / 10.0f;
    object["pitch_deg"] = packet.command.pitch / 10.0f;
    object["speed"] = packet.command.speed;
    object["auxiliary"] = packet.command.auxiliary;
    object["center_protocol"] = packet.command.center_v1 ? "joystick_center_v1" : "none";
    if (packet.command.center_v1) {
        object["sender_boot_nonce"] = packet.command.sender_boot_nonce;
        object["center_press_seq"] = packet.command.center_press_seq;
    }
    object["rssi_dbm"] = packet.rssi;
    object["channel"] = packet.channel;
    char source[18];
    snprintf(source, sizeof(source), "%02x:%02x:%02x:%02x:%02x:%02x", packet.source[0], packet.source[1], packet.source[2], packet.source[3], packet.source[4], packet.source[5]);
    object["source_mac"] = source;
}
void add_feedback(ArduinoJson::JsonObject object, const stackchan::motion::ServoFeedback& feedback)
{
    object["valid"] = feedback.valid;
    object["transport_error"] = feedback.transport_error;
    object["servo_status"] = feedback.servo_status;
    object["position_raw"] = feedback.position_raw;
    if (!feedback.valid) return;
    object["angle_deg"] = feedback.angle / 10.0f;
    object["load_valid"] = feedback.load_valid;
    object["current_valid"] = feedback.current_valid;
    if (feedback.load_valid) object["load_raw"] = feedback.load_raw;
    if (feedback.current_valid) object["current_raw"] = feedback.current_raw;
    if (feedback.voltage_raw >= 0) {
        object["speed_raw"] = feedback.speed_raw;
        object["voltage_raw"] = feedback.voltage_raw;   // 0.1 V units
        object["temperature_c"] = feedback.temperature_raw;
        object["moving"] = feedback.moving_raw;
    }
}
template <typename Counter>
void add_transaction_counter(ArduinoJson::JsonObject target, const Counter& value)
{
    target["attempts"] = value.attempts;
    target["ok"] = value.ok;
    target["no_reply"] = value.no_reply;
    target["bad_checksum"] = value.bad_checksum;
    target["wrong_id"] = value.wrong_id;
    target["bad_length"] = value.bad_length;
    target["servo_error"] = value.servo_error;
    target["tx_error"] = value.tx_error;
}
template <typename Trace>
void add_transaction_trace(ArduinoJson::JsonObject target, const Trace& value)
{
    target["valid"] = value.valid;
    if (!value.valid) return;
    target["timestamp_us"] = value.timestamp_us;
    target["duration_us"] = value.duration_us;
    target["id"] = value.id;
    target["instruction"] = value.instruction;
    target["address"] = value.address;
    target["requested_bytes"] = value.requested_bytes;
    auto payload = target["tx_data_prefix"].template to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < std::min<size_t>(value.tx_data_prefix_size, 6); ++i) payload.add(value.tx_data_prefix[i]);
    target["transport_error"] = value.transport_error;
    target["status_valid"] = value.status_valid;
    if (value.status_valid) target["servo_status"] = value.servo_status;
    else target["servo_status"] = nullptr;
    target["rx_bytes"] = value.rx_bytes;
    auto prefix = target["rx_prefix"].template to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < std::min<size_t>(value.rx_prefix_size, 24); ++i) prefix.add(value.rx_prefix[i]);
    target["tx_bytes"] = value.tx_bytes;
    target["tx_calls"] = value.tx_calls;
    target["tx_submit_span_us"] = value.tx_submit_span_us;
    target["tx_max_gap_us"] = value.tx_max_gap_us;
    target["tx_short_writes"] = value.tx_short_writes;
    target["tx_wait_error"] = value.tx_wait_error;
    target["discarded_before"] = value.discarded_before;
    target["raw_valid"] = value.raw_valid;
    if (value.raw_valid) target["raw_value"] = value.raw_value;
    else target["raw_value"] = nullptr;
}
void add_servo_diagnostics(ArduinoJson::JsonObject target, const stackchan::motion::ServoDiagnostics& value)
{
    target["generation"] = value.generation;
    target["snapshot_timestamp_us"] = diagnostics_timestamp;
    target["source"] = "cached_existing_uart_transactions";
    target["torque_disable_semantics"] = "acknowledgement_not_measured_torque";
    add_transaction_counter(target["all"].to<ArduinoJson::JsonObject>(), value.all);
    add_transaction_counter(target["position_read"].to<ArduinoJson::JsonObject>(), value.position_read);
    add_transaction_counter(target["moving_read"].to<ArduinoJson::JsonObject>(), value.moving_read);
    add_transaction_counter(target["torque_read"].to<ArduinoJson::JsonObject>(), value.torque_read);
    add_transaction_counter(target["torque_disable"].to<ArduinoJson::JsonObject>(), value.torque_disable);
    add_transaction_trace(target["last"].to<ArduinoJson::JsonObject>(), value.last);
    add_transaction_trace(target["last_error"].to<ArduinoJson::JsonObject>(), value.last_error);
    add_transaction_trace(target["last_position_write"].to<ArduinoJson::JsonObject>(), value.last_position_write);
    add_transaction_trace(target["last_torque_disable"].to<ArduinoJson::JsonObject>(), value.last_torque_disable);
    const auto& event = value.uart_events;
    auto uart = target["uart_events"].to<ArduinoJson::JsonObject>();
    uart["scope"] = "shared_bus_do_not_sum_axes";
    uart["coverage"] = "best_effort_queue_may_drop";
    uart["enabled"] = event.enabled;
    uart["frame_error_interrupt_enabled"] = event.frame_error_interrupt_enabled;
    uart["frame_error_interrupt_error"] = event.frame_error_interrupt_error;
    uart["counts_complete"] = event.counts_complete;
    uart["queue_capacity"] = event.queue_capacity;
    uart["queue_full_observations"] = event.queue_full_observations;
    uart["events_drained"] = event.events_drained;
    uart["frame_errors"] = event.frame_errors;
    uart["parity_errors"] = event.parity_errors;
    uart["fifo_overflows"] = event.fifo_overflows;
    uart["buffer_full"] = event.buffer_full;
    uart["breaks"] = event.breaks;
    uart["last_poll_timestamp_us"] = event.last_poll_timestamp_us;
}
void add_deadband_snapshot(ArduinoJson::JsonObject target, const stackchan::motion::ServoDeadbandSnapshot& value)
{
    target["source"] = "one_time_read_only_register_probe";
    target["register_start"] = 9;
    target["register_bytes"] = 19;
    target["received_bytes"] = value.received_bytes;
    target["min_angle_register"] = 9;
    target["max_angle_register"] = 11;
    target["phase_register"] = 18;
    target["word_order"] = "high_byte_first";
    target["attempted"] = value.attempted;
    target["valid"] = value.valid;
    target["timestamp_us"] = value.timestamp_us;
    target["transport_error"] = value.transport_error;
    const bool status_valid = value.attempted && value.transport_error == 0;
    target["status_valid"] = status_valid;
    if (status_valid) target["servo_status"] = value.servo_status;
    else target["servo_status"] = nullptr;
    target["cw_register"] = 26;
    target["ccw_register"] = 27;
    target["unit"] = "step";
    target["degrees_per_step"] = 0.3125f;
    if (value.valid) {
        target["cw_raw"] = value.cw_raw;
        target["ccw_raw"] = value.ccw_raw;
        target["min_angle_raw"] = value.min_angle_raw;
        target["max_angle_raw"] = value.max_angle_raw;
        target["phase_raw"] = value.phase_raw;
        target["max_temperature_raw"] = value.max_temperature_raw;
        target["max_voltage_raw"] = value.max_voltage_raw;
        target["min_voltage_raw"] = value.min_voltage_raw;
        target["max_torque_raw"] = value.max_torque_raw;
        target["unloading_raw"] = value.unloading_raw;
        target["led_alarm_raw"] = value.led_alarm_raw;
        target["p_raw"] = value.p_raw;
        target["d_raw"] = value.d_raw;
        target["i_raw"] = value.i_raw;
        target["punch_raw"] = value.punch_raw;
    } else {
        target["cw_raw"] = nullptr;
        target["ccw_raw"] = nullptr;
        target["min_angle_raw"] = nullptr;
        target["max_angle_raw"] = nullptr;
        target["phase_raw"] = nullptr;
    }
}
void add_torque_snapshot(ArduinoJson::JsonObject target, const stackchan::motion::ServoTorqueSnapshot& value)
{
    target["supported"] = value.supported;
    target["attempted"] = value.attempted;
    target["valid"] = value.valid;
    target["timestamp_us"] = value.timestamp_us;
    target["transport_error"] = value.transport_error;
    target["received_bytes"] = value.received_bytes;
    const bool status_valid = value.attempted && value.transport_error == 0;
    target["status_valid"] = status_valid;
    if (status_valid) target["servo_status"] = value.servo_status;
    else target["servo_status"] = nullptr;
    if (value.valid) {
        target["raw"] = value.raw;
        target["goal_position_raw"] = value.goal_position_raw;
        target["goal_time_raw"] = value.goal_time_raw;
        target["goal_speed_raw"] = value.goal_speed_raw;
    } else {
        target["raw"] = nullptr;
        target["goal_position_raw"] = nullptr;
        target["goal_time_raw"] = nullptr;
        target["goal_speed_raw"] = nullptr;
    }
    if (value.valid && (value.raw == 0 || value.raw == 1)) target["enabled"] = value.raw == 1;
    else target["enabled"] = nullptr;
}
void add_motion_summary(ArduinoJson::JsonObject target, const MotionTrace& value)
{
    target["source"] = value.source; target["request_seq"] = value.sequence; target["active"] = value.active;
    target["samples"] = static_cast<uint32_t>(value.count);
    if (value.count == 0) { target["target_deg"] = nullptr; target["start_deg"] = nullptr; target["last_deg"] = nullptr; target["error_deg"] = nullptr; target["peak_load_permille"] = nullptr; target["released_after_ms"] = nullptr; return; }
    target["target_deg"] = value.target / 10.0f;
    if (value.start.valid) target["start_deg"] = value.start.angle / 10.0f; else target["start_deg"] = nullptr;
    int peak = 0; const MotionTrace::Sample* last = nullptr;
    for (size_t i = 0; i < value.count; ++i) {
        const auto& s = value.samples[i];
        if (!s.valid) continue;
        last = &s;
        const int load = s.load_raw < 0 ? -s.load_raw : s.load_raw;
        if (load > peak) peak = load;
    }
    if (last) {
        target["last_deg"] = last->angle / 10.0f; target["last_raw"] = last->position_raw;
        target["error_deg"] = (last->angle - value.target) / 10.0f;
        target["last_sample_us"] = last->timestamp_us;
    } else { target["last_deg"] = nullptr; target["last_raw"] = nullptr; target["error_deg"] = nullptr; }
    target["peak_load_permille"] = peak;
    if (value.last_goal_write.valid) target["final_goal_write_us"] = value.last_goal_write.timestamp_us; else target["final_goal_write_us"] = nullptr;
    if (value.last_goal_write.valid && value.last_torque_disable.valid)
        target["released_after_ms"] = static_cast<int64_t>(value.last_torque_disable.timestamp_us - value.last_goal_write.timestamp_us) / 1000;
    else target["released_after_ms"] = nullptr;
}
void add_servo_tuning(ArduinoJson::JsonObject target, int axis, stackchan::motion::Servo& servo,
                      const stackchan::motion::ServoDeadbandSnapshot& snapshot)
{
    target["state"] = tune_state[axis];
    target["applied_seq"] = tune_applied_sequence[axis];
    target["pending"] = tune_pending && pending_tune.axis == axis;
    auto eeprom = target["eeprom"].to<ArduinoJson::JsonObject>();
    eeprom["valid"] = snapshot.valid; eeprom["timestamp_us"] = snapshot.timestamp_us;
    if (snapshot.valid) {
        eeprom["p"] = snapshot.p_raw; eeprom["d"] = snapshot.d_raw; eeprom["i"] = snapshot.i_raw;
        eeprom["punch"] = snapshot.punch_raw; eeprom["dead_cw"] = snapshot.cw_raw; eeprom["dead_ccw"] = snapshot.ccw_raw;
        eeprom["max_torque"] = snapshot.max_torque_raw; eeprom["min_angle"] = snapshot.min_angle_raw; eeprom["max_angle"] = snapshot.max_angle_raw;
        eeprom["phase"] = snapshot.phase_raw; eeprom["unloading"] = snapshot.unloading_raw; eeprom["led_alarm"] = snapshot.led_alarm_raw;
        eeprom["max_temperature"] = snapshot.max_temperature_raw; eeprom["min_voltage"] = snapshot.min_voltage_raw; eeprom["max_voltage"] = snapshot.max_voltage_raw;
    }
    auto runtime = target["runtime"].to<ArduinoJson::JsonObject>();
    const auto profile = servo.goalProfile();
    runtime["goal_time_ms"] = profile.time_ms; runtime["goal_speed"] = profile.speed;
    runtime["spring_stiffness"] = servo.speedSpringStiffness(); runtime["spring_damping"] = servo.speedSpringDamping();
    runtime["hold_min_ms"] = servo.releaseMinHoldMs(); runtime["hold_max_ms"] = servo.releaseMaxHoldMs();
    runtime["tolerance_deg"] = servo.releaseToleranceTenths() / 10.0f;
    const auto& write = last_tune_result[axis];
    auto last = target["last_write"].to<ArduinoJson::JsonObject>();
    last["attempted"] = write.attempted; last["unlocked"] = write.unlocked; last["locked"] = write.locked;
    last["writes"] = write.writes; last["failed_writes"] = write.failed_writes; last["failed_register"] = write.failed_register;
    last["timestamp_us"] = write.timestamp_us;
}
void add_motion_trace(ArduinoJson::JsonObject target, const MotionTrace& value)
{
    target["source"] = value.source;
    target["request_seq"] = value.sequence;
    target["target_deg"] = value.target / 10.0f;
    target["started_us"] = value.started_us;
    target["closed_us"] = value.closed_us;
    target["active"] = value.active;
    target["truncated"] = value.truncated;
    target["sample_interval_ms"] = 100;
    target["capacity"] = static_cast<uint32_t>(MotionTrace::kCapacity);
    target["semantics"] = "existing_100ms_feedback_reads; rows are [timestamp_us, position_raw, angle_deg, load_raw, current_raw, voltage_raw, temperature_c, moving], null when invalid";
    add_feedback(target["start"].to<ArduinoJson::JsonObject>(), value.start);
    auto samples = target["samples"].to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < value.count; ++i) {
        const auto& entry = value.samples[i];
        auto row = samples.add<ArduinoJson::JsonArray>();
        row.add(entry.timestamp_us);
        if (entry.valid) {
            row.add(entry.position_raw); row.add(entry.angle / 10.0f);
            row.add(entry.load_raw); row.add(entry.current_raw); row.add(entry.voltage_raw);
            row.add(entry.temperature_raw); row.add(entry.moving_raw);
        } else { for (int i = 0; i < 7; ++i) row.add(nullptr); }
    }
    add_transaction_trace(target["final_goal_write"].to<ArduinoJson::JsonObject>(), value.last_goal_write);
    add_transaction_trace(target["torque_disable"].to<ArduinoJson::JsonObject>(), value.last_torque_disable);
}
void add_tracking_torque(ArduinoJson::JsonObject target, const stackchan::motion::ServoTorqueCommandProbe& value)
{
    target["source"] = "bounded_read_only_register_probe";
    target["semantics"] = "register_state_not_measured_physical_torque";
    target["register_start"] = 40;target["register_bytes"] = 8;
    target["torque_register"] = 40;target["goal_position_register"] = 42;
    target["word_order"] = "high_byte_first";
    target["max_reads_per_command"] = 3;
    target["command_count"] = value.command_count;
    if (value.command_count) target["target_deg"] = value.target / 10.0f;
    else target["target_deg"] = nullptr;
    add_feedback(target["start_feedback"].to<ArduinoJson::JsonObject>(), value.start_feedback);
    add_torque_snapshot(target["before"].to<ArduinoJson::JsonObject>(), value.before);
    add_torque_snapshot(target["after_first_write"].to<ArduinoJson::JsonObject>(), value.after_first_write);
    add_torque_snapshot(target["before_cancel"].to<ArduinoJson::JsonObject>(), value.before_cancel);
    add_transaction_trace(target["first_position_write"].to<ArduinoJson::JsonObject>(), value.first_position_write);
}
}

void remote_init()
{
    load_pitch_setting();
    // Prepare UART synchronously on app_main, before audio and capture tasks.
    GetHAL().initCollectorMotion();
    configure_pitch_motion(GetStackChan().motion().pitchServo());
    GetStackChan().motion().yawServo().setReleasePolicy(kWebYawReleaseMinHoldMs, kWebYawReleaseMaxHoldMs, kWebYawReleaseToleranceTenths);
    motor_ready = true;
    // Collector already owns the STA connection. Never restart Wi-Fi or force a channel here.
    espnow_config_t config = ESPNOW_INIT_CONFIG_DEFAULT();
    config.forward_enable = false;
    config.forward_switch_channel = false;
    config.receive_enable.forward = false;
    config.receive_enable.data = true;
    const auto error = espnow_init(&config);
    if (error == ESP_OK) ready = espnow_set_config_for_data_type(ESPNOW_DATA_TYPE_DATA, true, received) == ESP_OK;
    ESP_LOGI("CollectorRemote", "ESP-NOW receiver %s; ID=1, channel follows Wi-Fi", ready ? "ready" : "unavailable");
    ESP_ERROR_CHECK(xTaskCreate(motor_task, "collector_motor", 6144, nullptr, 3, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
}
bool remote_arm(bool active)
{
    std::lock_guard<std::mutex> storage_lock(pitch_storage_mutex);
    std::lock_guard<std::mutex> lock(mutex);
    if (active && !ready) return false;
    if (!active) { stop_latch.request(); pitch_requests.stop(); manual_yaw.cancel(); web_yaw_active = false;
        tracking.request(false, esp_timer_get_time()); }
    control_requested = active;
    return true;
}
bool remote_enabled() { return enabled; }
esp_err_t remote_set_tracking(bool active)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (active && (!control_requested || !motor_ready || manual_yaw.pending || manual_yaw.state == ManualYaw::moving))
        return ESP_ERR_INVALID_STATE;
    tracking.request(active, esp_timer_get_time());
    return ESP_OK;
}
bool remote_tracking_requested()
{
    std::lock_guard<std::mutex> lock(mutex);
    return tracking.requested();
}
void remote_tracking_json(ArduinoJson::JsonObject target)
{
    std::lock_guard<std::mutex> lock(mutex);
    target["requested"] = tracking.requested();
    target["active"] = tracking.active();
    target["state"] = tracking.state();
    target["fault_latched"] = tracking.faulted();
    target["policy"] = "one_look_one_turn";
    target["moving"] = tracking.moving();
    target["range_deg"] = FaceTracking::kRange / 10.0f;
    target["max_command_deg"] = FaceTracking::kMaxCommandTenths / 10.0f;
    target["full_speed_up_to_deg"] = FaceTracking::kRateRefTenths / 10.0f;
    target["min_turn_speed"] = FaceTracking::kMinTurnSpeed;
    target["deadband_deg"] = FaceTracking::kDeadbandTenths / 10.0f;
    target["deadband_px"] = FaceTracking::kDeadbandPx;
    target["assumed_fov_deg"] = FaceTracking::kFovTenths / 10.0f;
    target["precomp_deg"] = FaceTracking::kPrecompTenths / 10.0f;
    target["look_delay_ms"] = FaceTracking::kLookDelayUs / 1000;
    target["look_frames"] = 2;
    target["still_deg"] = FaceTracking::kStillTenths / 10.0f;
    target["lead_ms"] = FaceTracking::kLeadUs / 1000;
    target["max_lead_deg"] = FaceTracking::kMaxLeadTenths / 10.0f;
    target["return_delay_ms"] = FaceTracking::kReturnDelayUs / 1000;
    target["no_progress_timeout_ms"] = FaceTracking::kProgressBaseUs / 1000;
    target["no_progress_per_deg_ms"] = FaceTracking::kProgressPerDegUs / 1000;
    target["no_progress_time_basis"] = "cumulative_active_command_time";
    target["min_progress_deg"] = FaceTracking::kProgressQuantum / 10.0f;
    target["speed_semantics"] = "bounded_command_model_not_measured_physical_speed";
    target["cancel_semantics"] = "torque_release_not_position_hold";
    target["command_count"] = tracking.commands();
    target["cancel_count"] = tracking.cancels();
    target["frame_count"] = tracking.frames();
    if (tracking.has_anchor()) {
        target["anchor_deg"] = tracking.anchor() / 10.0f;
        target["target_deg"] = tracking.target() / 10.0f;
    } else {
        target["anchor_deg"] = nullptr; target["target_deg"] = nullptr;
    }
    // Last turn: aim is the face estimate, command adds the precompensation,
    // arrived is the measured angle when the turn counted as finished.
    const auto& turn = tracking.last_turn();
    auto last = target["last_turn"].to<ArduinoJson::JsonObject>();
    last["valid"] = turn.valid;
    if (turn.valid) {
        last["returning"] = turn.returning;
        last["start_deg"] = turn.start / 10.0f;
        last["aim_deg"] = turn.aim / 10.0f;
        last["command_deg"] = turn.command / 10.0f;
        last["speed"] = FaceTracking::turn_speed(turn.command - turn.start);
        last["lead_deg"] = turn.lead / 10.0f;
        last["face_motion_deg"] = turn.motion / 10.0f; // between the two frames of the look
        last["face_motion_dt_ms"] = static_cast<int>(turn.motion_dt_us / 1000);
        last["settled"] = turn.settled;
        if (turn.settled) {
            last["arrived_deg"] = turn.arrived / 10.0f;
            last["error_deg"] = (turn.arrived - turn.aim) / 10.0f;
            last["settle"] = turn.by_deadline ? "deadline" : "stable";
        }
    }
    // Measured image shift per degree of head turn (first look after each turn).
    const auto& calibration = tracking.calibration();
    target["calibration"]["samples"] = calibration.samples;
    if (calibration.samples) {
        target["calibration"]["px_per_deg"] = calibration.last_px_per_deg_x10 / 10.0f;
        target["calibration"]["mean_px_per_deg"] = static_cast<float>(calibration.sum_px_per_deg_x10) / calibration.samples / 10.0f;
    } else {
        target["calibration"]["px_per_deg"] = nullptr; target["calibration"]["mean_px_per_deg"] = nullptr;
    }
    target["calibration"]["assumed_px_per_deg"] = FaceTracking::kFrameWidth * 10.0f / FaceTracking::kFovTenths;
}
esp_err_t remote_step_yaw(int delta_tenths, int speed, bool remote_like, uint32_t* request_seq)
{
    if (delta_tenths == 0 || delta_tenths % 10 != 0 || delta_tenths < -kWebYawMaxDeltaTenths || delta_tenths > kWebYawMaxDeltaTenths) return ESP_ERR_INVALID_ARG;
    if (speed < kWebYawMinSpeed || speed > kWebYawMaxSpeed) return ESP_ERR_INVALID_ARG;
    std::lock_guard<std::mutex> lock(mutex);
    // Manual yaw may take over an in-flight tracking command; motor_task first
    // consumes tracking's cancellation and then starts this measured step.
    const bool allowed = control_requested && enabled && motor_ready;
    const bool settled = tracking.active() || yaw_trajectory_complete;
    const int64_t now = esp_timer_get_time();
    const bool accepted = (delta_tenths == kWebYawStepTenths || delta_tenths == -kWebYawStepTenths)
        ? manual_yaw.submit_step(delta_tenths, speed, remote_like, allowed, yaw_feedback.valid, settled, yaw_feedback.angle, yaw_feedback_timestamp, now)
        : manual_yaw.submit(delta_tenths, speed, remote_like, allowed, yaw_feedback.valid, settled, yaw_feedback.angle, yaw_feedback_timestamp, now);
    if (!accepted) return ESP_ERR_INVALID_STATE;
    tracking.request(false, now, "manual_override");
    if (request_seq) *request_seq = manual_yaw.sequence;
    return ESP_OK;
}

esp_err_t remote_yaw_slot(int slot, int speed, uint32_t* request_seq)
{
    if (slot < -kWebYawSlots || slot > kWebYawSlots) return ESP_ERR_INVALID_ARG;
    if (speed < kWebYawMinSpeed || speed > kWebYawMaxSpeed) return ESP_ERR_INVALID_ARG;
    std::lock_guard<std::mutex> lock(mutex);
    const int64_t now = esp_timer_get_time();
    if (!manual_yaw.submit_slot(slot, speed, false, control_requested && enabled && motor_ready, yaw_feedback.valid,
            tracking.active() || yaw_trajectory_complete, yaw_feedback.angle, yaw_feedback_timestamp, now)) return ESP_ERR_INVALID_STATE;
    tracking.request(false, now, "manual_override");
    if (request_seq) *request_seq = manual_yaw.sequence;
    return ESP_OK;
}

esp_err_t remote_set_pitch(int angle_tenths, uint32_t* request_seq)
{
    if (!valid_pitch_setting(angle_tenths)) return ESP_ERR_INVALID_ARG;
    std::lock_guard<std::mutex> storage_lock(pitch_storage_mutex);
    bool already_saved;
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (!control_requested || !motor_ready) return ESP_ERR_INVALID_STATE;
        already_saved = pitch_persisted && pitch_configured == angle_tenths;
    }
    esp_err_t error = ESP_OK;
    if (!already_saved) {
        nvs_handle_t handle;
        error = nvs_open("collector", NVS_READWRITE, &handle);
        if (error == ESP_OK) {
            struct Store {
                nvs_handle_t handle;
                int set(int target) { return nvs_set_i16(handle, "pitch_deg10", static_cast<int16_t>(target)); }
                int commit() { return nvs_commit(handle); }
            } store{handle};
            error = persist_pitch_setting(store, angle_tenths);
            nvs_close(handle);
        }
    }
    std::lock_guard<std::mutex> lock(mutex);
    pitch_storage_error = error;
    if (error != ESP_OK) return error;
    pitch_configured = angle_tenths;
    pitch_persisted = true;
    const uint32_t accepted = pitch_requests.submit_committed(angle_tenths, esp_timer_get_time());
    tracking.request(false, esp_timer_get_time(), "manual_override");
    pitch_state = "queued";
    pitch_has_command_start = false;
    if (request_seq) *request_seq = accepted;
    return ESP_OK;
}

esp_err_t remote_servo_tune(ServoTuneRequest request, uint32_t* request_seq)
{
    if (!servo_tune_request_valid(request)) return ESP_ERR_INVALID_ARG;
    std::lock_guard<std::mutex> lock(mutex);
    if (!motor_ready || tune_pending) return ESP_ERR_INVALID_STATE;
    request.sequence = ++tune_sequence;
    request.requested_us = esp_timer_get_time();
    pending_tune = request; tune_pending = true; tune_state[request.axis] = "queued";
    if (request_seq) *request_seq = request.sequence;
    return ESP_OK;
}

void remote_pitch_setting_json(ArduinoJson::JsonObject target)
{
    std::lock_guard<std::mutex> lock(mutex);
    const auto& request = pitch_requests.current();
    const int64_t now = esp_timer_get_time();
    const bool valid = pitch_feedback.valid && feedback_timestamp > 0 && now - feedback_timestamp < 1000000;
    target["configured_deg"] = pitch_configured / 10.0f;
    target["persisted"] = pitch_persisted;
    target["request_seq"] = request.sequence;
    target["request_source"] = request.from_boot ? "boot" : "user";
    target["requested_deg"] = request.target / 10.0f;
    target["request_timestamp_us"] = request.timestamp;
    target["state"] = pitch_state;
    target["feedback_valid"] = valid;
    target["feedback_timestamp_us"] = feedback_timestamp;
    if (valid) target["measured_deg"] = pitch_feedback.angle / 10.0f;
    else target["measured_deg"] = nullptr;
    if (pitch_has_command_start) target["command_start_deg"] = pitch_command_start / 10.0f;
    else target["command_start_deg"] = nullptr;
    target["storage_error"] = pitch_storage_error;
    target["speed"] = StartupPose::kSpeed; // Requested value, overridden by the profile.
    target["motion_profile"] = kPitchMotionProfile;
    target["spring_stiffness"] = kPitchMotionStiffness;
    target["spring_damping"] = kPitchMotionDamping;
    target["min_deg"] = kPitchSettingMinimum / 10.0f;
    target["max_deg"] = kPitchSettingMaximum / 10.0f;
    target["state_semantics"] = "trajectory_state_not_measured_arrival";
}
RemoteSnapshot remote_snapshot()
{
    std::lock_guard<std::mutex> lock(mutex);
    RemoteSnapshot result;
    result.ready = ready.load(); result.requested = control_requested; result.enabled = enabled.load();
    result.rx_packets = sequence; result.receiver_id = kReceiver;
    if (sequence) {
        const auto& packet = history[sequence % kHistory];
        result.last_rx_us = packet.timestamp; result.rssi = packet.rssi;
    }
    result.reaction = reaction; result.reaction_seq = reaction_seq; result.reaction_timestamp_us = reaction_timestamp;
    result.display_requested = center_input.display_requested();
    result.center_event_seq = center_input.event_sequence();
    result.center_event_timestamp_us = center_input.event_timestamp();
    result.mode_left_seq = mode_input.left_sequence();
    result.mode_right_seq = mode_input.right_sequence();
    result.mode_up_seq = mode_input.up_sequence();
    result.mode_down_seq = mode_input.down_sequence();
    result.yaw_valid = yaw_feedback.valid; result.pitch_valid = pitch_feedback.valid;
    if (yaw_feedback.valid) result.yaw_angle_deg = yaw_feedback.angle / 10.0f;
    if (pitch_feedback.valid) result.pitch_angle_deg = pitch_feedback.angle / 10.0f;
    result.feedback_timestamp_us = feedback_timestamp;
    return result;
}
const char* remote_display_state()
{
    std::lock_guard<std::mutex> lock(mutex);
    if (!control_requested) return "OFF";
    if (!motor_ready) return "WAIT";
    return enabled ? "ON" : "READY";
}
void remote_json(ArduinoJson::JsonObject target, uint32_t after, bool include_history, bool include_servo_diagnostics)
{
    uint8_t channel = 0; wifi_second_chan_t secondary;
    esp_wifi_get_channel(&channel, &secondary);
    std::lock_guard<std::mutex> lock(mutex);
    target["ready"] = ready.load();
    target["receiver_id"] = kReceiver;
    target["wifi_channel"] = channel;
    target["valid"] = sequence > 0;
    target["rx_packets"] = sequence;
    target["rejected_packets"] = rejected;
    if (rejected) {
        auto diagnostic = target["last_rejected"].to<ArduinoJson::JsonObject>();
        diagnostic["size"] = rejected_size;
        diagnostic["timestamp_us"] = rejected_timestamp;
        if (rejected_size == 8) {
            auto signed_word = [](const uint8_t* bytes) { return static_cast<int16_t>(uint16_t(bytes[0]) | uint16_t(bytes[1]) << 8); };
            diagnostic["target_id"] = rejected_payload[0];
            diagnostic["yaw_raw"] = signed_word(rejected_payload.data() + 1);
            diagnostic["pitch_raw"] = signed_word(rejected_payload.data() + 3);
            diagnostic["speed_raw"] = signed_word(rejected_payload.data() + 5);
            diagnostic["auxiliary_raw"] = rejected_payload[7];
        }
    }
    target["other_target_packets"] = wrong_target;
    const bool sender_center_v1 = sequence && history[sequence % kHistory].command.center_v1;
    target["heartbeat_supported"] = sender_center_v1;
    target["center_press_supported"] = true;
    target["sender_center_supported"] = sender_center_v1;
    target["raw_controls_supported"] = false;
    target["auxiliary_output_enabled"] = false;
    target["feedback_required_for_control"] = false;
    target["control_mode"] = "standalone";
    target["yaw_control_enabled"] = false;
    target["yaw_input_to_target_sign"] = nullptr;
    target["yaw_input_role"] = "mode_toggle";
    target["pitch_control_enabled"] = false;
    target["pitch_input_role"] = "mode_toggle";
    target["pitch_mapping"] = "none";
    target["pitch_neutral_input_deg"] = 47.1f;
    target["pitch_neutral_target_deg"] = pitch_configured / 10.0f;
    target["mode_left_seq"] = mode_input.left_sequence();
    target["mode_right_seq"] = mode_input.right_sequence();
    target["mode_up_seq"] = mode_input.up_sequence();
    target["mode_down_seq"] = mode_input.down_sequence();
    target["direction_input_role"] = "mode_toggle_joystick_page";
    target["auxiliary_input_role"] = "record_only";
    target["center_input_role"] = "display_toggle";
    target["nod_trigger"] = "none";
    target["reaction_duration_ms"] = kReactionDurationMs;
    auto gesture = target["gesture"].to<ArduinoJson::JsonObject>();
    gesture["reaction"] = reaction == RemoteReaction::Heart ? "heart" :
        (reaction == RemoteReaction::Reject ? "reject" : "none");
    gesture["reaction_seq"] = reaction_seq;
    gesture["reaction_timestamp_us"] = reaction_timestamp;
    gesture["reaction_duration_ms"] = kReactionDurationMs;
    gesture["button_semantics"] = "joystick_center_sequence";
    gesture["display_requested"] = center_input.display_requested();
    gesture["center_event_seq"] = center_input.event_sequence();
    gesture["center_event_timestamp_us"] = center_input.event_timestamp();
    target["requires_observer"] = false;
    target["lease_ms"] = 0; // Legacy field; no lease in standalone mode.
    if (sequence) add_packet(target["last"].to<ArduinoJson::JsonObject>(), history[sequence % kHistory]);
    if (include_history) {
        const uint32_t first = sequence >= kHistory ? sequence - kHistory + 1 : 1;
        target["history_lost"] = after > 0 && after + 1 < first ? first - after - 1 : 0;
        auto packets = target["packets"].to<ArduinoJson::JsonArray>();
        for (uint32_t i = std::max(first, after + 1); i <= sequence; ++i) add_packet(packets.add<ArduinoJson::JsonObject>(), history[i % kHistory]);
    }
    auto motor = target["motor"].to<ArduinoJson::JsonObject>();
    auto web_yaw = motor["web_yaw"].to<ArduinoJson::JsonObject>();
    web_yaw["request_seq"] = manual_yaw.sequence;
    web_yaw["target_deg"] = manual_yaw.target / 10.0f;
    web_yaw["state"] = manual_yaw.state;
    web_yaw["requested_us"] = manual_yaw.requested_us;
    web_yaw["speed"] = manual_yaw.speed;
    web_yaw["default_speed"] = kWebYawDefaultSpeed;
    web_yaw["path"] = manual_yaw.remote_like ? "remote_like" : "web";
    web_yaw["max_delta_deg"] = kWebYawMaxDeltaTenths / 10;
    web_yaw["step_deg"] = kWebYawStepTenths / 10;
    web_yaw["slots"] = kWebYawSlots;
    web_yaw["range_deg"] = kWebYawRangeTenths / 10;
    web_yaw["target_slot"] = manual_yaw.sequence ? web_yaw_slot(manual_yaw.target) : 0;
    web_yaw["measured_slot"] = yaw_feedback.valid ? web_yaw_slot(yaw_feedback.angle) : 0;
    motor["yaw_trajectory_complete"] = yaw_trajectory_complete;
    motor["enabled"] = enabled.load();
    motor["requested"] = control_requested;
    motor["feedback_valid"] = yaw_feedback.valid && pitch_feedback.valid;
    motor["state"] = motor_state;
    motor["timestamp_us"] = feedback_timestamp;
    motor["feedback_age_ms"] = feedback_timestamp ? (esp_timer_get_time() - feedback_timestamp) / 1000 : -1;
    // Legacy packet application fields are intentionally zero/null: receiving
    // a mode event does not mean an actuator command has been applied.
    motor["applied_seq"] = 0;
    motor["yaw_target_deg"] = nullptr; motor["applied_speed"] = nullptr;
    motor["pitch_target_deg"] = nullptr;
    motor["pitch_owner"] = "setting";
    motor["pitch_motion_profile"] = kPitchMotionProfile;
    motor["speed_limit"] = kRemoteSpeedLimit;
    motor["startup_pitch_target_deg"] = boot_pitch_target / 10.0f;
    motor["startup_pose_state"] = startup_state;
    auto nod = motor["nod"].to<ArduinoJson::JsonObject>();
    nod["state"] = "disabled";
    nod["request_seq"] = 0; nod["phase"] = 0; nod["completed_trajectories"] = 0;
    nod["speed"] = nullptr; nod["leg_ms"] = nullptr;
    nod["low_deg"] = nullptr; nod["high_deg"] = nullptr; nod["target_deg"] = nullptr;
    add_feedback(motor["yaw"].to<ArduinoJson::JsonObject>(), yaw_feedback);
    add_feedback(motor["pitch"].to<ArduinoJson::JsonObject>(), pitch_feedback);
    add_motion_summary(motor["yaw"]["motion_summary"].to<ArduinoJson::JsonObject>(), yaw_trace);
    add_motion_summary(motor["pitch"]["motion_summary"].to<ArduinoJson::JsonObject>(), pitch_trace);
    if (motor_ready) {
        auto& motion = GetStackChan().motion();
        add_servo_tuning(motor["yaw"]["tuning"].to<ArduinoJson::JsonObject>(), 0, motion.yawServo(), yaw_deadband);
        add_servo_tuning(motor["pitch"]["tuning"].to<ArduinoJson::JsonObject>(), 1, motion.pitchServo(), pitch_deadband);
    }
    if (include_servo_diagnostics) {
        add_servo_diagnostics(motor["yaw"]["diagnostics"].to<ArduinoJson::JsonObject>(), yaw_diagnostics);
        add_servo_diagnostics(motor["pitch"]["diagnostics"].to<ArduinoJson::JsonObject>(), pitch_diagnostics);
        motor["yaw"]["diagnostics"]["trajectory_complete"] = yaw_trajectory_complete;
        motor["pitch"]["diagnostics"]["trajectory_complete"] = pitch_trajectory_complete;
        add_deadband_snapshot(motor["yaw"]["diagnostics"]["deadband_configuration"].to<ArduinoJson::JsonObject>(), yaw_deadband);
        add_deadband_snapshot(motor["pitch"]["diagnostics"]["deadband_configuration"].to<ArduinoJson::JsonObject>(), pitch_deadband);
        add_tracking_torque(motor["yaw"]["diagnostics"]["tracking_torque"].to<ArduinoJson::JsonObject>(), yaw_tracking_torque);
        add_motion_trace(motor["yaw"]["diagnostics"]["motion_trace"].to<ArduinoJson::JsonObject>(), yaw_trace);
        add_motion_trace(motor["pitch"]["diagnostics"]["motion_trace"].to<ArduinoJson::JsonObject>(), pitch_trace);
    }
}
}
