/* SPDX-License-Identifier: MIT */
#include "remote.h"
#include "remote_protocol.h"
#include "startup_pose.h"
#include <hal/hal.h>
#include <stackchan/stackchan.h>
#include <espnow.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <esp_log.h>
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
uint32_t sequence = 0, rejected = 0, wrong_target = 0;
std::array<uint8_t, 8> rejected_payload{};
size_t rejected_size = 0;
int64_t rejected_timestamp = 0;
int64_t feedback_timestamp = 0;
// Collector is also the remote receiver app: it does not need an Android lease.
bool control_requested = true;
uint32_t arm_after_seq = 0, applied_seq = 0;
std::atomic_bool ready{false}, motor_ready{false}, enabled{false};
stackchan::motion::ServoFeedback yaw_feedback, pitch_feedback;
int applied_yaw = 0, applied_pitch = 0, applied_speed = 0;
const char* motor_state = "starting";
const char* startup_state = "waiting_feedback";

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
    return ESP_OK;
}

void motor_task(void*)
{
    // Boot pitch is a one-shot pose; the official receiver can preempt it.
    // The official remote works independently of the observer connection.
    bool was_enabled = false;
    int64_t last_feedback = 0;
    StartupPose startup;
    TickType_t wake = xTaskGetTickCount();
    while (true) {
        const int64_t now = esp_timer_get_time();
        Packet packet;
        bool active;
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
        if (now - last_feedback >= 100000) {
            const auto yaw = motion.yawServo().readFeedback();
            const auto pitch = motion.pitchServo().readFeedback();
            std::lock_guard<std::mutex> lock(mutex);
            yaw_feedback = yaw; pitch_feedback = pitch;
            feedback_timestamp = esp_timer_get_time();
            last_feedback = now;
            startup_feedback = pitch;
            // Feedback is observation data. Factory receiver control does not
            // disable itself because an optional position read failed.
        }
        {
            // A stop may arrive while the UART reads are in progress.
            std::lock_guard<std::mutex> lock(mutex);
            active = control_requested;
            after = arm_after_seq;
            packet = history[sequence % kHistory];
        }
        const auto pitch_limits = motion.pitchServo().getAngleLimit();
        const bool valid_start = startup_feedback.valid && startup_feedback.angle >= pitch_limits.x &&
                                 startup_feedback.angle <= pitch_limits.y;
        const auto pose_action = startup.step(now, active, packet.seq > after, valid_start,
                                              motion.pitchServo().isTrajectoryComplete());
        if (pose_action == StartupPose::Action::Start) {
            motion.pitchServo().moveFromFeedbackWithSpeed(startup_feedback, StartupPose::kPitch, StartupPose::kSpeed);
            ESP_LOGI("CollectorRemote", "Startup pitch: measured %.1f -> target %.1f deg (10%%), speed %d",
                     startup_feedback.angle / 10.0f, StartupPose::kPitch / 10.0f, StartupPose::kSpeed);
        }
        { std::lock_guard<std::mutex> lock(mutex); startup_state = startup.state(); }
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
                const auto limited = limit_remote_motion(packet.command);
                const int yaw = limited.yaw;
                const int pitch = limited.pitch;
                const int speed = limited.speed;
                // Sync to the physical pose for a new control session, then retain the
                // official spring velocity across subsequent remote packets.
                motion.setAutoAngleSyncEnabled(applied_seq <= after);
                motion.moveWithSpeed(yaw, pitch, speed);
                // Use the factory position-write path; no extra per-packet torque command.
                motion.update();
                std::lock_guard<std::mutex> lock(mutex);
                applied_seq = packet.seq;
                applied_yaw = yaw; applied_pitch = pitch; applied_speed = speed;
                motor_state = "following";
            }
            // Match factory remote control: finish the spring trajectory even
            // when a held joystick sends no new packet. Servo::update retains
            // the factory automatic torque release when the motion has settled.
            if (applied_seq > after) motion.update();
            else {
                if (pose_action != StartupPose::Action::None) motion.pitchServo().update();
                std::lock_guard<std::mutex> lock(mutex);
                motor_state = strcmp(startup_state, "moving") == 0 ? "startup_pose" : "waiting_for_input";
            }
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
}

void remote_init()
{
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
    std::lock_guard<std::mutex> lock(mutex);
    if (active && !ready) return false;
    if (active && !control_requested) arm_after_seq = sequence;
    control_requested = active;
    return true;
}
bool remote_enabled() { return enabled; }
const char* remote_display_state()
{
    std::lock_guard<std::mutex> lock(mutex);
    if (!control_requested) return "OFF";
    if (!motor_ready) return "WAIT";
    return applied_seq > arm_after_seq ? "ON" : "READY";
}
void remote_json(ArduinoJson::JsonObject target, uint32_t after, bool include_history)
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
    target["heartbeat_supported"] = false;
    target["raw_controls_supported"] = false;
    target["auxiliary_output_enabled"] = false;
    target["feedback_required_for_control"] = false;
    target["control_mode"] = "standalone";
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
    motor["enabled"] = enabled.load();
    motor["requested"] = control_requested;
    motor["feedback_valid"] = yaw_feedback.valid && pitch_feedback.valid;
    motor["state"] = motor_state;
    motor["timestamp_us"] = feedback_timestamp;
    motor["feedback_age_ms"] = feedback_timestamp ? (esp_timer_get_time() - feedback_timestamp) / 1000 : -1;
    motor["applied_seq"] = applied_seq;
    if (applied_seq) { motor["yaw_target_deg"] = applied_yaw / 10.0f; motor["pitch_target_deg"] = applied_pitch / 10.0f; motor["applied_speed"] = applied_speed; }
    motor["speed_limit"] = kRemoteSpeedLimit;
    motor["startup_pitch_target_deg"] = StartupPose::kPitch / 10.0f;
    motor["startup_pose_state"] = startup_state;
    add_feedback(motor["yaw"].to<ArduinoJson::JsonObject>(), yaw_feedback);
    add_feedback(motor["pitch"].to<ArduinoJson::JsonObject>(), pitch_feedback);
}
}
