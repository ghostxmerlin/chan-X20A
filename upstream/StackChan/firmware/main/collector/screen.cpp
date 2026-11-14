/* SPDX-License-Identifier: MIT */
#include "screen.h"
#include "runtime_log.h"
#include "laughing_man.h"
#include "packed_palette.h"
#include "remote_gestures.h"
#include "home_touch.h"
#include "ui_pages.h"
#include "eyes/view.h"
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

namespace collector {
namespace {
// Phosphor palette: ink, chrome, rule, muted text, signal.
constexpr uint32_t kInk = 0x000000, kChrome = 0x0C1C12, kRule = 0x244C32;
constexpr uint32_t kMuted = 0x72A980, kSignal = 0x79FF97;
lv_obj_t *home, *summary, *terminal, *log_viewport, *log_body, *portrait, *reaction_overlay;
lv_obj_t *summary_title, *summary_rows[10], *pairing, *pairing_body;
lv_obj_t *faces_page = nullptr, *faces_status = nullptr, *face_boxes[kScreenMaxFaces] = {};
// Wi-Fi setup pages. Row/key geometry is shared by drawing and hit testing.
lv_obj_t *wifi_page = nullptr, *wifi_status = nullptr, *wifi_rows[kScreenWifiEntries] = {};
lv_obj_t *key_page = nullptr, *key_title = nullptr, *key_entry = nullptr, *key_hint = nullptr, *key_labels[12] = {};
ScreenWifiEntry wifi_entries[kScreenWifiEntries];
size_t wifi_count = 0;
bool wifi_scanning = false, wifi_scan_request = false, wifi_credentials_ready = false;
char wifi_selected[33] = "", wifi_password[kScreenWifiPasswordMax + 1] = "";
bool wifi_selected_secure = true;
char wifi_status_text[64] = "";
constexpr int kWifiRowY0 = 56, kWifiRowH = 22;
constexpr int kKeyX[3] = {12, 116, 220}, kKeyY[4] = {78, 118, 158, 198}, kKeyW = 96, kKeyH = 36;
const char* const kKeyText[12] = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "<", "0", "OK"};
lv_obj_t* screen_root = nullptr;
lv_display_t* screen_display = nullptr;
lv_obj_t* function_ribbon = nullptr;
lv_obj_t* function_segments[5] = {};
constexpr uint32_t kRibbonDark = 0x080A0D;
constexpr uint32_t kRibbonColors[] = {0xFFFFFF, 0x278BFF, 0xFF3838, 0x36E874, 0xFFBF40};
constexpr uint8_t kExpressionFlag = 1 << 3;
uint8_t function_flags = 0, rendered_function_flags = 0;
bool eyes_visible = false;
bool enabled = true, status_pending = false;
ScreenStatus pending_status;
char pending_version[64], pending_ip[64], pending_token[65], pending_motor[64], pending_ssid[33];
enum class IconKind { Wifi, Camera, Remote, Battery, Axes, Yaw, Pitch, Mic };
struct Icon { lv_obj_t* obj = nullptr; IconKind kind; bool valid = false; int value = 0; };
Icon icons[8];
// Pages and where gestures lead (0.4.94: ui_pages.h). `page` is what is shown; nav keeps the same page plus the
// Wi-Fi list's opener and the idle clock.
using Page = ui::Page;
Page page = Page::Home;
ui::Nav nav;
uint32_t screen_now_ms = 0;            // the latest tick / touch time (status updates carry none)
ScreenReaction reaction = ScreenReaction::None;
uint32_t reaction_at = 0;
char log_text[kRuntimeLogTextBytes] = {};
uint32_t log_sequence = 0;
bool log_dirty = true;
int touch_start_x = -1, touch_start_y = -1, touch_last_x = -1, touch_last_y = -1;
int touch_max_dx = 0, touch_max_dy = 0;
uint16_t* animation_pixels = nullptr;
lv_image_dsc_t animation_image;
uint32_t animation_frame = 0;
bool pressed = false, touch_cancelled = false, long_press_fired = false;
uint32_t pressed_at = 0, last_release = 0;
// Home page (hello modes step 1b): taps and swipes go through the C3 classifier; tap / double tap are the
// mode's (screen_take_home_gesture), a left swipe opens the summary. Up / down swipes are not wired yet.
touch::HomeTouch home_gesture;           // a failed read on home is a release it may rejoin (home_touch.h)
ScreenHomeGesture home_pending = ScreenHomeGesture::None;
// Mode corner badge (top right, every page) and the 2 s toast; small LVGL objects (internal RAM).
lv_obj_t *mode_badge = nullptr, *mode_toast = nullptr;
lv_obj_t* summary_mode = nullptr;        // summary title row: "模式 脱口秀" in the CJK font, else ASCII
uint32_t toast_until_ms = 0;
bool toast_shown = false;
constexpr uint32_t kToastMs = 2000;
// Chat stage dots (0.4.93): three circles at the home page's top left, clear of the top-right badge and the bottom
// toast; 16 px each, 8 px apart, so they read at arm's length. Small LVGL objects, only while in chat.
lv_obj_t* chat_dots[3] = {};
int chat_dots_lit = -2;
bool chat_dots_grey = false;
uint32_t chat_dots_color = 0;
constexpr int kDotPx = 16, kDotGap = 8, kDotX = 10, kDotY = 8;
constexpr uint32_t kDotDim = 0x5A5A5A, kDotGrey = 0x8C8C8C;
// 0.4.94 quick settings (swipe down) and mode picker (swipe up), ui_pages.h: small LVGL objects (internal RAM),
// built when the page opens and deleted when it closes. Requests wait here until the LVGL timer takes them.
lv_obj_t *quick_page = nullptr, *quick_volume = nullptr, *quick_net[2] = {}, *quick_last = nullptr, *quick_hear = nullptr;
lv_obj_t *picker_page = nullptr, *picker_tiles[ui::kTiles] = {}, *picker_labels[ui::kTiles] = {};
int volume_request = -1, billing_request = -1, hearing_request = -1, pick_request = -1;
int quick_volume_shown = -1;
bool hearing_shown = false;
// A value just pressed is shown until the main loop has applied it and the status caught up.
constexpr uint32_t kRequestHoldMs = 1500;
uint32_t volume_hold_until = 0, hearing_hold_until = 0, billing_line_ms = 0;
uint8_t mode_now = 0, dir_now = 0;
int billing_last = -1;
char billing_code[20] = "";
uint32_t billing_at_ms = 0;
// The latest status, for the quick page (it may open between two status updates).
bool net_connected = false, net_rssi_valid = false, status_hearing = false;
int net_rssi = 0, status_volume = -1;
char net_ssid[33] = "", net_ip[16] = "";
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
void update_ribbon()
{
    if (!function_ribbon) return;
    const uint8_t flags = function_flags |
        ((enabled && page == Page::Home && (eyes_visible || reaction != ScreenReaction::None)) ? kExpressionFlag : 0);
    const uint8_t changed = flags ^ rendered_function_flags;
    for (unsigned i = 0; i < 5; ++i) {
        if (changed & (1 << i))
            lv_obj_set_style_bg_color(function_segments[i],
                lv_color_hex(flags & (1 << i) ? kRibbonColors[i] : kRibbonDark), 0);
    }
    rendered_function_flags = flags;
}
bool update_eyes()
{
    eyes_visible=eyes::view_tick(page==Page::Home,enabled,reaction!=ScreenReaction::None);
    if (portrait) lv_obj_update_flag(portrait,LV_OBJ_FLAG_HIDDEN,eyes_visible);
    update_ribbon();
    return eyes_visible;
}
void open_quick();
void close_quick();
void open_picker();
void close_picker();
void render_quick();
void show_page(Page target, uint32_t now_ms)
{
    if (target != Page::Home) { home_gesture.reset(); home_pending = ScreenHomeGesture::None; }
    if (target != Page::Quick) close_quick();
    if (target != Page::Picker) close_picker();
    nav.go(target, now_ms);
    page = target;
    if (page == Page::Quick) open_quick();
    if (page == Page::Picker) open_picker();
    lv_obj_update_flag(home, LV_OBJ_FLAG_HIDDEN, page != Page::Home);
    lv_obj_update_flag(summary, LV_OBJ_FLAG_HIDDEN, page != Page::Summary);
    lv_obj_update_flag(terminal, LV_OBJ_FLAG_HIDDEN, page != Page::Log);
    lv_obj_update_flag(pairing, LV_OBJ_FLAG_HIDDEN, page != Page::Pairing);
    lv_obj_update_flag(faces_page, LV_OBJ_FLAG_HIDDEN, page != Page::Faces);
    if (wifi_page) lv_obj_update_flag(wifi_page, LV_OBJ_FLAG_HIDDEN, page != Page::Wifi);
    if (key_page) lv_obj_update_flag(key_page, LV_OBJ_FLAG_HIDDEN, page != Page::WifiKey);
    if (page != Page::Home) {
        reaction = ScreenReaction::None;
        cancel_transition();
        lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
    }
    update_eyes();
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
    eyes::view_create(home);
    prepare_reactions();
    reaction_overlay = box(home, 30, 0, 260, 240, kInk);
    lv_obj_add_event_cb(reaction_overlay, reaction_draw, LV_EVENT_DRAW_POST, nullptr);
    lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
#ifdef ESP_PLATFORM
    lv_display_add_event_cb(screen_display, reaction_refresh, LV_EVENT_RENDER_START, nullptr);
    lv_display_add_event_cb(screen_display, reaction_refresh, LV_EVENT_RENDER_READY, nullptr);
#endif

    summary = box(parent, 0, 0, 320, 240, kInk);
    summary_title = label(summary, 12, 7, "chan-X20A", kSignal);
    // 0.4.95: the mode's Chinese name (CJK font demo), between the title and the version (x 210, under the badge).
    summary_mode = label(summary, 104, 5, "", kSignal);
    lv_obj_set_width(summary_mode, 104);
    lv_label_set_long_mode(summary_mode, LV_LABEL_LONG_CLIP);
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
    set_text(summary_rows[9], ui::text::kSummaryHint);

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
    label(pairing, 12, 219, "Tap: summary   Swipe <: Wi-Fi", kMuted);
    lv_obj_add_flag(pairing, LV_OBJ_FLAG_HIDDEN);

    wifi_page = box(parent, 0, 0, 320, 240, kInk);
    label(wifi_page, 12, 7, "Wi-Fi", kSignal);
    label(wifi_page, 254, 7, "Rescan", kMuted);
    box(wifi_page, 12, 28, 296, 1, kRule);
    wifi_status = label(wifi_page, 12, 34, "scanning...", kMuted);
    lv_obj_set_width(wifi_status, 296);
    lv_label_set_long_mode(wifi_status, LV_LABEL_LONG_CLIP);
    for (size_t i = 0; i < kScreenWifiEntries; ++i) {
        wifi_rows[i] = label(wifi_page, 12, kWifiRowY0 + static_cast<int>(i) * kWifiRowH, "", kSignal);
        lv_obj_set_width(wifi_rows[i], 296);
        lv_label_set_long_mode(wifi_rows[i], LV_LABEL_LONG_CLIP);
    }
    box(wifi_page, 12, 212, 296, 1, kRule);
    label(wifi_page, 12, 219, "Tap network   Title: back", kMuted);
    lv_obj_add_flag(wifi_page, LV_OBJ_FLAG_HIDDEN);

    key_page = box(parent, 0, 0, 320, 240, kInk);
    key_title = label(key_page, 12, 7, "", kSignal);
    lv_obj_set_width(key_title, 230);
    lv_label_set_long_mode(key_title, LV_LABEL_LONG_CLIP);
    label(key_page, 254, 7, "Back", kMuted);
    box(key_page, 12, 28, 296, 1, kRule);
    key_entry = label(key_page, 12, 34, "", kSignal);
    lv_obj_set_width(key_entry, 296);
    lv_label_set_long_mode(key_entry, LV_LABEL_LONG_CLIP);
    key_hint = label(key_page, 12, 56, "digits only  <: delete  OK: join", kMuted);
    lv_obj_set_width(key_hint, 296);
    lv_label_set_long_mode(key_hint, LV_LABEL_LONG_CLIP);
    for (int i = 0; i < 12; ++i) {
        const int col = i % 3, row = i / 3;
        auto key = box(key_page, kKeyX[col], kKeyY[row], kKeyW, kKeyH, kChrome);
        lv_obj_set_style_border_color(key, lv_color_hex(kRule), 0);
        lv_obj_set_style_border_width(key, 1, 0);
        key_labels[i] = label(key, kKeyW / 2 - (i == 11 ? 9 : 4), kKeyH / 2 - 8, kKeyText[i], kSignal);
    }
    lv_obj_add_flag(key_page, LV_OBJ_FLAG_HIDDEN);

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
    nav = ui::Nav();
    reaction = ScreenReaction::None;
    log_dirty = true;
    pressed = touch_cancelled = long_press_fired = false;
    last_release = 0;
    animation_clock_ready = false;
    animation_phase_ms = 0;
    animation_frame = 0;
    // A separate display layer keeps the strip above all full-screen pages and
    // eye frames. Neither it nor its segments participates in touch hit-testing.
    function_flags = rendered_function_flags = 0;
    eyes_visible = false;
    function_ribbon = box(lv_display_get_layer_top(screen_display), 0, 237, 320, 3, kInk);
    lv_obj_remove_flag(function_ribbon, LV_OBJ_FLAG_CLICKABLE);
    for (unsigned i = 0; i < 5; ++i) {
        function_segments[i] = box(function_ribbon, i * 64, 0, i == 4 ? 64 : 63, 3, kRibbonDark);
        lv_obj_remove_flag(function_segments[i], LV_OBJ_FLAG_CLICKABLE);
    }
    home_gesture.reset();
    home_pending = ScreenHomeGesture::None;
    auto* top = lv_display_get_layer_top(screen_display);
    mode_badge = label(top, 0, 0, "TALK", kInk);
    lv_obj_set_style_bg_color(mode_badge, lv_color_hex(0xE040A0), 0);
    lv_obj_set_style_bg_opa(mode_badge, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_hor(mode_badge, 4, 0);
    lv_obj_set_style_pad_ver(mode_badge, 1, 0);
    lv_obj_align(mode_badge, LV_ALIGN_TOP_RIGHT, 0, 0);
    lv_obj_remove_flag(mode_badge, LV_OBJ_FLAG_CLICKABLE);
    mode_toast = label(top, 0, 0, "", kSignal);
    lv_obj_set_style_bg_color(mode_toast, lv_color_hex(kInk), 0);
    lv_obj_set_style_bg_opa(mode_toast, LV_OPA_80, 0);
    lv_obj_set_style_pad_hor(mode_toast, 6, 0);
    lv_obj_set_style_pad_ver(mode_toast, 3, 0);
    lv_obj_align(mode_toast, LV_ALIGN_BOTTOM_MID, 0, -12);
    lv_obj_remove_flag(mode_toast, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(mode_toast, LV_OBJ_FLAG_HIDDEN);
    toast_shown = false;
}
void screen_set_enabled(bool value, uint32_t now_ms)
{
    if (!screen_display || enabled == value) return;
    enabled = value;
    lv_obj_update_flag(function_ribbon, LV_OBJ_FLAG_HIDDEN, !enabled);
    if (mode_badge) lv_obj_update_flag(mode_badge, LV_OBJ_FLAG_HIDDEN, !enabled);
    if (mode_toast && !enabled) { lv_obj_add_flag(mode_toast, LV_OBJ_FLAG_HIDDEN); toast_shown = false; }
    update_eyes();
    // Neither a pending tap nor a finger held across the transition may
    // navigate. A fresh release is required before accepting another touch.
    screen_cancel_touch();
    touch_cancelled = true;
    home_gesture.reset();
    home_pending = ScreenHomeGesture::None;
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
    nav.touched(now_ms);                 // the idle clock starts again when the screen comes back
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
    screen_now_ms = now_ms;
    if (toast_shown && int32_t(now_ms - toast_until_ms) >= 0) { lv_obj_add_flag(mode_toast, LV_OBJ_FLAG_HIDDEN); toast_shown = false; }
    if (page == Page::Home) {
        // 0.4.94: the swipes are global (every mode): down quick settings, up mode picker, left summary.
        switch (const auto g = home_gesture.take(now_ms)) {
        case touch::Gesture::Tap: home_pending = ScreenHomeGesture::Tap; break;
        case touch::Gesture::DoubleTap: home_pending = ScreenHomeGesture::DoubleTap; break;
        case touch::Gesture::None: break;
        default: if (const Page target = nav.swipe(g); target != page) show_page(target, now_ms); break;
        }
    } else if (nav.idle(now_ms)) {
        show_page(Page::Home, now_ms);   // 30 s without a touch (Wi-Fi pages 90 s)
    }
    if (quick_page && now_ms - billing_line_ms >= 1000) {
        billing_line_ms = now_ms;
        char text[64];
        ui::billing_line(text, sizeof text, billing_last, billing_code, int64_t(uint32_t(now_ms - billing_at_ms) / 1000));
        set_text(quick_last, text);
    }
    if (reaction != ScreenReaction::None && now_ms - reaction_at >= kReactionDurationMs) {
        reaction = ScreenReaction::None;
        mark_transition("hide");
        lv_obj_add_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN);
    }
    if (update_eyes()) {animation_last_ms=now_ms;return;}
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
    const uint8_t* pixels = collector_laughing_man_frames + frame * COLLECTOR_FACE_FRAME_BYTES;
    const auto* offsets = collector_laughing_man_offsets;
    const auto* palette = collector_laughing_man_palette;
    for (size_t i = 0; i < COLLECTOR_FACE_CHANGED_PIXELS; ++i)
        animation_pixels[offsets[i]] = palette[palette5(pixels,i)];
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
        std::snprintf(pending_ssid, sizeof(pending_ssid), "%s", s.ssid ? s.ssid : "");
        pending_status.ssid = pending_ssid;
        pending_status.version = pending_version;
        pending_status.ip = pending_ip;
        pending_status.token = pending_token;
        pending_status.motor = pending_motor;
        status_pending = true;
        return;
    }
    status_pending = false;
    function_flags = s.function_flags & (ScreenFunctionTracking | ScreenFunctionListening |
                                         ScreenFunctionSpeaking | ScreenFunctionRecognition);
    update_ribbon();
    net_connected = s.wifi_connected && s.ip && s.ip[0];
    std::snprintf(net_ssid, sizeof net_ssid, "%s", s.ssid ? s.ssid : "");
    std::snprintf(net_ip, sizeof net_ip, "%s", s.ip ? s.ip : "");
    net_rssi_valid = s.rssi_valid; net_rssi = s.rssi;
    status_volume = s.volume;
    status_hearing = (s.function_flags & ScreenFunctionListening) != 0;
    if (quick_page) {
        if (int32_t(screen_now_ms - volume_hold_until) >= 0) quick_volume_shown = status_volume;
        if (int32_t(screen_now_ms - hearing_hold_until) >= 0) hearing_shown = status_hearing;
        render_quick();
    }
    char text[160];
    const char* version = s.version ? s.version : "";
    if (std::strncmp(version, "collector-", 10) == 0) version += 10;
    std::snprintf(text, sizeof(text), "chan-X20A             %.8s", version);
    set_text(summary_title, text);
    set_text(summary_rows[0], s.wifi_connected ? (s.ssid && s.ssid[0] ? s.ssid : "online") : "offline");
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
    update_eyes();
    reaction_at = now_ms;
    mark_transition(value == ScreenReaction::None ? "hide" : value == ScreenReaction::Heart ? "heart" : "reject");
    lv_obj_update_flag(reaction_overlay, LV_OBJ_FLAG_HIDDEN, value == ScreenReaction::None);
    lv_obj_invalidate(reaction_overlay);
}
void render_wifi_rows()
{
    char text[64];
    for (size_t i = 0; i < kScreenWifiEntries; ++i) {
        if (i >= wifi_count) { set_text(wifi_rows[i], ""); continue; }
        const auto& e = wifi_entries[i];
        std::snprintf(text, sizeof(text), "%c %-22.22s %4d%s", e.saved ? '*' : ' ', e.ssid, e.rssi, e.secure ? " #" : "  ");
        set_text(wifi_rows[i], text);
    }
    if (wifi_status_text[0]) set_text(wifi_status, wifi_status_text);
    else if (wifi_scanning) set_text(wifi_status, "scanning...");
    else {
        std::snprintf(text, sizeof(text), "%u networks  * saved  # secured", static_cast<unsigned>(wifi_count));
        set_text(wifi_status, wifi_count ? text : "no networks found; tap Rescan");
    }
}
void render_key_entry()
{
    char text[80];
    std::snprintf(text, sizeof(text), "Password: %s_", wifi_password);
    set_text(key_entry, text);
}
void open_wifi_page(uint32_t now_ms)
{
    wifi_status_text[0] = '\0';
    wifi_scan_request = true;
    wifi_scanning = true;
    render_wifi_rows();
    show_page(Page::Wifi, now_ms);
}
void wifi_page_tap(int x, int y, uint32_t now_ms)
{
    if (y < 28) {
        if (x >= 220) { wifi_scan_request = true; wifi_scanning = true; wifi_status_text[0] = '\0'; render_wifi_rows(); }
        else show_page(nav.back(), now_ms);          // pairing, or quick settings when it opened the list
        return;
    }
    for (size_t i = 0; i < wifi_count; ++i) {
        const int top = kWifiRowY0 + static_cast<int>(i) * kWifiRowH - 2;
        if (y < top || y >= top + kWifiRowH) continue;
        std::snprintf(wifi_selected, sizeof(wifi_selected), "%s", wifi_entries[i].ssid);
        wifi_selected_secure = wifi_entries[i].secure;
        wifi_password[0] = '\0';
        set_text(key_title, wifi_selected);
        set_text(key_hint, wifi_selected_secure ? "digits only  <: delete  OK: join" : "open network: OK joins without password");
        render_key_entry();
        show_page(Page::WifiKey, now_ms);
        return;
    }
}
void key_page_tap(int x, int y, uint32_t now_ms)
{
    if (y < 28) { show_page(Page::Wifi, now_ms); return; }
    for (int i = 0; i < 12; ++i) {
        const int col = i % 3, row = i / 3;
        if (x < kKeyX[col] || x >= kKeyX[col] + kKeyW || y < kKeyY[row] || y >= kKeyY[row] + kKeyH) continue;
        const size_t length = std::strlen(wifi_password);
        if (i == 9) { if (length) wifi_password[length - 1] = '\0'; }
        else if (i == 11) {
            if (wifi_selected_secure && length < 8) { set_text(key_hint, "need at least 8 digits"); return; }
            wifi_credentials_ready = true;
            std::snprintf(wifi_status_text, sizeof(wifi_status_text), "joining %.20s ...", wifi_selected);
            render_wifi_rows();
            show_page(Page::Wifi, now_ms);
            return;
        } else if (length < kScreenWifiPasswordMax) {
            wifi_password[length] = kKeyText[i][0];
            wifi_password[length + 1] = '\0';
        }
        render_key_entry();
        return;
    }
}
namespace {
// Black or white text, whichever reads on `bg` (the badge's rule).
uint32_t ink_for(uint32_t bg)
{
    const unsigned r = bg >> 16 & 0xFF, g = bg >> 8 & 0xFF, b = bg & 0xFF;
    return (r * 299 + g * 587 + b * 114) / 1000 > 140 ? kInk : 0xFFFFFF;
}
uint32_t dimmed(uint32_t c)
{
    return ((c >> 16 & 0xFF) * 115 / 255) << 16 | ((c >> 8 & 0xFF) * 115 / 255) << 8 | (c & 0xFF) * 115 / 255;
}
// A full-screen page with a title bar: "< Back" on the left (the top-right corner is the mode badge's).
lv_obj_t* titled_page(const char* title)
{
    auto p = box(screen_root, 0, 0, 320, 240, kInk);
    box(p, 0, 0, 320, ui::kTitleH, kChrome);
    box(p, 0, ui::kTitleH, 320, 1, kRule);
    label(p, 12, 6, ui::text::kBack, kMuted);
    label(p, 100, 6, title, kSignal);
    return p;
}
lv_obj_t* button(lv_obj_t* parent, const ui::Rect& r, const char* text)
{
    auto b = box(parent, r.x, r.y, r.w, r.h, kChrome);
    lv_obj_set_style_border_color(b, lv_color_hex(kRule), 0);
    lv_obj_set_style_border_width(b, 1, 0);
    auto t = label(b, 0, 0, text, kSignal);
    lv_obj_center(t);
    return t;
}
lv_obj_t* clipped(lv_obj_t* parent, int x, int y, int w, uint32_t color)
{
    auto l = label(parent, x, y, "", color);
    lv_obj_set_width(l, w);
    lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
    return l;
}
void render_quick()
{
    if (!quick_page) return;
    char text[64];
    if (quick_volume_shown >= 0) std::snprintf(text, sizeof text, "%3d", quick_volume_shown);
    else std::snprintf(text, sizeof text, " --");
    set_text(quick_volume, text);
    if (net_connected) {
        std::snprintf(text, sizeof text, ui::text::kWifiNamed, net_ssid[0] ? net_ssid : ui::text::kWifiHidden);
        set_text(quick_net[0], text);
        if (net_rssi_valid) std::snprintf(text, sizeof text, "%s  %d dBm", net_ip, net_rssi);
        else std::snprintf(text, sizeof text, "%s", net_ip);
        set_text(quick_net[1], text);
    } else {
        set_text(quick_net[0], ui::text::kWifiOffline);
        set_text(quick_net[1], ui::text::kNotConnected);
    }
    set_text(quick_hear, hearing_shown ? ui::text::kOn : ui::text::kOff);
    ui::billing_line(text, sizeof text, billing_last, billing_code, int64_t(uint32_t(screen_now_ms - billing_at_ms) / 1000));
    set_text(quick_last, text);
}
void open_quick()
{
    if (quick_page) return;
    quick_page = titled_page(ui::text::kQuickTitle);
    label(quick_page, 12, 44, ui::text::kVolume, kSignal);
    button(quick_page, ui::kVolumeDown, "-");
    button(quick_page, ui::kVolumeUp, "+");
    quick_volume = label(quick_page, 202, 44, "", kSignal);
    box(quick_page, 12, 73, 296, 1, kRule);
    quick_net[0] = clipped(quick_page, 12, 76, 204, kSignal);
    quick_net[1] = clipped(quick_page, 12, 95, 204, kMuted);
    button(quick_page, ui::kWifiButton, ui::text::kWifiButton);
    box(quick_page, 12, 115, 296, 1, kRule);
    label(quick_page, 12, 128, ui::text::kBilling, kSignal);
    button(quick_page, ui::kAllOn, ui::text::kAllOn);
    button(quick_page, ui::kAllOff, ui::text::kAllOff);
    quick_last = clipped(quick_page, 12, 158, 296, kMuted);
    label(quick_page, 12, 188, ui::text::kHearing, kSignal);
    quick_hear = button(quick_page, ui::kHearing, "");
    if (int32_t(screen_now_ms - volume_hold_until) >= 0) quick_volume_shown = status_volume;
    if (int32_t(screen_now_ms - hearing_hold_until) >= 0) hearing_shown = status_hearing;
    billing_line_ms = screen_now_ms;
    render_quick();
}
void close_quick()
{
    if (!quick_page) return;
    lv_obj_delete(quick_page);           // its children with it
    quick_page = quick_volume = quick_last = quick_hear = nullptr;
    quick_net[0] = quick_net[1] = nullptr;
}
void render_picker()
{
    if (!picker_page) return;
    modes::rules::State s;
    s.mode = static_cast<modes::rules::Mode>(mode_now);
    s.dir = static_cast<modes::rules::Dir>(dir_now);
    for (size_t i = 0; i < ui::kTiles; ++i) {
        const bool current = ui::tile_current(i, s);
        const uint32_t bg = current ? ui::tile_color(i) : dimmed(ui::tile_color(i));
        lv_obj_set_style_bg_color(picker_tiles[i], lv_color_hex(bg), 0);
        lv_obj_set_style_border_width(picker_tiles[i], current ? 3 : 0, 0);
        lv_obj_set_style_text_color(picker_labels[i], lv_color_hex(ink_for(bg)), 0);
    }
}
void open_picker()
{
    if (picker_page) return;
    picker_page = titled_page(ui::text::kPickerTitle);
    for (size_t i = 0; i < ui::kTiles; ++i) {
        const ui::Rect r = ui::tile_rect(i);
        picker_tiles[i] = box(picker_page, r.x, r.y, r.w, r.h, ui::tile_color(i));
        lv_obj_set_style_radius(picker_tiles[i], 6, 0);
        lv_obj_set_style_border_color(picker_tiles[i], lv_color_hex(0xFFFFFF), 0);
        picker_labels[i] = label(picker_tiles[i], 0, 0, ui::tile_label(i), kInk);
        lv_obj_center(picker_labels[i]);
    }
    render_picker();
}
void close_picker()
{
    if (!picker_page) return;
    lv_obj_delete(picker_page);
    picker_page = nullptr;
    for (auto& t : picker_tiles) t = nullptr;
    for (auto& l : picker_labels) l = nullptr;
}
void quick_tap(int x, int y, uint32_t now_ms)
{
    const ui::Quick b = ui::quick_hit(x, y);
    switch (b) {
    case ui::Quick::Back: show_page(nav.back(), now_ms); break;
    case ui::Quick::VolumeDown: case ui::Quick::VolumeUp: {
        // Shown at once; the main loop saves it (NVS) and the next sound uses it.
        const int base = quick_volume_shown >= 0 ? quick_volume_shown : status_volume >= 0 ? status_volume : kVolumeDefault;
        quick_volume_shown = volume_request = ui::volume_after(b, base);
        volume_hold_until = now_ms + kRequestHoldMs;
        render_quick();
        break;
    }
    case ui::Quick::Wifi: open_wifi_page(now_ms); break;
    case ui::Quick::AllOn: case ui::Quick::AllOff: billing_request = ui::billing_digit(b); break;   // toast: mind's
    case ui::Quick::Hearing:
        hearing_shown = !hearing_shown;
        hearing_request = hearing_shown ? 1 : 0;
        hearing_hold_until = now_ms + kRequestHoldMs;
        render_quick();
        break;
    case ui::Quick::None: break;
    }
}
void picker_tap(int x, int y, uint32_t now_ms)
{
    if (ui::kBack.hit(x, y)) { show_page(Page::Home, now_ms); return; }
    const int tile = ui::picker_hit(x, y);
    if (tile < 0) return;                // a gap between tiles
    pick_request = tile;                 // the rules decide (enter, leave, switch direction); the toast says what
    show_page(Page::Home, now_ms);
}
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
    if (page == Page::Home) {
        home_gesture.sample(down, now_ms, x, y);
        if (!down && pressed) last_release = now_ms;
        pressed = down;
        return;
    }
    screen_now_ms = now_ms;
    if (down) nav.touched(now_ms);       // a finger on the page keeps it open
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
        const bool located = touch_start_x >= 0 && touch_start_y >= 0;
        const ui::Gesture g = ui::stroke(now_ms - pressed_at, located ? touch_last_x - touch_start_x : 0,
                                         located ? touch_last_y - touch_start_y : 0, touch_max_dx, touch_max_dy);
        if (g == ui::Gesture::Tap) {
            if (page == Page::Wifi) wifi_page_tap(touch_last_x, touch_last_y, now_ms);
            else if (page == Page::WifiKey) key_page_tap(touch_last_x, touch_last_y, now_ms);
            else if (page == Page::Quick) quick_tap(touch_last_x, touch_last_y, now_ms);
            else if (page == Page::Picker) picker_tap(touch_last_x, touch_last_y, now_ms);
            else show_page(page == Page::Pairing ? Page::Summary : Page::Home, now_ms);
            last_release = now_ms;
        } else if (g != ui::Gesture::None) {
            // Each page closes with the swipe opposite to the one that opened it (ui_pages.h).
            const Page target = nav.swipe(g);
            if (target == Page::Wifi && page != Page::Wifi) open_wifi_page(now_ms);
            else if (target != page) show_page(target, now_ms);
            last_release = now_ms;
        }
    }
    pressed = down;
}
void screen_cancel_touch()
{
    // Home: a failed read is only a release for the classifier, which rejoins the stroke if the finger is still
    // there within 60 ms. Cancelling here (touch_cancelled) would drop the rest of the stroke and turn a cut-off
    // stroke into a phantom tap (review I1). Other pages keep the cancel.
    if (page == Page::Home) { home_gesture.lost(); pressed = false; return; }
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
void screen_update_wifi_scan(const ScreenWifiEntry* entries, size_t count, bool scanning)
{
    if (!wifi_page) return;
    wifi_count = std::min(count, kScreenWifiEntries);
    for (size_t i = 0; i < wifi_count; ++i) wifi_entries[i] = entries[i];
    wifi_scanning = scanning;
    render_wifi_rows();
}
void screen_update_wifi_status(const char* text)
{
    if (!wifi_page) return;
    std::snprintf(wifi_status_text, sizeof(wifi_status_text), "%s", text ? text : "");
    render_wifi_rows();
}
bool screen_wifi_take_scan_request()
{
    const bool pending = wifi_scan_request;
    wifi_scan_request = false;
    return pending;
}
bool screen_wifi_take_credentials(char* ssid, size_t ssid_size, char* password, size_t password_size)
{
    if (!wifi_credentials_ready) return false;
    wifi_credentials_ready = false;
    std::snprintf(ssid, ssid_size, "%s", wifi_selected);
    std::snprintf(password, password_size, "%s", wifi_password);
    wifi_password[0] = '\0';
    return true;
}
bool screen_showing_wifi() { return page == Page::Wifi || page == Page::WifiKey; }
ScreenHomeGesture screen_take_home_gesture()
{
    const ScreenHomeGesture g = home_pending;
    home_pending = ScreenHomeGesture::None;
    return g;
}
void screen_update_mode(const char* text, uint32_t color, bool online)
{
    if (!mode_badge) return;
    char buf[24];
    std::snprintf(buf, sizeof buf, "%s%s", text ? text : "", online ? "" : " OFF");
    const uint32_t bg = online ? color : 0x505050;
    set_text(mode_badge, buf);
    lv_obj_set_style_bg_color(mode_badge, lv_color_hex(bg), 0);
    lv_obj_set_style_text_color(mode_badge, lv_color_hex(ink_for(bg)), 0);
}
void screen_update_mode_name(const char* text, const lv_font_t* font)
{
    if (!summary_mode) return;
    const lv_font_t* use = font ? font : &collector_mono_15;
    if (lv_obj_get_style_text_font(summary_mode, LV_PART_MAIN) != use) lv_obj_set_style_text_font(summary_mode, use, 0);
    set_text(summary_mode, text ? text : "");
}
void screen_show_toast(const char* text, uint32_t now_ms)
{
    if (!mode_toast || !enabled || !text || !*text) return;
    set_text(mode_toast, text);
    lv_obj_remove_flag(mode_toast, LV_OBJ_FLAG_HIDDEN);
    toast_until_ms = now_ms + kToastMs;
    toast_shown = true;
}
void screen_update_chat_dots(bool visible, int lit, bool grey, uint32_t color)
{
    if (!home) return;
    if (!visible) {
        for (auto*& d : chat_dots) if (d) { lv_obj_delete(d); d = nullptr; }
        chat_dots_lit = -2;
        return;
    }
    if (!chat_dots[0]) {
        for (int i = 0; i < 3; ++i) {
            auto* d = lv_obj_create(home);
            lv_obj_remove_style_all(d);
            lv_obj_set_pos(d, kDotX + i * (kDotPx + kDotGap), kDotY);
            lv_obj_set_size(d, kDotPx, kDotPx);
            lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, 0);
            lv_obj_set_style_border_width(d, 2, 0);
            lv_obj_remove_flag(d, LV_OBJ_FLAG_CLICKABLE);
            lv_obj_remove_flag(d, LV_OBJ_FLAG_SCROLLABLE);
            chat_dots[i] = d;
        }
        chat_dots_lit = -2;
    }
    if (lit == chat_dots_lit && grey == chat_dots_grey && color == chat_dots_color) return;
    for (int i = 0; i < 3; ++i) {
        const bool on = grey || i == lit;
        const uint32_t c = grey ? kDotGrey : on ? color : kDotDim;
        lv_obj_set_style_border_color(chat_dots[i], lv_color_hex(c), 0);
        lv_obj_set_style_bg_color(chat_dots[i], lv_color_hex(c), 0);
        lv_obj_set_style_bg_opa(chat_dots[i], on ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    }
    chat_dots_lit = lit; chat_dots_grey = grey; chat_dots_color = color;
}
void screen_update_mode_state(uint8_t mode, uint8_t dir)
{
    if (mode >= modes::rules::kModeCount || dir > uint8_t(modes::rules::Dir::EnZh)) return;
    if (mode == mode_now && dir == dir_now) return;
    mode_now = mode; dir_now = dir;
    render_picker();
}
void screen_update_billing(int digit, const char* code, uint32_t at_ms)
{
    billing_last = digit == 0 || digit == 9 ? digit : -1;
    std::snprintf(billing_code, sizeof billing_code, "%s", code ? code : "");
    billing_at_ms = at_ms;
    if (quick_page) render_quick();
}
bool screen_take_mode_pick(size_t& tile)
{
    if (pick_request < 0) return false;
    tile = static_cast<size_t>(pick_request);
    pick_request = -1;
    return true;
}
int screen_take_volume_request() { const int v = volume_request; volume_request = -1; return v; }
int screen_take_billing_request() { const int v = billing_request; billing_request = -1; return v; }
int screen_take_hearing_request() { const int v = hearing_request; hearing_request = -1; return v; }
bool screen_showing_quick() { return page == Page::Quick; }
bool screen_showing_picker() { return page == Page::Picker; }
}
