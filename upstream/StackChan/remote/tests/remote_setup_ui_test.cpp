#include <cassert>
#include <cstdint>
#include <cstdio>
#include "../code/main/ui/ui_setup_screen.h"
extern "C" {
joystick_data_t joystick_data;
static bool gui_locked;
bool lvgl_port_lock() { assert(!gui_locked); gui_locked = true; return true; }
void lvgl_port_unlock() { assert(gui_locked); gui_locked = false; }
bool lv_obj_is_valid(lv_obj_t *obj) { assert(gui_locked); return obj != nullptr; }
#include "../code/main/ui/ui_setup_screen.c"
}
int main()
{
    joystick_data = {};
    joystick_data.channel = 6;
    joystick_data.id = 1;
    joystick_data.screen_mode = MODE_SETUP;
    joystick_data.joyY = Y_CENTER;
    create_setup_screen();
    assert(lv_dropdown_get_selected(channel_dropdown) == 5);
    assert(lv_dropdown_get_selected(id_dropdown) == 1);
    joystick_data.channel = 11;
    joystick_data.id = 7;
    sync_setup_screen(&joystick_data);
    assert(lv_dropdown_get_selected(channel_dropdown) == 10);
    assert(lv_dropdown_get_selected(id_dropdown) == 7);
    joystick_data.joyY = Y_MAX;
    update_setup_screen(&joystick_data);
    assert(joystick_data.channel == 12 && joystick_data.id == 7);
    joystick_data.select_mode = ID_SELECT;
    joystick_data.joyY = Y_MIN;
    update_setup_screen(&joystick_data);
    assert(joystick_data.channel == 12 && joystick_data.id == 6);
    // A delayed setup tick after confirmation must not alter active settings.
    joystick_data.screen_mode = 1;
    update_setup_screen(&joystick_data);
    assert(joystick_data.channel == 12 && joystick_data.id == 6);
    puts("remote setup current selections and late-update suppression: PASS");
}
