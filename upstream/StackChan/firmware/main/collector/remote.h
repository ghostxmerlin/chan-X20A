/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <esp_err.h>
#include <cstdint>
#include "remote_gestures.h"
namespace collector {
struct RemoteSnapshot {
    bool ready = false, requested = false, enabled = false;
    uint32_t rx_packets = 0;
    int64_t last_rx_us = 0;
    int rssi = 0;
    uint8_t receiver_id = 1;
    RemoteReaction reaction = RemoteReaction::None;
    uint32_t reaction_seq = 0;
    int64_t reaction_timestamp_us = 0;
    // Requested state only; the display module reports software display application.
    bool display_requested = true;
    uint32_t center_event_seq = 0;
    int64_t center_event_timestamp_us = 0;
    uint32_t mode_left_seq = 0, mode_right_seq = 0, mode_up_seq = 0, mode_down_seq = 0;
    bool yaw_valid = false, pitch_valid = false;
    float yaw_angle_deg = 0, pitch_angle_deg = 0;
    int64_t feedback_timestamp_us = 0;
};
RemoteSnapshot remote_snapshot();
void remote_init();
void remote_json(ArduinoJson::JsonObject target, uint32_t after, bool include_history, bool include_servo_diagnostics = false);
bool remote_arm(bool enabled);
bool remote_enabled();
esp_err_t remote_set_tracking(bool enabled);
bool remote_tracking_requested();
void remote_tracking_json(ArduinoJson::JsonObject target);
esp_err_t remote_step_yaw(int delta_tenths, uint32_t* request_seq);
const char* remote_display_state();
// Commit one collector setting, then queue one explicit pitch attempt. ESP_OK
// confirms persistence/acceptance, not measured arrival. Disabled returns
// ESP_ERR_INVALID_STATE without writing; invalid 0.1-degree target returns
// ESP_ERR_INVALID_ARG. NVS errors propagate without queuing a new movement.
esp_err_t remote_set_pitch(int angle_tenths, uint32_t* request_seq);
void remote_pitch_setting_json(ArduinoJson::JsonObject target);
}
