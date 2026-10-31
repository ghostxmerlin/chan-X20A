/* SPDX-License-Identifier: MIT */
#include "screen.h"
#include "laughing_man.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#ifdef ESP_PLATFORM
#include <esp_heap_caps.h>
#include <esp_log.h>
#endif

LV_FONT_DECLARE(collector_mono_15);

namespace collector {
namespace {
// Phosphor palette: ink, chrome, rule, muted text, signal.
constexpr uint32_t kInk = 0x000000, kChrome = 0x0C1C12, kRule = 0x244C32;
constexpr uint32_t kMuted = 0x72A980, kSignal = 0x79FF97;
lv_obj_t *home, *terminal, *body, *footer, *portrait;
uint16_t* animation_pixels = nullptr;
lv_image_dsc_t animation_image;
uint32_t animation_frame = 0;
bool showing_status = false, pressed = false;
uint32_t pressed_at = 0, last_release = 0;
uint32_t animation_last_ms = 0, animation_render_ms = 0, animation_phase_ms = 0;
bool animation_clock_ready = false;
constexpr uint32_t kRotationMs = 12000, kAnimationFrameMs = kRotationMs / COLLECTOR_FACE_FRAME_COUNT;

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h, uint32_t color)
{
    auto obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    return obj;
}
lv_obj_t* label(lv_obj_t* parent, int x, int y, const char* text, uint32_t color)
{
    auto obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_text_font(obj, &collector_mono_15, 0);
    lv_label_set_text(obj, text);
    return obj;
}
}
void screen_create(lv_obj_t* parent)
{
    lv_obj_set_style_bg_color(parent, lv_color_hex(kInk), 0);
    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);
    home = box(parent, 0, 0, 320, 240, kInk);
    portrait = lv_image_create(home);
    if (!animation_pixels) {
#ifdef ESP_PLATFORM
        animation_pixels = static_cast<uint16_t*>(heap_caps_malloc(collector_laughing_man.data_size,
                                                                   MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!animation_pixels) ESP_LOGE("CollectorScreen", "Animation buffer unavailable; keeping static home");
#else
        animation_pixels = static_cast<uint16_t*>(std::malloc(collector_laughing_man.data_size));
#endif
    }
    if (animation_pixels) {
        std::memcpy(animation_pixels, collector_laughing_man.data, collector_laughing_man.data_size);
        animation_image = collector_laughing_man;
        animation_image.data = reinterpret_cast<const uint8_t*>(animation_pixels);
        lv_image_set_src(portrait, &animation_image);
    } else lv_image_set_src(portrait, &collector_laughing_man);
    lv_obj_set_pos(portrait, 30, 0);

    terminal = box(parent, 0, 0, 320, 240, kInk);
    box(terminal, 0, 0, 320, 28, kChrome);
    box(terminal, 0, 28, 320, 1, kRule);
    const uint32_t dots[] = {0x477452, 0x72A980, kSignal};
    for (int i = 0; i < 3; ++i) {
        auto dot = box(terminal, 10 + i * 13, 10, 7, 7, dots[i]);
        lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    }
    label(terminal, 76, 6, "chan-X20A / terminal", kMuted);
    body = label(terminal, 12, 38, "", kSignal);
    lv_obj_set_width(body, 296);
    lv_obj_set_style_text_line_space(body, 3, 0);
    box(terminal, 12, 211, 296, 1, kRule);
    footer = label(terminal, 12, 219, "USB: Wi-Fi setup    TAP: face", kMuted);
    lv_obj_add_flag(terminal, LV_OBJ_FLAG_HIDDEN);
    showing_status = false;
    pressed = false;
    last_release = 0;
    animation_clock_ready = false;
    animation_phase_ms = 0;
    animation_frame = 0;
}
void screen_tick(uint32_t now_ms)
{
    if (!portrait || !animation_pixels) return;
    if (!animation_clock_ready) {
        animation_last_ms = animation_render_ms = now_ms;
        animation_clock_ready = true;
    }
    const uint32_t elapsed = now_ms - animation_last_ms;
    animation_last_ms = now_ms;
    if (showing_status) return;
    animation_phase_ms = (animation_phase_ms + elapsed % kRotationMs) % kRotationMs;
    if (now_ms - animation_render_ms < kAnimationFrameMs) return;
    animation_render_ms = now_ms;
    const uint32_t frame = animation_phase_ms * COLLECTOR_FACE_FRAME_COUNT / kRotationMs;
    if (frame == animation_frame) return;
    animation_frame = frame;
    const uint8_t* pixels = collector_laughing_man_frames + frame * COLLECTOR_FACE_CHANGED_PIXELS;
    const auto* offsets = collector_laughing_man_offsets;
    const auto* palette = collector_laughing_man_palette;
    for (size_t i = 0; i < COLLECTOR_FACE_CHANGED_PIXELS; ++i)
        animation_pixels[offsets[i]] = palette[pixels[i]];
    // The S3 only copies an opaque image. Real-time rotated alpha layers made
    // the higher-priority LVGL task starve the camera encoder on core 1.
    lv_area_t bounds;
    lv_obj_get_coords(portrait, &bounds);
    for (size_t i = 0; i < COLLECTOR_FACE_REGION_COUNT; ++i) {
        auto area = collector_laughing_man_regions[i];
        area.x1 += bounds.x1; area.x2 += bounds.x1;
        area.y1 += bounds.y1; area.y2 += bounds.y1;
        lv_obj_invalidate_area(portrait, &area);
    }
}
void screen_update(const ScreenStatus& s)
{
    if (!body) return;
    char text[400];
    snprintf(text, sizeof(text),
             "> %s\n"
             "CAM #%lu  MIC %s\n"
             "CH %u / ID 1  MOTOR %s\n"
             "HTTP %s%s\n"
             "AUDIO :8081  %s\n"
             "PAIRING TOKEN / LOCAL HTTP\n"
             "%.16s\n%.16s",
             s.version, static_cast<unsigned long>(s.frame), s.audio_ready ? "ON" : "WAIT",
             s.channel, s.motor, s.ip[0] ? s.ip : "waiting for Wi-Fi", s.ip[0] ? ":8080" : "",
             s.audio_streaming ? "LIVE" : "READY", s.token, s.token + 16);
    lv_label_set_text(body, text);
}
void screen_touch(bool down, uint32_t now_ms)
{
    if (!home) return;
    if (down && !pressed) pressed_at = now_ms;
    if (!down && pressed && now_ms - pressed_at >= 30 && now_ms - pressed_at <= 700 &&
        now_ms - last_release >= 180) {
        showing_status = !showing_status;
        lv_obj_update_flag(home, LV_OBJ_FLAG_HIDDEN, showing_status);
        lv_obj_update_flag(terminal, LV_OBJ_FLAG_HIDDEN, !showing_status);
        last_release = now_ms;
        animation_last_ms = animation_render_ms = now_ms;
    }
    pressed = down;
}
bool screen_showing_status() { return showing_status; }
}
