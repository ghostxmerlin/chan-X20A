// collector-0.4.98 home top row on the real screen.cpp (host LVGL): stage blocks (chat / crowd), M12 vision (off /
// offline / hot / on, person, face, recognised name in the CJK font or its ASCII stand-in), Wi-Fi bars and battery,
// all ending before the mode badge. Writes one PPM per case (the top layer with the badge included) and checks that
// the blocks are as tall as the badge, that nothing in the row reaches the badge, and that a name never draws boxes.
// 0.5.00: the 视觉爆发 burst group (eye, look count, time bar) between the name and the Wi-Fi bars, and its toasts.
//   top_row_preview <output dir> <CJK font parts dir, e.g. assets/ui-font/sdcard/soul/font>
#include <collector/screen.h>
#include "font_load.h"
#include <src/display/lv_display_private.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

std::vector<uint8_t> display_pixels(320 * 240 * 3);
namespace hal_bridge { void board_set_display_backlight_enabled(bool) {} }
using namespace collector;

namespace {
uint32_t t = 1000;
std::string out;
void frame() { lv_tick_inc(20); lv_timer_handler(); }
void tick(uint32_t ms = 20) { for (uint32_t i = 0; i < ms; i += 20) { t += 20; screen_tick(t); frame(); } }
void save(const std::string& name)
{
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(lv_display_get_default());
    auto* file = std::fopen((out + "/" + name + ".ppm").c_str(), "wb");
    assert(file);
    std::fprintf(file, "P6\n320 240\n255\n");
    std::fwrite(display_pixels.data(), 1, display_pixels.size(), file);
    std::fclose(file);
    std::printf("%s\n", name.c_str());
}
const uint8_t* px(int x, int y) { return &display_pixels[(y * 320 + x) * 3]; }
bool is(const uint8_t* p, uint32_t c) { return p[0] == (c >> 16 & 0xFF) && p[1] == (c >> 8 & 0xFF) && p[2] == (c & 0xFF); }

ScreenStatus status;
lv_obj_t *badge = nullptr, *row = nullptr;
void base()
{
    status = ScreenStatus{};
    status.version = "collector-0.4.98"; status.ip = "192.0.2.10"; status.ssid = "example-net"; status.token = "";
    status.wifi_connected = true; status.rssi_valid = true; status.rssi = -50; status.volume = 80;
    status.power_valid = true; status.battery_percent = 76; status.charging = false;
    status.vision.power = top::VisionPower::On;
}
void vision(top::VisionPower p, bool person = false, bool face = false, const char* name = "")
{
    status.vision = top::Vision{};
    status.vision.power = p; status.vision.person = person; status.vision.face = face;
    std::snprintf(status.vision.name, sizeof status.vision.name, "%s", name);
}
// The row is as tall as the badge, starts at its top and ends before it; nothing is drawn in the gap (checked on the
// saved frame).
lv_area_t row_area, badge_area;
void check_layout()
{
    lv_obj_update_layout(row);
    lv_obj_get_coords(badge, &badge_area);
    lv_obj_get_coords(row, &row_area);
    assert(row_area.y1 == badge_area.y1 && lv_area_get_height(&row_area) == lv_area_get_height(&badge_area));
    assert(row_area.x2 < badge_area.x1 - 4);
}
void check_gap()
{
    for (int y = 0; y < lv_area_get_height(&badge_area); ++y)
        for (int x = row_area.x2 + 1; x < badge_area.x1; ++x) assert(is(px(x, y), 0));
}
void shot(const std::string& name)
{
    screen_update(status);
    tick(60);
    check_layout();
    save(name);
    check_gap();
}
void dots(bool visible, int lit, bool grey, uint32_t color) { screen_update_chat_dots(visible, lit, grey, color); }
}

int main(int argc, char** argv)
{
    assert(argc == 3);
    out = argv[1];
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
    static LoadedFont fonts[ui_font::kVariantCount];
    for (size_t i = 0; i < ui_font::kVariantCount; ++i)
        if (!load_font(argv[2], ui_font::kVariants[i], fonts[i])) { std::fprintf(stderr, "no font parts\n"); return 1; }

    screen_create(lv_screen_active());
    badge = lv_obj_get_child(lv_display_get_layer_top(lv_display_get_default()), 1);
    auto* home = lv_obj_get_child(lv_screen_active(), 0);
    row = lv_obj_get_child(home, -1);
    constexpr uint32_t kMeet = 0x30D060;
    base();
    screen_update_mode("DOCK", kMeet, true);
    screen_set_cjk_font(&fonts[0].font, &fonts[1].font);

    // Crowd (DOCK, was MEET): the three stage blocks as tall as the badge, in the mode's colour.
    dots(true, 0, false, kMeet);
    vision(top::VisionPower::On, true, true, "张三");
    shot("crowd-listen");
    {
        lv_area_t b;
        lv_obj_get_coords(badge, &b);
        const int h = lv_area_get_height(&b);
        // The first block: filled in the mode colour over the badge's full height, and narrow (one block of 7 px).
        assert(is(px(9, 0), kMeet) && is(px(9, h - 1), kMeet) && !is(px(9, h), kMeet));
        assert(is(px(6, h / 2), kMeet) && is(px(12, h / 2), kMeet) && !is(px(13, h / 2), kMeet));
        assert(!is(px(19, h / 2), kMeet));                   // the second block is only an outline (grey)
        std::printf("badge height %d, block 7 x %d\n", h, h);
    }
    dots(true, 1, false, kMeet); shot("crowd-think");
    dots(true, 2, false, kMeet); shot("crowd-speak");
    dots(true, -1, true, kMeet); shot("crowd-skip-grey");
    dots(true, -1, false, kMeet); shot("crowd-idle");

    // M12 in every mode (TALK: no stage blocks, the row starts with the lens).
    dots(false, -1, false, 0);
    screen_update_mode("TALK", 0xE040A0, true);
    vision(top::VisionPower::Offline); shot("m12-offline");
    vision(top::VisionPower::Off); shot("m12-off");
    vision(top::VisionPower::Hot); shot("m12-hot");
    vision(top::VisionPower::On); shot("m12-on-no-one");
    vision(top::VisionPower::On, true); shot("m12-person");
    vision(top::VisionPower::On, true, true); shot("m12-face");
    vision(top::VisionPower::On, true, true, "张三"); shot("m12-known-cjk");
    vision(top::VisionPower::On, true, true, "Alice"); shot("m12-known-ascii-name");
    // A character outside the font (U+4DB5, CJK extension A): "known", never a box.
    vision(top::VisionPower::On, true, true, "\xE4\xB6\xB5"); shot("m12-known-missing-glyph");
    {
        assert(!std::strcmp(screen_top_name(), top::kKnownStandIn));
    }
    // Without the CJK font: the Chinese name falls back to "known"; the font arriving redraws it in Chinese.
    screen_set_cjk_font(nullptr, nullptr);
    vision(top::VisionPower::On, true, true, "张三"); shot("m12-known-no-font");
    assert(!std::strcmp(screen_top_name(), top::kKnownStandIn));
    screen_set_cjk_font(&fonts[0].font, &fonts[1].font);
    tick(60);
    assert(!std::strcmp(screen_top_name(), "张三"));

    // Battery and Wi-Fi.
    vision(top::VisionPower::On);
    status.battery_percent = 12; shot("battery-low");
    status.battery_percent = 25; shot("battery-mid");
    status.battery_percent = 45; status.charging = true; shot("battery-charging");
    status.battery_percent = 100; shot("battery-full-charging");
    status.power_valid = false; status.charging = false; shot("battery-unknown");
    base();
    status.wifi_connected = false; status.ip = ""; status.rssi_valid = false; shot("wifi-off");
    base();
    status.rssi = -80; shot("wifi-weak");
    status.rssi = -70; shot("wifi-fair");
    status.rssi = -50; shot("wifi-strong");
    status.rssi_valid = false; shot("wifi-unknown-signal");

    // Worst cases: the widest badge with a long name (clipped at a character), and crowd offline with all lit.
    base();
    screen_update_mode("SUB ZH>EN", 0x7C6CF0, true);
    status.battery_percent = 100; status.charging = true;
    vision(top::VisionPower::On, true, true, "欧阳娜娜娜娜娜娜娜");
    shot("worst-sub-long-name");
    {
        // Cut between characters: a shorter, well-formed prefix of the name.
        const char* full = "欧阳娜娜娜娜娜娜娜";
        const size_t n = std::strlen(screen_top_name());
        assert(n > 0 && n < std::strlen(full) && n % 3 == 0 && !std::strncmp(screen_top_name(), full, n));
        assert(top::all_chars(screen_top_name(), [](uint32_t) { return true; }));
        // The bolt going away gives the name more room again (no vision change needed).
        status.charging = false;
        shot("worst-sub-long-name-not-charging");
        assert(std::strlen(screen_top_name()) > n);
        status.charging = true;
    }
    screen_update_mode("DOCK", kMeet, false);
    dots(true, 2, false, kMeet);
    vision(top::VisionPower::On, true, true, "Alexandria-Smith");
    shot("worst-meet-off-long-ascii");
    {
        const size_t n = std::strlen(screen_top_name());
        assert(n > 0 && n < 16 && !std::strncmp(screen_top_name(), "Alexandria-Smith", n));
    }
    dots(false, -1, false, 0);
    screen_update_mode("TALK", 0xE040A0, true);
    base();
    vision(top::VisionPower::On, true, true, "张三");
    status.battery_percent = 64;
    shot("talk-typical");

    // 0.5.00 vision burst (DOCK, where "注意看" starts it): eye + count + time bar; the name stays whole.
    {
        constexpr uint32_t kBurst = 0xFF5AD8;
        const auto pink_in_row = [] {
            for (int y = 0; y < top::kRowH; ++y) for (int x = 0; x < 320; ++x) if (is(px(x, y), kBurst)) return true;
            return false;
        };
        screen_update_mode("DOCK", kMeet, true);
        dots(true, 0, false, kMeet);
        base();
        vision(top::VisionPower::On, true, true, "张三");
        shot("burst-off-before");
        assert(!pink_in_row());
        screen_update_burst(true, 0, 120, 120);
        screen_show_toast("LOOKING 2 MIN", t);
        shot("burst-start-toast");
        assert(pink_in_row() && !std::strcmp(screen_top_name(), "张三"));
        tick(2100);                                        // the toast is gone
        screen_update_burst(true, 12, 63, 120);
        shot("burst-12-looks");
        assert(!std::strcmp(screen_top_name(), "张三"));
        screen_update_burst(true, 31, 4, 120);
        shot("burst-ending");
        // Unchanged count and bar pixels: no redraw.
        lv_refr_now(lv_display_get_default());
        screen_update_burst(true, 31, 4, 120);
        assert(!lv_display_get_default()->inv_p);
        vision(top::VisionPower::On, true, true, "");
        screen_update_burst(true, 7, 90, 120);
        shot("burst-no-name");
        // A long name: the count gives way (eye and bar stay), the name keeps more room than with the count.
        vision(top::VisionPower::On, true, true, "Alexandria-Smith");
        screen_update_burst(true, 123, 90, 120);
        shot("burst-long-name-eye-only");
        assert(pink_in_row() && std::strlen(screen_top_name()) >= 4);
        // Without the stage blocks (TALK) and charging.
        dots(false, -1, false, 0);
        screen_update_mode("TALK", 0xE040A0, true);
        vision(top::VisionPower::On, true, true, "me01");
        status.charging = true;
        screen_update_burst(true, 5, 100, 120);
        shot("burst-talk-ascii-name");
        assert(!std::strcmp(screen_top_name(), "me01"));
        screen_update_burst(false, 0, 0, 1);
        screen_show_toast("LOOK DONE 5", t);
        shot("burst-done-toast");
        assert(!pink_in_row() && !std::strcmp(screen_top_name(), "me01"));
        tick(2100);
        base();
        vision(top::VisionPower::On, true, true, "张三");
        status.battery_percent = 64;
    }

    // Unchanged status: the row does not redraw (no dirty area after a repeated update).
    screen_update(status);
    tick(60);
    lv_refr_now(lv_display_get_default());
    screen_update(status);
    assert(!lv_display_get_default()->inv_p);
    std::puts("top row preview ok");
}
