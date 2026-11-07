/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
namespace collector::eyes {
// All view functions are called only by the LVGL owner.
void view_create(lv_obj_t* parent);
bool view_tick(bool home,bool display_on,bool reaction);
}
