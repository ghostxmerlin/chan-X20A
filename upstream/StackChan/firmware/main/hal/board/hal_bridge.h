/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "stackchan_camera.h"
#include <cstdint>
#include <lvgl.h>
#include <driver/i2c_master.h>
#include <string_view>

namespace hal_bridge {

struct TouchPoint_t {
    int num = 0;
    int x   = -1;
    int y   = -1;
    bool valid = false;
    int64_t timestampUs = 0;
};

struct Data_t {
    TouchPoint_t touchPoint;
    bool isXiaozhiMode              = false;
    bool isXiaozhiModeToggleEnabled = false;
};

struct XiaozhiConfig_t {
    uint32_t idleShutdownTimeSeconds = 600;
    bool allowShutdownWhenCharging   = false;
    uint8_t idleRandomMovementLevel  = 2;
    bool startAiAgentOnBoot          = false;
};

void lock();
void unlock();
Data_t& get_data();

void set_touch_point(int num, int x, int y);
void invalidate_touch_point();
TouchPoint_t get_touch_point();
void set_collector_mode(bool enabled);
bool is_collector_mode();
bool board_get_power_snapshot(int& level, bool& charging, int64_t& timestamp_us);
// collector-0.5.18: the AXP2101's raw power state (read only) and counters since boot, one sample per power poll.
struct PowerDiag {
    bool valid = false;
    uint8_t status1 = 0, status2 = 0, vindpm_reg = 0, ilim_reg = 0;
    uint16_t bat_mv = 0, vbus_mv = 0, sys_mv = 0;
    uint32_t samples_vbus = 0, discharging_on_vbus = 0, current_limited = 0, vindpm_active = 0;
};
bool board_get_power_diag(PowerDiag& out);
// Collector playback only. State is -1 when the last hardware check failed.
esp_err_t board_set_speaker_reset(bool held);
int board_speaker_reset_state();

bool is_xiaozhi_mode();
void set_xiaozhi_mode(bool mode);
void toggle_xiaozhi_chat_state();

void disply_lvgl_lock();
void disply_lvgl_unlock();
lv_disp_t* display_get_lvgl_display();

void xiaozhi_board_init();
void start_xiaozhi_app();
bool is_xiaozhi_ready();
bool is_xiaozhi_idle();
XiaozhiConfig_t get_xiaozhi_config();
void set_xiaozhi_config(const XiaozhiConfig_t& config);

i2c_master_bus_handle_t board_get_i2c_bus();
StackChanCamera* board_get_camera();
int board_get_battery_level();
bool board_is_battery_charging();
void board_set_backlight_brightness(uint8_t brightness, bool permanent = false);
uint8_t board_get_backlight_brightness();
// Display-only, RAM-only gate; retain the normal backlight brightness target.
void board_set_display_backlight_enabled(bool enabled);
void board_set_speaker_volume(uint8_t volume, bool permanent = false);
uint8_t board_get_speaker_volume();

void app_play_sound(const std::string_view& sound);

}  // namespace hal_bridge
