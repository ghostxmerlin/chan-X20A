/* SPDX-License-Identifier: MIT */
#include "screen.h"
#include "laughing_man.h"
#include "remote_gestures.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <algorithm>
// Keep the renderer host-testable without importing the board's IDF headers.
namespace hal_bridge { void board_set_display_backlight_enabled(bool enabled); }
#ifdef ESP_PLATFORM
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#endif

LV_FONT_DECLARE(collector_mono_15);
LV_FONT_DECLARE(collector_emotion_14);

namespace collector {
namespace {
// Phosphor palette: ink, chrome, rule, muted text, signal.
constexpr uint32_t kInk = 0x000000, kChrome = 0x0C1C12, kRule = 0x244C32;
constexpr uint32_t kMuted = 0x72A980, kSignal = 0x79FF97;
lv_obj_t *home, *summary, *terminal, *log_viewport, *log_body, *portrait, *reaction_overlay;
lv_obj_t *summary_title, *summary_rows[10], *pairing, *pairing_body;
lv_obj_t* emotion_label = nullptr;
lv_obj_t *faces_page = nullptr, *faces_status = nullptr, *face_boxes[kScreenMaxFaces] = {};
lv_obj_t* screen_root = nullptr;
lv_display_t* screen_display = nullptr;
bool enabled = true, status_pending = false;
ScreenStatus pending_status;
char pending_version[64], pending_ip[64], pending_token[65], pending_motor[64];
enum class IconKind { Wifi, Camera, Remote, Battery, Axes, Yaw, Pitch, Mic };
struct Icon { lv_obj_t* obj = nullptr; IconKind kind; bool valid = false; int value = 0; };
Icon icons[8];
enum class Page { Home, Summary, Log, Pairing, Faces };
Page page = Page::Home;
ScreenReaction reaction = ScreenReaction::None;
uint32_t reaction_at = 0;
char log_text[4096] = {};
uint32_t log_sequence = 0;
bool log_dirty = true;
int touch_start_x = -1, touch_start_y = -1, touch_last_x = -1, touch_last_y = -1;
int touch_max_dx = 0, touch_max_dy = 0;
uint16_t* animation_pixels = nullptr;
lv_image_dsc_t animation_image;
uint32_t animation_frame = 0;
bool pressed = false, touch_cancelled = false, long_press_fired = false;
uint32_t pressed_at = 0, last_release = 0;
uint32_t animation_last_ms = 0, animation_render_ms = 0, animation_phase_ms = 0;
bool animation_clock_ready = false;
constexpr uint32_t kRotationMs = 12000, kAnimationFrameMs = kRotationMs / COLLECTOR_FACE_FRAME_COUNT;
constexpr int kReactionX = 76, kReactionY = 43, kReactionWidth = 168, kReactionHeight = 154;
uint16_t* reaction_pixels = nullptr;
lv_image_dsc_t reaction_images[2] = {};
constexpr lv_point_t kHeart[] = {
    {160,83},{150,67},{136,56},{120,50},{104,51},{90,59},
    {80,72},{76,88},{79,105},{87,122},{102,139},{120,157},
    {140,176},{160,194},{180,176},{200,157},{218,139},{233,122},
    {241,105},{244,88},{240,72},{230,59},{216,51},{200,50},
    {184,56},{170,67}
};
constexpr lv_point_t kCross[] = {
    {104,43},{160,99},{216,43},{237,64},{181,120},{237,176},
    {216,197},{160,141},{104,197},{83,176},{139,120},{83,64}
};

#ifdef ESP_PLATFORM
// Measures refresh submission, not the final DMA completion or physical light.
const char* transition_phase = nullptr;
int64_t transition_started_us = 0, render_started_us = 0, reaction_submitted_us = 0;
void mark_transition(const char* phase)
{
    transition_phase = phase;
    transition_started_us = esp_timer_get_time();
}
void cancel_transition() { transition_phase = nullptr; reaction_submitted_us = 0; }
void reaction_refresh(lv_event_t* event)
{
    const auto now = esp_timer_get_time();
    if (lv_event_get_code(event) == LV_EVENT_RENDER_START) { render_started_us = now; return; }
    if (!transition_phase) return;
    const bool showing = reaction != ScreenReaction::None;
    const auto hold = !showing && reaction_submitted_us ? now - reaction_submitted_us : 0;
    // This build uses newlib nano formatting, which does not support %lld.
    const auto log_us = [](int64_t value) {
        return static_cast<unsigned long>(std::clamp<int64_t>(value, 0, UINT32_MAX));
    };
    ESP_LOGI("CollectorScreen", "%s cached=%d refresh_us=%lu submit_us=%lu hold_us=%lu",
             transition_phase, reaction_pixels != nullptr,
             log_us(now - render_started_us), log_us(now - transition_started_us), log_us(hold));
    reaction_submitted_us = showing ? now : 0;
    transition_phase = nullptr;
}
#else
void mark_transition(const char*) {}
void cancel_transition() {}
#endif

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

void set_text(lv_obj_t* obj, const char* text)
{
    if (std::strcmp(lv_label_get_text(obj), text) != 0) lv_label_set_text(obj, text);
}
void render_log()
{
    if (!log_dirty || !log_body) return;
    set_text(log_body, log_text[0] ? log_text : "Waiting for system log...");
    lv_obj_update_layout(log_viewport);
    lv_obj_scroll_to_y(log_viewport, LV_COORD_MAX, LV_ANIM_OFF);
    log_dirty = false;
}
void show_page(Page target, uint32_t now_ms)
{
    page = target;
    lv_obj_update_flag(home, LV_OBJ_FLAG_HIDDEN, page != Page::Home);
    lv_obj_update_flag(summary, LV_OBJ_FLAG_HIDDEN, page != Page::Summary);
    lv_obj_update_flag(terminal, LV_OBJ_FLAG_HIDDEN, page != Page::Log);
    lv_obj_update_flag(pairing, LV_OBJ_FLAG_HIDDEN, page != Page::Pairing);
    lv_obj_update_flag(faces_page, LV_OBJ_FLAG_HIDDEN, page != Page::Faces);
    if (page != Page::Home) {
        reaction = ScreenReaction::None;
        cancel_transition();
        lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
    }
    if (page == Page::Log) render_log();
    animation_last_ms = animation_render_ms = now_ms;
}
void draw_stroke(lv_layer_t* layer, const lv_area_t& area, const lv_point_t* points,
                 size_t count, int width, uint32_t color)
{
    lv_draw_line_dsc_t line;
    lv_draw_line_dsc_init(&line);
    line.color = lv_color_hex(color);
    line.width = width;
    line.round_start = line.round_end = true;
    for (size_t i = 1; i < count; ++i) {
        line.p1 = {area.x1 + points[i - 1].x, area.y1 + points[i - 1].y};
        line.p2 = {area.x1 + points[i].x, area.y1 + points[i].y};
        lv_draw_line(layer, &line);
    }
}
void draw_rect(lv_layer_t* layer, const lv_area_t& origin, int x, int y, int w, int h,
               uint32_t color, int radius = 0, bool filled = true, int border = 2, lv_opa_t opacity = LV_OPA_COVER)
{
    lv_draw_rect_dsc_t rect;
    lv_draw_rect_dsc_init(&rect);
    rect.radius = radius;
    rect.bg_color = lv_color_hex(color);
    rect.bg_opa = filled ? opacity : static_cast<lv_opa_t>(LV_OPA_TRANSP);
    rect.border_color = lv_color_hex(color);
    rect.border_width = filled ? 0 : border;
    const lv_area_t area = {origin.x1 + x, origin.y1 + y, origin.x1 + x + w - 1, origin.y1 + y + h - 1};
    lv_draw_rect(layer, &rect, &area);
}
// Scanline vector fill keeps each interior pixel fully opaque. Tessellated
// LVGL triangles antialias their internal diagonals and leave visible seams.
// Edge pixels get coverage alpha, but every row interior is a single rectangle.
template<typename PaintSpan>
void polygon_spans(const lv_point_t* points, size_t count, PaintSpan paint)
{
    if (count < 3 || count > 48) return;
    int first_y = points[0].y, last_y = points[0].y;
    for (size_t i = 1; i < count; ++i) {
        first_y = std::min(first_y, static_cast<int>(points[i].y));
        last_y = std::max(last_y, static_cast<int>(points[i].y));
    }
    for (int y = first_y; y < last_y; ++y) {
        float intersections[48];
        size_t n = 0;
        const float scan_y = y + 0.5f;
        for (size_t i = 0; i < count; ++i) {
            const auto& a = points[i];
            const auto& b = points[(i + 1) % count];
            if ((a.y <= scan_y && b.y > scan_y) || (b.y <= scan_y && a.y > scan_y))
                intersections[n++] = a.x + (scan_y - a.y) * (b.x - a.x) / (b.y - a.y);
        }
        std::sort(intersections, intersections + n);
        for (size_t i = 0; i + 1 < n; i += 2) {
            const float left = intersections[i], right = intersections[i + 1];
            const int left_pixel = static_cast<int>(std::floor(left));
            const int right_pixel = static_cast<int>(std::floor(right));
            if (left_pixel == right_pixel) {
                paint(left_pixel, y, 1, static_cast<lv_opa_t>((right - left) * 255));
                continue;
            }
            const int start = static_cast<int>(std::ceil(left));
            if (right_pixel > start) paint(start, y, right_pixel - start, LV_OPA_COVER);
            if (start > left_pixel)
                paint(left_pixel, y, 1, static_cast<lv_opa_t>((start - left) * 255));
            if (right > right_pixel)
                paint(right_pixel, y, 1, static_cast<lv_opa_t>((right - right_pixel) * 255));
        }
    }
}
void draw_polygon(lv_layer_t* layer, const lv_area_t& area, const lv_point_t* points,
                  size_t count, uint32_t color)
{
    polygon_spans(points, count, [&](int x, int y, int width, lv_opa_t opacity) {
        draw_rect(layer, area, x, y, width, 1, color, 0, true, 0, opacity);
    });
}
void prepare_reactions()
{
    if (reaction_pixels) return;
    constexpr size_t pixels = kReactionWidth * kReactionHeight;
#ifdef ESP_PLATFORM
    reaction_pixels = static_cast<uint16_t*>(heap_caps_calloc(2 * pixels, sizeof(uint16_t),
                                                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!reaction_pixels) ESP_LOGE("CollectorScreen", "Reaction cache unavailable; using vector fallback");
#else
    reaction_pixels = static_cast<uint16_t*>(std::calloc(2 * pixels, sizeof(uint16_t)));
#endif
    if (!reaction_pixels) return;
    for (unsigned i = 0; i < 2; ++i) {
        auto* data = reaction_pixels + i * pixels;
        auto& image = reaction_images[i];
        image.header.magic = LV_IMAGE_HEADER_MAGIC;
        image.header.cf = LV_COLOR_FORMAT_RGB565;
        image.header.w = kReactionWidth; image.header.h = kReactionHeight;
        image.header.stride = kReactionWidth * sizeof(uint16_t);
        image.data_size = pixels * sizeof(uint16_t);
        image.data = reinterpret_cast<const uint8_t*>(data);
        const auto* points = i == 0 ? kHeart : kCross;
        const size_t count = i == 0 ? sizeof(kHeart) / sizeof(kHeart[0]) : sizeof(kCross) / sizeof(kCross[0]);
        polygon_spans(points, count, [&](int x, int y, int width, lv_opa_t opacity) {
            // Bake edge coverage against the opaque black background once.
            const auto color = lv_color_make(((kSignal >> 16) & 255) * opacity / 255,
                                            ((kSignal >> 8) & 255) * opacity / 255,
                                            (kSignal & 255) * opacity / 255);
            if (y < kReactionY || y >= kReactionY + kReactionHeight) return;
            const int begin = std::max(x, kReactionX), end = std::min(x + width, kReactionX + kReactionWidth);
            if (begin < end) std::fill(data + (y - kReactionY) * kReactionWidth + begin - kReactionX,
                                      data + (y - kReactionY) * kReactionWidth + end - kReactionX,
                                      lv_color_to_u16(color));
        });
    }
}
void reaction_draw(lv_event_t* event)
{
    auto* layer = lv_event_get_layer(event);
    lv_area_t area;
    lv_obj_get_coords(reaction_overlay, &area);
    area.x1 -= 30; // Glyph coordinates are relative to the whole 320px home.
    if (reaction_pixels && reaction != ScreenReaction::None) {
        lv_draw_image_dsc_t image;
        lv_draw_image_dsc_init(&image);
        image.src = &reaction_images[reaction == ScreenReaction::Heart ? 0 : 1];
        const lv_area_t bounds = {area.x1 + kReactionX, area.y1 + kReactionY,
                                 area.x1 + kReactionX + kReactionWidth - 1,
                                 area.y1 + kReactionY + kReactionHeight - 1};
        lv_draw_image(layer, &image, &bounds);
        return;
    }
    if (reaction == ScreenReaction::Heart) {
        draw_polygon(layer, area, kHeart, sizeof(kHeart) / sizeof(kHeart[0]), kSignal);
    } else if (reaction == ScreenReaction::Reject) {
        draw_polygon(layer, area, kCross, sizeof(kCross) / sizeof(kCross[0]), kSignal);
    }
}
void icon_draw(lv_event_t* event)
{
    auto& icon = *static_cast<Icon*>(lv_event_get_user_data(event));
    auto* layer = lv_event_get_layer(event);
    lv_area_t area;
    lv_obj_get_coords(icon.obj, &area);
    const uint32_t color = icon.valid ? kSignal : kMuted;
    switch (icon.kind) {
        case IconKind::Wifi: {
            static const lv_point_t outer[] = {{1,9},{6,5},{12,3},{18,3},{24,5},{29,9}};
            static const lv_point_t inner[] = {{6,15},{10,12},{15,11},{20,12},{24,15}};
            static const lv_point_t center[] = {{11,20},{15,18},{19,20}};
            draw_stroke(layer, area, outer, 6, 3, color);
            draw_stroke(layer, area, inner, 5, 3, color);
            draw_stroke(layer, area, center, 3, 3, color);
            draw_rect(layer, area, 13, 24, 5, 5, color, LV_RADIUS_CIRCLE);
            break;
        }
        case IconKind::Camera: {
            draw_rect(layer, area, 1, 7, 22, 19, color, 3, false);
            draw_rect(layer, area, 6, 3, 11, 5, color, 1);
            draw_rect(layer, area, 7, 12, 10, 10, color, LV_RADIUS_CIRCLE, false);
            static const lv_point_t finder[] = {{24,12},{30,8},{30,25},{24,21}};
            draw_polygon(layer, area, finder, 4, color);
            break;
        }
        case IconKind::Remote: {
            draw_rect(layer, area, 1, 8, 29, 20, color, 5, false);
            static const lv_point_t horizontal[] = {{6,17},{14,17}};
            static const lv_point_t vertical[] = {{10,13},{10,21}};
            draw_stroke(layer, area, horizontal, 2, 2, color);
            draw_stroke(layer, area, vertical, 2, 2, color);
            draw_rect(layer, area, 21, 13, 4, 4, color, LV_RADIUS_CIRCLE);
            draw_rect(layer, area, 24, 20, 4, 4, color, LV_RADIUS_CIRCLE);
            draw_rect(layer, area, 14, 1, 3, 5, color, 1);
            break;
        }
        case IconKind::Battery: {
            draw_rect(layer, area, 1, 8, 35, 18, color, 3, false);
            draw_rect(layer, area, 36, 13, 3, 8, color, 1);
            if (icon.valid) {
                const int fill = std::clamp(icon.value & 0xff, 0, 100) * 29 / 100;
                if (fill) draw_rect(layer, area, 4, 11, fill, 12, kSignal, 1);
                if (icon.value & 0x100) {
                    static const lv_point_t bolt[] = {{49,3},{41,17},{46,17},{43,28},{53,12},{48,12}};
                    draw_polygon(layer, area, bolt, 6, kSignal);
                }
            }
            break;
        }
        case IconKind::Axes: {
            static const lv_point_t x[] = {{10,20},{24,13},{20,13},{24,13},{22,17}};
            static const lv_point_t y[] = {{10,20},{10,3},{7,7},{10,3},{13,7}};
            static const lv_point_t z[] = {{10,20},{2,28},{2,23},{2,28},{7,28}};
            draw_stroke(layer, area, x, 5, 2, color);
            draw_stroke(layer, area, y, 5, 2, color);
            draw_stroke(layer, area, z, 5, 2, color);
            break;
        }
        case IconKind::Yaw:
        case IconKind::Pitch: {
            static const lv_point_t arc[] = {{2,21},{4,14},{9,9},{16,7},{23,9},{28,14},{30,21}};
            draw_stroke(layer, area, arc, 7, 2, color);
            if (icon.valid) {
                const float normalized = icon.kind == IconKind::Yaw ? icon.value / 1280.0f : (icon.value - 450) / 400.0f;
                const float angle = std::clamp(normalized, -1.0f, 1.0f) * 1.15f;
                const lv_point_t needle[] = {{16,21},{16 + static_cast<int>(12 * std::sin(angle)),
                                                       21 - static_cast<int>(12 * std::cos(angle))}};
                draw_stroke(layer, area, needle, 2, 2, kSignal);
                draw_rect(layer, area, 14, 19, 4, 4, kSignal, LV_RADIUS_CIRCLE);
            }
            break;
        }
        case IconKind::Mic: {
            draw_rect(layer, area, 5, 1, 7, 11, color, 4, false);
            static const lv_point_t stand[] = {{2,9},{2,12},{5,15},{11,15},{14,12},{14,9}};
            static const lv_point_t stem[] = {{8,15},{8,19}};
            draw_stroke(layer, area, stand, 6, 1, color);
            draw_stroke(layer, area, stem, 2, 1, color);
            break;
        }
    }
}
void create_icon(IconKind kind, int x, int y, int width, int height)
{
    auto& icon = icons[static_cast<unsigned>(kind)];
    icon = {box(summary, x, y, width, height, kInk), kind, false, 0};
    lv_obj_add_event_cb(icon.obj, icon_draw, LV_EVENT_DRAW_POST, &icon);
}
void update_icon(IconKind kind, bool valid, int value = 0)
{
    auto& icon = icons[static_cast<unsigned>(kind)];
    if (icon.valid == valid && icon.value == value) return;
    icon.valid = valid; icon.value = value;
    lv_obj_invalidate(icon.obj);
}

}
void screen_create(lv_obj_t* parent)
{
    screen_root = parent;
    screen_display = lv_obj_get_display(parent);
    enabled = true;
    cancel_transition();
    status_pending = false;
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

    // Rasterize once, then copy an opaque image on show. Limit both transition
    // invalidations to the portrait; the outer black margins never change.
    prepare_reactions();
    reaction_overlay = box(home, 30, 0, 260, 240, kInk);
    lv_obj_add_event_cb(reaction_overlay, reaction_draw, LV_EVENT_DRAW_POST, nullptr);
    lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
    // Small opaque corner badge, above both the portrait and reaction layer.
    auto emotion_box = box(home, 2, 220, 102, 20, kInk);
    emotion_label = label(emotion_box, 2, 2, "情绪：未连接", kMuted);
    lv_obj_set_style_text_font(emotion_label, &collector_emotion_14, 0);
#ifdef ESP_PLATFORM
    lv_display_add_event_cb(screen_display, reaction_refresh, LV_EVENT_RENDER_START, nullptr);
    lv_display_add_event_cb(screen_display, reaction_refresh, LV_EVENT_RENDER_READY, nullptr);
#endif

    summary = box(parent, 0, 0, 320, 240, kInk);
    summary_title = label(summary, 12, 7, "chan-X20A", kSignal);
    box(summary, 12, 28, 296, 1, kRule);
    const int row_x[] = {50, 210, 50, 232, 12, 42, 42, 47, 207, 12};
    const int row_y[] = {41, 41, 77, 77, 106, 148, 169, 194, 194, 219};
    const int row_w[] = {110, 98, 112, 75, 188, 214, 214, 112, 106, 296};
    for (int i = 0; i < 10; ++i) {
        summary_rows[i] = label(summary, row_x[i], row_y[i], "", i == 4 || i == 9 ? kMuted : kSignal);
        lv_obj_set_width(summary_rows[i], row_w[i]);
        lv_label_set_long_mode(summary_rows[i], LV_LABEL_LONG_CLIP);
    }
    create_icon(IconKind::Wifi, 12, 34, 32, 32);
    create_icon(IconKind::Camera, 172, 34, 32, 32);
    create_icon(IconKind::Remote, 12, 70, 32, 32);
    create_icon(IconKind::Battery, 172, 70, 56, 32);
    create_icon(IconKind::Axes, 12, 150, 28, 34);
    create_icon(IconKind::Yaw, 10, 187, 32, 25);
    create_icon(IconKind::Pitch, 170, 187, 32, 25);
    create_icon(IconKind::Mic, 207, 104, 18, 22);
    label(summary, 69, 128, "X      Y      Z", kMuted);
    label(summary, 264, 148, "m/s2", kMuted);
    label(summary, 273, 169, "d/s", kMuted);
    // Mic status shares the quiet network line, leaving sensor data readable.
    auto mic_text = label(summary, 234, 106, "--", kMuted);
    lv_obj_set_width(mic_text, 74);
    lv_obj_set_user_data(icons[static_cast<unsigned>(IconKind::Mic)].obj, mic_text);
    box(summary, 12, 125, 296, 1, kRule);
    box(summary, 12, 212, 296, 1, kRule);
    set_text(summary_rows[9], "Tap:face  <log  Hold:pair");

    terminal = box(parent, 0, 0, 320, 240, kInk);
    box(terminal, 0, 0, 320, 28, kChrome);
    box(terminal, 0, 28, 320, 1, kRule);
    label(terminal, 12, 6, "syslog", kSignal);
    label(terminal, 192, 6, "live / tail", kMuted);
    log_viewport = box(terminal, 12, 34, 296, 177, kInk);
    // Programmatic follow-tail scrolling only: raw touch gestures are handled
    // below so a horizontal navigation swipe can never become an accidental tap.
    lv_obj_set_scrollbar_mode(log_viewport, LV_SCROLLBAR_MODE_OFF);
    log_body = label(log_viewport, 0, 0, "Waiting for system log...", kSignal);
    lv_obj_set_width(log_body, 296);
    lv_label_set_long_mode(log_body, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(log_body, 3, 0);
    box(terminal, 12, 212, 296, 1, kRule);
    label(terminal, 12, 219, "Tap: face    swipe >: summary", kMuted);
    pairing = box(parent, 0, 0, 320, 240, kInk);
    label(pairing, 12, 7, "chan-X20A / pairing", kSignal);
    box(pairing, 12, 28, 296, 1, kRule);
    pairing_body = label(pairing, 12, 39, "", kSignal);
    lv_obj_set_width(pairing_body, 296);
    lv_obj_set_style_text_line_space(pairing_body, 6, 0);
    box(pairing, 12, 212, 296, 1, kRule);
    label(pairing, 12, 219, "Tap: summary", kMuted);
    lv_obj_add_flag(pairing, LV_OBJ_FLAG_HIDDEN);

    // M12 face view: the M12's 640x480 frame scaled to the whole screen, boxes only.
    faces_page = box(parent, 0, 0, 320, 240, kInk);
    auto frame = lv_obj_create(faces_page);
    lv_obj_remove_style_all(frame);
    lv_obj_set_pos(frame, 0, 0);
    lv_obj_set_size(frame, 320, 240);
    lv_obj_set_style_border_color(frame, lv_color_hex(kRule), 0);
    lv_obj_set_style_border_width(frame, 1, 0);
    lv_obj_remove_flag(frame, LV_OBJ_FLAG_SCROLLABLE);
    label(faces_page, 8, 4, "M12 face", kMuted);
    for (auto& face : face_boxes) {
        face = lv_obj_create(faces_page);
        lv_obj_remove_style_all(face);
        lv_obj_set_style_border_color(face, lv_color_hex(kSignal), 0);
        lv_obj_set_style_border_width(face, 3, 0);
        lv_obj_set_style_radius(face, 6, 0);
        lv_obj_remove_flag(face, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(face, LV_OBJ_FLAG_HIDDEN);
    }
    faces_status = label(faces_page, 8, 219, "starting M12...", kSignal);
    lv_obj_set_width(faces_status, 304);
    lv_label_set_long_mode(faces_status, LV_LABEL_LONG_CLIP);
    lv_obj_add_flag(faces_page, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(summary, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(terminal, LV_OBJ_FLAG_HIDDEN);
    page = Page::Home;
    reaction = ScreenReaction::None;
    log_dirty = true;
    pressed = touch_cancelled = long_press_fired = false;
    last_release = 0;
    animation_clock_ready = false;
    animation_phase_ms = 0;
    animation_frame = 0;
}
void screen_set_enabled(bool value, uint32_t now_ms)
{
    if (!screen_display || enabled == value) return;
    enabled = value;
    // Neither a pending tap nor a finger held across the transition may
    // navigate. A fresh release is required before accepting another touch.
    screen_cancel_touch();
    touch_cancelled = true;
    animation_last_ms = animation_render_ms = now_ms;
    animation_clock_ready = true;
    auto* refresh = lv_display_get_refr_timer(screen_display);
    if (!enabled) {
        // Pausing alone is insufficient: LV_EVENT_REFR_REQUEST resumes this
        // timer on invalidation. Pair the pause with the display's own gate.
        lv_display_enable_invalidation(screen_display, false);
        if (refresh) lv_timer_pause(refresh);
        reaction = ScreenReaction::None;
        cancel_transition();
        lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
        hal_bridge::board_set_display_backlight_enabled(false);
        return;
    }
    lv_display_enable_invalidation(screen_display, true);
    if (status_pending) screen_update(pending_status);
    if (page == Page::Log) render_log();
    lv_obj_invalidate(screen_root);
    if (refresh) {
        lv_timer_resume(refresh);
        lv_timer_ready(refresh);
    }
    hal_bridge::board_set_display_backlight_enabled(true);
}
bool screen_enabled() { return enabled; }
void screen_tick(uint32_t now_ms)
{
    if (!enabled) return;
    if (reaction != ScreenReaction::None && now_ms - reaction_at >= kReactionDurationMs) {
        reaction = ScreenReaction::None;
        mark_transition("hide");
        lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
    }
    if (!portrait || !animation_pixels) return;
    if (!animation_clock_ready) {
        animation_last_ms = animation_render_ms = now_ms;
        animation_clock_ready = true;
    }
    const uint32_t elapsed = now_ms - animation_last_ms;
    animation_last_ms = now_ms;
    if (page != Page::Home || reaction != ScreenReaction::None) return;
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
    if (!summary_title) return;
    if (!enabled) {
        pending_status = s;
        // Callers can supply temporary strings, so the off-screen snapshot
        // owns its text as well as its numeric/sensor values.
        std::snprintf(pending_version, sizeof(pending_version), "%s", s.version ? s.version : "");
        std::snprintf(pending_ip, sizeof(pending_ip), "%s", s.ip ? s.ip : "");
        std::snprintf(pending_token, sizeof(pending_token), "%s", s.token ? s.token : "");
        std::snprintf(pending_motor, sizeof(pending_motor), "%s", s.motor ? s.motor : "");
        pending_status.version = pending_version;
        pending_status.ip = pending_ip;
        pending_status.token = pending_token;
        pending_status.motor = pending_motor;
        status_pending = true;
        return;
    }
    status_pending = false;
    char text[160];
    set_text(emotion_label, emotion_caption(s.emotion));
    const char* version = s.version ? s.version : "";
    if (std::strncmp(version, "collector-", 10) == 0) version += 10;
    std::snprintf(text, sizeof(text), "chan-X20A             %.8s", version);
    set_text(summary_title, text);
    set_text(summary_rows[0], s.wifi_connected ? "online" : "offline");
    if (s.camera_working)
        std::snprintf(text, sizeof(text), "%4.1f fps", std::isfinite(s.camera_fps) ? s.camera_fps : 0);
    else std::snprintf(text, sizeof(text), "-- fps");
    set_text(summary_rows[1], text);
    const char* remote = !s.remote_ready ? "waiting" : s.remote_recent ? "recent" :
                         s.remote_last_age_ms < 0 ? "no input" : "idle";
    set_text(summary_rows[2], remote);
    if (s.power_valid) std::snprintf(text, sizeof(text), "%d%%", s.battery_percent);
    else std::snprintf(text, sizeof(text), "--");
    set_text(summary_rows[3], text);
    std::snprintf(text, sizeof(text), "IP %s", s.wifi_connected && s.ip && s.ip[0] ? s.ip : "--");
    set_text(summary_rows[4], text);
    auto mic_text = static_cast<lv_obj_t*>(lv_obj_get_user_data(icons[static_cast<unsigned>(IconKind::Mic)].obj));
    set_text(mic_text, s.audio_streaming ? "live" : s.audio_ready ? "ready" : "--");
    if (s.imu_valid) {
        std::snprintf(text, sizeof(text), "%+6.1f %+6.1f %+6.1f", s.accel[0], s.accel[1], s.accel[2]);
        set_text(summary_rows[5], text);
        std::snprintf(text, sizeof(text), "%+6.1f %+6.1f %+6.1f", s.gyro[0], s.gyro[1], s.gyro[2]);
    } else {
        set_text(summary_rows[5], "    --     --     --");
        std::snprintf(text, sizeof(text), "    --     --     --");
    }
    set_text(summary_rows[6], text);
    const bool yaw_valid = s.yaw_valid && std::isfinite(s.yaw_deg);
    const bool pitch_valid = s.pitch_valid && std::isfinite(s.pitch_deg);
    if (yaw_valid) std::snprintf(text, sizeof(text), "Yaw %+.1f", s.yaw_deg);
    else std::snprintf(text, sizeof(text), "Yaw --");
    set_text(summary_rows[7], text);
    if (pitch_valid) std::snprintf(text, sizeof(text), "Pitch %+.1f", s.pitch_deg);
    else std::snprintf(text, sizeof(text), "Pitch --");
    set_text(summary_rows[8], text);
    update_icon(IconKind::Wifi, s.wifi_connected);
    update_icon(IconKind::Camera, s.camera_working);
    update_icon(IconKind::Remote, s.remote_recent);
    update_icon(IconKind::Battery, s.power_valid,
                s.power_valid ? std::clamp(s.battery_percent, 0, 100) | (s.charging ? 0x100 : 0) : 0);
    update_icon(IconKind::Axes, s.imu_valid);
    update_icon(IconKind::Yaw, yaw_valid, yaw_valid ? static_cast<int>(s.yaw_deg * 10) : 0);
    update_icon(IconKind::Pitch, pitch_valid, pitch_valid ? static_cast<int>(s.pitch_deg * 10) : 0);
    update_icon(IconKind::Mic, s.audio_ready);
    const char* token = s.token ? s.token : "";
    const char* token_tail = std::strlen(token) > 16 ? token + 16 : "";
    char details[256];
    std::snprintf(details, sizeof(details), "%s\n%s%s%s\n\nPairing token\n%.16s\n%.16s\nUSB: Wi-Fi setup",
                  s.version ? s.version : "", s.wifi_connected ? "http://" : "",
                  s.wifi_connected && s.ip && s.ip[0] ? s.ip : "waiting for Wi-Fi",
                  s.wifi_connected ? ":8080" : "", token[0] ? token : "unavailable", token_tail);
    set_text(pairing_body, details);
}
void screen_update_log(const char* text, uint32_t sequence)
{
    if (log_sequence == sequence && !log_dirty) return;
    if (!text) text = "";
    // Snapshot input is already bounded by its ring, but keep an explicit limit
    // and prefer its newest tail if a different provider passes a longer one.
    const size_t size = std::strlen(text);
    if (size >= sizeof(log_text)) text += size - (sizeof(log_text) - 1);
    if (std::strcmp(log_text, text) != 0) {
        std::snprintf(log_text, sizeof(log_text), "%s", text);
        log_dirty = true;
    }
    log_sequence = sequence;
    if (enabled && page == Page::Log) render_log();
}
void screen_show_reaction(ScreenReaction value, uint32_t now_ms)
{
    if (!enabled || !reaction_overlay || page != Page::Home) return;
    reaction = value;
    reaction_at = now_ms;
    mark_transition(value == ScreenReaction::None ? "hide" : value == ScreenReaction::Heart ? "heart" : "reject");
    lv_obj_update_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN, value == ScreenReaction::None);
    lv_obj_invalidate(reaction_overlay);
}
void screen_touch(bool down, uint32_t now_ms, int x, int y)
{
    if (!home) return;
    if (!enabled) {
        touch_cancelled = down;
        return;
    }
    if (touch_cancelled) {
        if (!down) touch_cancelled = false;
        return;
    }
    if (down && !pressed) {
        pressed_at = now_ms;
        touch_start_x = touch_last_x = x;
        touch_start_y = touch_last_y = y;
        touch_max_dx = touch_max_dy = 0;
        long_press_fired = false;
    }
    if (pressed && x >= 0 && y >= 0 && touch_start_x >= 0 && touch_start_y >= 0) {
        touch_last_x = x;
        touch_last_y = y;
        touch_max_dx = std::max(touch_max_dx, std::abs(x - touch_start_x));
        touch_max_dy = std::max(touch_max_dy, std::abs(y - touch_start_y));
    }
    if (pressed && !long_press_fired && page == Page::Summary &&
        now_ms - pressed_at >= 900 && touch_max_dx <= 18 && touch_max_dy <= 18) {
        show_page(Page::Pairing, now_ms);
        long_press_fired = true;
        last_release = now_ms;
    }
    if (!down && pressed && !long_press_fired && now_ms - last_release >= 180) {
        const uint32_t held = now_ms - pressed_at;
        const int dx = touch_last_x - touch_start_x, dy = touch_last_y - touch_start_y;
        const bool horizontal = touch_start_x >= 0 && std::abs(dx) >= 55 &&
                                std::abs(dx) > std::abs(dy) * 2 && touch_max_dy <= 55;
        if (held >= 30 && held <= 1000 && horizontal) {
            if (page == Page::Summary && dx < 0) show_page(Page::Log, now_ms);
            else if (page == Page::Log && dx > 0) show_page(Page::Summary, now_ms);
            last_release = now_ms;
        } else if (held >= 30 && held <= 700 && touch_max_dx <= 18 && touch_max_dy <= 18) {
            show_page(page == Page::Home || page == Page::Pairing ? Page::Summary : Page::Home, now_ms);
            last_release = now_ms;
        }
    }
    pressed = down;
}
void screen_cancel_touch()
{
    touch_cancelled = pressed || touch_cancelled;
    pressed = false;
    touch_start_x = touch_start_y = touch_last_x = touch_last_y = -1;
    touch_max_dx = touch_max_dy = 0;
}
void screen_show_faces(bool on, uint32_t now_ms)
{
    if (!faces_page) return;
    if (on) {
        if (page != Page::Faces) {
            screen_update_faces(nullptr, 0, "starting M12...");
            show_page(Page::Faces, now_ms);
        }
    } else if (page == Page::Faces) show_page(Page::Home, now_ms);
}
void screen_update_faces(const ScreenFace* faces, size_t count, const char* status)
{
    if (!faces_page) return;
    if (!faces) count = 0;
    for (size_t i = 0; i < kScreenMaxFaces; ++i) {
        const bool shown = i < count;
        if (shown) {
            lv_obj_set_pos(face_boxes[i], faces[i].x, faces[i].y);
            lv_obj_set_size(face_boxes[i], std::max<int>(faces[i].w, 6), std::max<int>(faces[i].h, 6));
        }
        lv_obj_update_flag(face_boxes[i], LV_OBJ_FLAG_HIDDEN, !shown);
    }
    set_text(faces_status, status ? status : "");
}
bool screen_showing_faces() { return page == Page::Faces; }
bool screen_showing_status() { return page != Page::Home; }
bool screen_showing_log() { return page == Page::Log; }
bool screen_showing_pairing() { return page == Page::Pairing; }
}
