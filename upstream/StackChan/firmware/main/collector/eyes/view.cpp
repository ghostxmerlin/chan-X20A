/* SPDX-License-Identifier: MIT */
#include "view.h"
#include "eyes.h"
namespace collector::eyes {
namespace {
lv_obj_t* image=nullptr;
lv_image_dsc_t descriptors[2]{};
bool showing=false;
void refresh(lv_event_t*) { rendered(showing); }
}
void view_create(lv_obj_t* parent) {
    image=lv_image_create(parent);lv_obj_set_pos(image,0,0);
    lv_obj_add_flag(image,LV_OBJ_FLAG_HIDDEN);showing=false;
    lv_display_add_event_cb(lv_obj_get_display(parent),refresh,LV_EVENT_REFR_READY,nullptr);
    for (auto& d:descriptors) {
        d.header.magic=LV_IMAGE_HEADER_MAGIC;d.header.cf=LV_COLOR_FORMAT_RGB565;
        d.header.w=320;d.header.h=240;d.header.stride=640;d.data_size=320*240*2;
    }
}
bool view_tick(bool home,bool on,bool reaction) {
    display_gate(home,on,reaction);
    if (!image) return false;
    if (home && on && !reaction) {
        auto frame=take_frame();
        if (frame.pixels) {
            auto& d=descriptors[frame.slot];d.data=reinterpret_cast<const uint8_t*>(frame.pixels);
            lv_image_set_src(image,&d);
            // A descriptor can be reused after REFR_READY; invalidate even if
            // its address equals a previously cached image source.
            lv_obj_invalidate(image);
        }
    }
    const bool visible=home && on && !reaction && show_bound_frame();
    if (showing!=visible) {showing=visible;lv_obj_update_flag(image,LV_OBJ_FLAG_HIDDEN,!showing);}
    return showing;
}
}
