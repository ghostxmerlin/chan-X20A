#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "fake_nvs.h"
#include "../code/main/remote_settings.h"
#include "lvgl.h"
#include "../code/main/joystick/joystick_basic.h"

// Replace only the platform/UI boundaries; execute the production button and
// initial-screen dispatch below, including their shared joystick_data state.
#define UI_H
#define __JOYSTICK_HANDLE_H__
#define __ESP_NOW_INIT_H__
#define LVGL_PORT_H
#define MODE_SETUP 0
#define MODE_RUNNING 1
#define MODE_IMU 2
#define CHANNEL_SELECT 0
#define ID_SELECT 1
#define X_CENTER 2180
#define Y_CENTER 1960
extern "C" {
static int setup_obj, running_obj, imu_obj;
lv_obj_t *setup_screen = nullptr, *running_screen = nullptr, *imu_screen = nullptr;
static lv_obj_t *loaded_screen;
static int reinit_count, reinit_channel;
static int shown_channel, shown_id;
static bool gui_locked;
void create_setup_screen() { assert(!gui_locked); setup_screen = &setup_obj; }
void create_running_screen() { assert(!gui_locked); running_screen = &running_obj; }
void update_running_screen(int16_t, int16_t, uint8_t channel, uint8_t id, uint8_t)
{ shown_channel = channel; shown_id = id; }
void create_imu_screen() { assert(!gui_locked); imu_screen = &imu_obj; }
void ui_setup_screen_destory() { setup_screen = nullptr; }
void ui_running_screen_destory() { running_screen = nullptr; }
void ui_imu_screen_destory() { imu_screen = nullptr; }
bool lv_obj_is_valid(lv_obj_t *obj) { assert(gui_locked); return obj != nullptr; }
void lv_disp_load_scr(lv_obj_t *obj) { assert(gui_locked); loaded_screen = obj; }
void lv_scr_load_anim(lv_obj_t *obj, int, int, int, bool) { assert(gui_locked); loaded_screen = obj; }
bool lvgl_port_lock() { assert(!gui_locked); gui_locked = true; return true; }
void lvgl_port_unlock() { assert(gui_locked); gui_locked = false; }
void lvgl_port_init() {}
void sync_setup_screen(const joystick_data_t *) { assert(!gui_locked); }
void joystick_set_mode(joystick_data_t *data, uint8_t mode) { data->screen_mode = mode; }
joystick_data_t joystick_init() { return {}; }
void wifi_espnow_init(uint8_t) {}
int wifi_espnow_reinit(uint8_t channel) { ++reinit_count; reinit_channel = channel; return channel; }
void handle_setup_screen(void *) {}
void handle_running_screen(void *) {}
void handle_imu_screen(void *) {}
void switch_screen(int);
void ui_init();
}
#include "../code/main/StackChan-RemoteControl-ESPNow.cpp"
extern "C" {
#include "../code/main/ui/ui.c"
}

static void press_a() { M5.BtnA.pressed = true; handle_button_press(); }
static void press_b() { M5.BtnB.pressed = true; handle_button_press(); }

int main()
{
    reset_nvs();
    remote_settings_t settings;
    assert(remote_settings_load(&settings) == ESP_ERR_NVS_NOT_FOUND);
    joystick_data = {};
    joystick_data.channel = settings.channel;
    joystick_data.id = settings.id;
    joystick_data.screen_mode = MODE_RUNNING;
    ui_init();
    assert(loaded_screen == running_screen && loaded_screen != nullptr);
    assert(shown_channel == 6 && shown_id == 1);
    assert(blob_writes == 0 && write_opens == 0 && commits == 0);
    press_a();
    assert(joystick_data.screen_mode == MODE_IMU && loaded_screen == imu_screen);
    assert(reinit_count == 0);
    press_a();
    assert(joystick_data.screen_mode == MODE_SETUP && loaded_screen == setup_screen);
    press_b();
    assert(joystick_data.select_mode == ID_SELECT && !joystick_data.btnB_status);
    press_a();
    assert(joystick_data.screen_mode == MODE_RUNNING && loaded_screen == running_screen);
    assert(reinit_count == 1 && reinit_channel == 6);
    assert(blob_writes == 0 && write_opens == 0 && commits == 0);
    press_b();
    assert(joystick_data.btnB_status);
    press_a(); press_b();
    assert(joystick_data.screen_mode == MODE_IMU && !joystick_data.btnB_status);
    press_a();
    joystick_data.channel = 11; joystick_data.id = 7;
    assert(blob_writes == 0);
    press_b();
    assert(blob_writes == 0); // Selection/navigation is not confirmation.
    press_a();
    assert(blob_writes == 1 && commits == 1 && reinit_channel == 11);
    press_a(); press_a();
    write_error = ESP_FAIL;
    joystick_data.channel = 3; joystick_data.id = 2;
    press_a();
    assert(joystick_data.screen_mode == MODE_RUNNING);
    assert(joystick_data.channel == 11 && joystick_data.id == 7 && reinit_channel == 11);
    assert(commits == 1); // Failed writes do not replace the confirmed pair.
    puts("remote boot UI and button sequence: PASS");
}
