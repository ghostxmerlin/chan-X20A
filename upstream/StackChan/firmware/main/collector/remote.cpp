/* SPDX-License-Identifier: MIT */
#include "remote.h"
#include "remote_protocol.h"
#include "remote_actuation.h"
#include "startup_pose.h"
#include "remote_stop.h"
#include "pitch_setting.h"
#include "web_control.h"
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
int64_t feedback_timestamp = 0;
// Collector is also the remote receiver app: it does not need an Android lease.
bool control_requested = true;
RemoteStopLatch stop_latch;
ManualYaw manual_yaw;
std::atomic_bool web_yaw_active{false};
uint32_t arm_after_seq = 0, applied_seq = 0;
std::atomic_bool ready{false}, motor_ready{false}, enabled{false};
stackchan::motion::ServoFeedback yaw_feedback, pitch_feedback;
stackchan::motion::ServoDiagnostics yaw_diagnostics, pitch_diagnostics;
stackchan::motion::ServoDeadbandSnapshot yaw_deadband, pitch_deadband;
int64_t diagnostics_timestamp = 0;
bool yaw_trajectory_complete = false, pitch_trajectory_complete = false;
bool yaw_command_started = false;
int applied_yaw = 0, applied_speed = 0;
const char* motor_state = "starting";
const char* startup_state = "waiting_feedback";
RemoteGestureInput gesture_input;
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
    const auto old_target = remote_yaw_target(history[sequence % kHistory].command);
    const auto new_target = remote_yaw_target(command);
    if (!command.center_v1 || old_target.yaw != new_target.yaw || old_target.speed != new_target.speed) {
        manual_yaw.cancel(); web_yaw_active = false;
    }
    Packet packet;
    packet.command = command;
    packet.seq = ++sequence;
    packet.timestamp = esp_timer_get_time();
    packet.rssi = rx->rssi;
    packet.channel = rx->channel;
    memcpy(packet.source.data(), source, 6);
    history[sequence % kHistory] = packet;
    const auto event = gesture_input.accept(command.pitch);
    if (event != RemoteReaction::None) {
        reaction = event;
        reaction_seq = packet.seq;
        reaction_timestamp = packet.timestamp;
    }
    // Screen input is consumed here, independently of motor_task and feedback.
    center_input.accept(command, packet.source, packet.timestamp);
    return ESP_OK;
}

void motor_task(void*)
{
    // Boot pitch is a one-shot pose, independent of yaw-only remote input.
    // The official remote works independently of the observer connection.
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
        Packet packet;
        bool active;
        bool stop_pending = false;
        bool pitch_cancelled = false;
        PitchSettingRequest latest_pitch;
        uint32_t after;
        {
            std::lock_guard<std::mutex> lock(mutex);
            packet = history[sequence % kHistory];
            active = control_requested;
            after = arm_after_seq;
        }
        if (!motor_ready) {
            if (active) {
                { std::lock_guard<std::mutex> lock(mutex); motor_state = "initializing"; }
                GetHAL().initCollectorMotion();
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
            const auto pitch = motion.pitchServo().readFeedback();
            std::lock_guard<std::mutex> lock(mutex);
            yaw_feedback = yaw; pitch_feedback = pitch;
            feedback_timestamp = esp_timer_get_time();
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
            after = arm_after_seq;
            packet = history[sequence % kHistory];
            stop_pending = stop_latch.consume();
            latest_pitch = pitch_requests.current();
            pitch_cancelled = pitch_requests.cancelled(latest_pitch);
        }
        if (stop_pending) {
            apply_remote_stop(startup, motion, now);
            was_enabled = false;
            yaw_command_started = false;
        }
        if (latest_pitch.sequence != pitch_request.sequence) {
            // Only a successfully committed, explicit request replaces pitch.
            // An intervening stop cancels earlier queued requests as well.
            motion.pitchServo().cancelMotion();
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
        }
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (pitch_request.from_boot) startup_state = startup.state();
            if (pitch_requests.current().sequence == pitch_request.sequence) pitch_state = startup.state();
        }
        if (!active) {
            if (was_enabled) {
                motion.yawServo().cancelMotion();
                motion.pitchServo().cancelMotion();
            }
            enabled = false;
            { std::lock_guard<std::mutex> lock(mutex); motor_state = "disarmed"; }
        } else {
            enabled = true;
            // Do not replay any packet received before the user enabled control.
            if (packet.seq > after && packet.seq != applied_seq) {
                // Sync to the physical pose for a new control session, then retain the
                // official spring velocity across subsequent remote packets.
                const auto target = remote_yaw_target(packet.command);
                // Center-v1 heartbeats refresh link/press observation, but must
                // not wake an unchanged yaw target or delay torque release.
                if (!packet.command.center_v1 || applied_seq <= after ||
                    target.yaw != applied_yaw || target.speed != applied_speed) {
                    apply_remote_yaw(motion.yawServo(), packet.command, applied_seq <= after);
                    yaw_command_started = true; // motor-task owned
                }
                std::lock_guard<std::mutex> lock(mutex);
                applied_seq = packet.seq;
                applied_yaw = target.yaw; applied_speed = target.speed;
                motor_state = "following";
            }
            // Match factory remote control: finish the spring trajectory even
            // when a held joystick sends no new packet. Servo::update retains
            // the factory automatic torque release when the motion has settled.
            {
                std::lock_guard<std::mutex> lock(mutex);
                // Recheck stop after feedback reads; do not move from HTTP or a second UART owner.
                if (control_requested && manual_yaw.take(esp_timer_get_time(), manual_yaw_idle(yaw_command_started, motion.yawServo().isTrajectoryComplete()))) {
                    const bool fresh = yaw_feedback.valid && feedback_timestamp > 0 &&
                        esp_timer_get_time() - feedback_timestamp < 1000000;
                    const auto limits = motion.yawServo().getAngleLimit();
                    const bool accepted = fresh && manual_yaw.target >= limits.x && manual_yaw.target <= limits.y &&
                        motion.yawServo().moveFromFeedbackWithSpeed(yaw_feedback, manual_yaw.target, 100);
                    manual_yaw.finish(accepted); web_yaw_active = accepted;
                    if (accepted) yaw_command_started = true;
                }
            }
            if (applied_seq > after || web_yaw_active) motion.yawServo().update();
            else {
                std::lock_guard<std::mutex> lock(mutex);
                motor_state = strcmp(startup.state(), "moving") == 0 ?
                    (pitch_request.from_boot ? "startup_pose" : "pitch_setting") : "waiting_for_input";
            }
            // Vertical reactions and center display toggles are independent of motion.
            // Pitch runs only for this boot or an explicit committed setting.
            if (pose_action == StartupPose::Action::Start || pose_action == StartupPose::Action::Update)
                motion.pitchServo().update();
        }
        // One read-only configuration probe per axis, after boot has settled and
        // a fresh valid position arrived. No retries, EEPROM writes or motion.
        // The motor task remains the sole UART owner; HTTP only reads the copy.
        if (now >= 5000000 && !yaw_deadband_attempted && sampled_yaw.valid &&
            motion.yawServo().isTrajectoryComplete()) {
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
    } else {
        target["cw_raw"] = nullptr;
        target["ccw_raw"] = nullptr;
    }
}
}

void remote_init()
{
    load_pitch_setting();
    // Prepare UART synchronously on app_main, before audio and capture tasks.
    GetHAL().initCollectorMotion();
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
    if (active && !control_requested) arm_after_seq = sequence;
    if (!active) { stop_latch.request(); pitch_requests.stop(); manual_yaw.cancel(); web_yaw_active = false; }
    control_requested = active;
    return true;
}
bool remote_enabled() { return enabled; }
esp_err_t remote_step_yaw(int delta_tenths, uint32_t* request_seq)
{
    if (delta_tenths != -50 && delta_tenths != 50) return ESP_ERR_INVALID_ARG;
    std::lock_guard<std::mutex> lock(mutex);
    if ((sequence > arm_after_seq && sequence != applied_seq) ||
        !manual_yaw.submit(delta_tenths, control_requested && enabled && motor_ready,
            yaw_feedback.valid, yaw_trajectory_complete, yaw_feedback.angle,
            feedback_timestamp, esp_timer_get_time())) return ESP_ERR_INVALID_STATE;
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
    pitch_state = "queued";
    pitch_has_command_start = false;
    if (request_seq) *request_seq = accepted;
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
    target["speed"] = StartupPose::kSpeed;
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
    return applied_seq > arm_after_seq ? "ON" : "READY";
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
    target["yaw_control_enabled"] = true;
    target["yaw_input_to_target_sign"] = -1;
    target["pitch_control_enabled"] = kRemotePitchControlEnabled;
    target["pitch_input_role"] = "home_reaction";
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
    motor["yaw_trajectory_complete"] = yaw_trajectory_complete;
    motor["enabled"] = enabled.load();
    motor["requested"] = control_requested;
    motor["feedback_valid"] = yaw_feedback.valid && pitch_feedback.valid;
    motor["state"] = motor_state;
    motor["timestamp_us"] = feedback_timestamp;
    motor["feedback_age_ms"] = feedback_timestamp ? (esp_timer_get_time() - feedback_timestamp) / 1000 : -1;
    motor["applied_seq"] = applied_seq;
    if (applied_seq) { motor["yaw_target_deg"] = applied_yaw / 10.0f; motor["applied_speed"] = applied_speed; }
    motor["pitch_target_deg"] = nullptr; // No pitch target is applied from remote input.
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
    if (include_servo_diagnostics) {
        add_servo_diagnostics(motor["yaw"]["diagnostics"].to<ArduinoJson::JsonObject>(), yaw_diagnostics);
        add_servo_diagnostics(motor["pitch"]["diagnostics"].to<ArduinoJson::JsonObject>(), pitch_diagnostics);
        motor["yaw"]["diagnostics"]["trajectory_complete"] = yaw_trajectory_complete;
        motor["pitch"]["diagnostics"]["trajectory_complete"] = pitch_trajectory_complete;
        add_deadband_snapshot(motor["yaw"]["diagnostics"]["deadband_configuration"].to<ArduinoJson::JsonObject>(), yaw_deadband);
        add_deadband_snapshot(motor["pitch"]["diagnostics"]["deadband_configuration"].to<ArduinoJson::JsonObject>(), pitch_deadband);
    }
}
}
