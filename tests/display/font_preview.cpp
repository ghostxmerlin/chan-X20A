// Host check of the screen's CJK font through real LVGL 9.4 (collector-0.4.95-font): the generated SD parts are
// joined, validated and bound exactly as the robot does (ui_font_blob.h / ui_font_lvgl.h), then drawn.
//   font_preview <parts dir: assets/ui-font/sdcard/soul/font> <output dir>
// Writes font-sample.ppm (both sizes) and summary-cjk.ppm / summary-ascii.ppm (the summary page with and without
// the font), and fails if a mode name has a missing glyph or draws nothing.
#include <collector/screen.h>
#include "font_load.h"
#include <collector/ui_text_zh.h>
#include <src/misc/lv_text_private.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

LV_FONT_DECLARE(collector_mono_15);
namespace hal_bridge { void board_set_display_backlight_enabled(bool) {} }
using namespace collector;

std::vector<uint8_t> pixels(320 * 240 * 3);

void snapshot(lv_obj_t* obj, const std::string& path)
{
    lv_obj_update_layout(obj);
    auto* buf = lv_snapshot_take(obj, LV_COLOR_FORMAT_RGB888);
    assert(buf);
    FILE* f = std::fopen(path.c_str(), "wb");
    assert(f);
    std::fprintf(f, "P6\n%u %u\n255\n", unsigned(buf->header.w), unsigned(buf->header.h));
    for (uint32_t y = 0; y < buf->header.h; ++y)
        for (uint32_t x = 0; x < buf->header.w; ++x) {
            const uint8_t* p = buf->data + y * buf->header.stride + x * 3;
            const uint8_t rgb[3] = {p[2], p[1], p[0]};
            std::fwrite(rgb, 1, 3, f);
        }
    std::fclose(f);
    lv_draw_buf_destroy(buf);
}

// Lit pixels inside a rectangle of the physical (flushed) frame, which includes the top layer.
size_t lit(int x0, int y0, int x1, int y1)
{
    size_t n = 0;
    for (int y = y0; y < y1; ++y)
        for (int x = x0; x < x1; ++x) {
            const uint8_t* p = &pixels[(y * 320 + x) * 3];
            n += p[0] + p[1] + p[2] > 120;
        }
    return n;
}

int main(int argc, char** argv)
{
    assert(argc == 3);
    const std::string dir = argv[1], out = argv[2];
    lv_init();
    auto* display = lv_display_create(320, 240);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB888);
    std::vector<uint8_t> framebuffer(320 * 20 * 3);
    lv_display_set_buffers(display, framebuffer.data(), nullptr, framebuffer.size(), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t* area, uint8_t* px) {
        const auto stride = lv_draw_buf_width_to_stride(lv_area_get_width(area), LV_COLOR_FORMAT_RGB888);
        for (int y = area->y1; y <= area->y2; ++y) for (int x = area->x1; x <= area->x2; ++x) {
            const auto* p = px + (y - area->y1) * stride + (x - area->x1) * 3;
            auto* o = &pixels[(y * 320 + x) * 3];
            o[0] = p[2]; o[1] = p[1]; o[2] = p[0];
        }
        lv_display_flush_ready(d);
    });

    static LoadedFont fonts[ui_font::kVariantCount];
    for (size_t i = 0; i < ui_font::kVariantCount; ++i) {
        if (!load_font(dir, ui_font::kVariants[i], fonts[i])) { std::fprintf(stderr, "missing parts in %s\n", dir.c_str()); return 1; }
    }
    // Every mode name resolves to real glyphs in both sizes.
    for (auto& f : fonts)
        for (const char* name : ui_text::kModeNames)
            for (uint32_t i = 0; name[i];) {
                const uint32_t c = lv_text_encoded_next(name, &i);   // advances i past one UTF-8 letter
                lv_font_glyph_dsc_t g{};
                assert(lv_font_get_glyph_dsc(&f.font, &g, c, 0) && g.box_w > 0);
            }

    // A sample sheet in both sizes (screen colors).
    auto* sheet = lv_obj_create(nullptr);
    lv_obj_set_size(sheet, 320, 240);
    lv_obj_set_style_bg_color(sheet, lv_color_hex(0x000000), 0);
    lv_obj_set_style_pad_all(sheet, 0, 0);
    const char* lines[] = {"模式 脱口秀 对话 设置", "事件驱动 字幕 围观 陪伴", "音量 网络 计费 听觉，返回！", "IDLE TALK 0.4.95 （测试）"};
    int y = 4;
    for (size_t i = 0; i < ui_font::kVariantCount; ++i)
        for (const char* text : lines) {
            if (i == 1 && text == lines[3]) continue;
            auto* l = lv_label_create(sheet);
            lv_obj_set_style_text_font(l, &fonts[i].font, 0);
            lv_obj_set_style_text_color(l, lv_color_hex(0x79FF97), 0);
            lv_label_set_text(l, text);
            lv_obj_set_pos(l, 6, y);
            y += fonts[i].font.line_height + 4;
        }
    snapshot(sheet, out + "/font-sample.ppm");

    // The real summary page: with the 16 px font, and the ASCII fallback (no SD font).
    screen_create(lv_screen_active());
    ScreenStatus status;
    status.version = "collector-0.4.95-font";
    status.ip = "192.168.1.255";
    status.ssid = "example";
    status.token = "0123456789abcdef0123456789abcdef";
    screen_update(status);
    screen_update_mode("AUTO", 0x20B8C8, true);
    for (uint32_t t = 0; t < 400; t += 20) { screen_tick(t); lv_tick_inc(20); lv_timer_handler(); }
    screen_touch(true, 1000, 280, 120);
    screen_touch(true, 1080, 60, 122);
    screen_touch(false, 1100);
    for (uint32_t t = 1100; t < 2000; t += 20) { screen_tick(t); lv_tick_inc(20); lv_timer_handler(); }
    assert(screen_showing_status());
    // 0.4.95: the screen takes the font (screen_set_cjk_font) and picks every word itself.
    screen_update_mode_state(uint8_t(modes::rules::Mode::Event), 0);
    screen_set_cjk_font(&fonts[0].font, &fonts[1].font);
    lv_refr_now(display);
    const size_t with_font = lit(104, 4, 208, 26);
    snapshot(lv_screen_active(), out + "/summary-cjk.ppm");
    screen_set_cjk_font(nullptr, nullptr);
    lv_refr_now(display);
    const size_t ascii = lit(104, 4, 208, 26);
    snapshot(lv_screen_active(), out + "/summary-ascii.ppm");
    std::printf("summary title row lit pixels: CJK %zu, ASCII %zu\n", with_font, ascii);
    assert(with_font > 150 && ascii > 60);
    std::puts("font_preview OK");
    return 0;
}
