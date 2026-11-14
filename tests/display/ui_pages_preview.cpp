// collector-0.4.94 global swipes on the real screen.cpp (host LVGL): home swipe down / up / left opens quick settings
// / the mode picker / the summary, the opposite swipe and "< Back" close them, 30 s idle goes home, the pages are
// built on open and deleted on close, and their buttons hand back requests. Writes PPM screenshots of both pages
// (the top layer with the mode badge and toast included).
#include <collector/screen.h>
#include <collector/ui_pages.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

std::vector<uint8_t> display_pixels(320 * 240 * 3);
namespace hal_bridge {
void board_set_display_backlight_enabled(bool) {}
}
using namespace collector;

namespace {
uint32_t t = 1000;
void frame() { lv_tick_inc(20); lv_timer_handler(); }
void tick(uint32_t ms = 20) { for (uint32_t i = 0; i < ms; i += 20) { t += 20; screen_tick(t); frame(); } }
// Home strokes go through the classifier (20 ms frames, released 60 ms before classification).
void stroke(int x1, int y1, int x2, int y2)
{
    for (int i = 0; i <= 5; ++i) { t += 20; screen_touch(true, t, x1 + (x2 - x1) * i / 5, y1 + (y2 - y1) * i / 5); screen_tick(t); }
    t += 20; screen_touch(false, t); screen_tick(t);
    tick(400);
}
void tap(int x, int y)
{
    t += 20; screen_touch(true, t, x, y);
    t += 80; screen_touch(true, t, x, y);
    t += 20; screen_touch(false, t);
    tick(300);
}
void save(const std::string& path)
{
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(lv_display_get_default());
    auto* file = std::fopen(path.c_str(), "wb");
    assert(file);
    std::fprintf(file, "P6\n320 240\n255\n");
    std::fwrite(display_pixels.data(), 1, display_pixels.size(), file);
    std::fclose(file);
}
uint32_t children() { return lv_obj_get_child_count(lv_screen_active()); }
const uint8_t* px(int x, int y) { return &display_pixels[(y * 320 + x) * 3]; }
}

int main(int argc, char** argv)
{
    assert(argc == 2);
    const std::string out = argv[1];
    lv_init();
    auto display = lv_display_create(320, 240);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB888);
    std::vector<uint8_t> framebuffer(320 * 20 * 3);
    lv_display_set_buffers(display, framebuffer.data(), nullptr, framebuffer.size(), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t* area, uint8_t* pixels) {
        const auto stride = lv_draw_buf_width_to_stride(lv_area_get_width(area), LV_COLOR_FORMAT_RGB888);
        for (int y = area->y1; y <= area->y2; ++y) for (int x = area->x1; x <= area->x2; ++x) {
            const auto* p = pixels + (y - area->y1) * stride + (x - area->x1) * 3;
            auto* o = &display_pixels[(y * 320 + x) * 3];
            o[0] = p[2]; o[1] = p[1]; o[2] = p[0];
        }
        lv_display_flush_ready(d);
    });
    screen_create(lv_screen_active());
    ScreenStatus s;
    s.version = "collector-0.4.94"; s.ip = "192.0.2.10"; s.ssid = "example-net"; s.token = "";
    s.wifi_connected = true; s.rssi_valid = true; s.rssi = -58; s.volume = 80;
    screen_update(s);
    screen_update_mode("TALK", 0xE040A0, true);
    tick(100);
    const uint32_t base = children();

    // Swipe down on home: quick settings, built now.
    stroke(160, 60, 160, 180);
    assert(screen_showing_quick() && children() == base + 1);
    screen_update_billing(9, "ok", t - 125000);
    tick(1100);
    save(out + "/quick.ppm");
    // Volume +: shown at once and handed back once; then -, -.
    tap(280, 50);
    assert(screen_take_volume_request() == 90 && screen_take_volume_request() == -1);
    tap(150, 50); tap(150, 50);
    assert(screen_take_volume_request() == 70);
    // Billing: 全开 = 9, 全关 = 0 (sent by the caller as the digit words).
    tap(140, 130);
    assert(screen_take_billing_request() == 9);
    tap(250, 130);
    assert(screen_take_billing_request() == 0 && screen_take_billing_request() == -1);
    // Hearing toggles from what the status said (off).
    tap(250, 190);
    assert(screen_take_hearing_request() == 1);
    // The Wi-Fi button opens the existing list; back returns to quick settings.
    tap(260, 90);
    assert(screen_showing_wifi() && !screen_showing_quick() && children() == base);   // quick deleted
    assert(screen_wifi_take_scan_request());
    tap(40, 10);
    assert(screen_showing_quick() && children() == base + 1);
    // The opposite swipe (up) closes it and deletes it.
    stroke(160, 180, 160, 60);
    assert(!screen_showing_status() && children() == base);

    // Swipe up: the mode picker; the current mode is highlighted.
    screen_update_mode_state(uint8_t(modes::rules::Mode::Subtitle), uint8_t(modes::rules::Dir::EnZh));
    stroke(160, 180, 160, 60);
    assert(screen_showing_picker() && children() == base + 1);
    tick(100);
    save(out + "/picker.ppm");
    // The highlighted tile (SUB EN>ZH, index 5) has a white border; the others none.
    const ui::Rect cur = ui::tile_rect(5), other = ui::tile_rect(0);
    assert(px(cur.x + 1, cur.y + cur.h / 2)[0] == 255 && px(cur.x + 1, cur.y + cur.h / 2)[1] == 255);
    assert(px(other.x + 1, other.y + other.h / 2)[0] != 255 || px(other.x + 1, other.y + other.h / 2)[1] != 255);
    // A tap on a gap does nothing; on a tile it hands back that tile and goes home.
    tap(106, 60);
    size_t tile = 99;
    assert(screen_showing_picker() && !screen_take_mode_pick(tile));
    tap(160, 196);                          // PAL
    assert(screen_take_mode_pick(tile) && tile == 7 && !screen_showing_status() && children() == base);
    // Down closes it; so does "< Back".
    stroke(160, 180, 160, 60);
    stroke(160, 60, 160, 180);
    assert(!screen_showing_status());
    stroke(160, 180, 160, 60);
    tap(40, 10);
    assert(!screen_showing_status() && !screen_take_mode_pick(tile));

    // Left: the summary; right closes it. A tap on home stays the mode's (no page).
    stroke(260, 120, 80, 120);
    assert(screen_showing_status() && !screen_showing_log());
    stroke(80, 120, 260, 120);
    assert(!screen_showing_status());
    tap(160, 120);
    tick(400);
    assert(!screen_showing_status() && screen_take_home_gesture() == ScreenHomeGesture::Tap);

    // 30 s with no touch: back home (and the page is deleted).
    stroke(160, 60, 160, 180);
    assert(screen_showing_quick());
    tick(ui::kIdleMs - 1000);
    assert(screen_showing_quick());
    tick(1200);
    assert(!screen_showing_status() && children() == base);
    std::puts("ui pages preview ok");
}
