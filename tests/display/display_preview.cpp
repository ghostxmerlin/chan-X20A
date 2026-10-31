#include <collector/screen.h>
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

size_t flushed_pixels = 0, flush_calls = 0;

void snapshot(const std::string& path)
{
    lv_obj_update_layout(lv_screen_active());
    auto buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB888);
    assert(buf && buf->header.w == 320 && buf->header.h == 240);
    auto file = fopen(path.c_str(), "wb");
    assert(file);
    fprintf(file, "P6\n320 240\n255\n");
    for (int y = 0; y < 240; ++y) for (int x = 0; x < 320; ++x) {
        const auto p = buf->data + y * buf->header.stride + x * 3;
        const uint8_t rgb[] = {p[2], p[1], p[0]};
        fwrite(rgb, 1, 3, file);
    }
    fclose(file);
    lv_draw_buf_destroy(buf);
}
int main(int argc, char** argv)
{
    assert(argc == 2);
    lv_init();
    auto display = lv_display_create(320, 240);
    std::vector<uint8_t> framebuffer(320 * 20 * 2);
    lv_display_set_buffers(display, framebuffer.data(), nullptr, framebuffer.size(), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t* area, uint8_t*) {
        flushed_pixels += lv_area_get_size(area); ++flush_calls; lv_display_flush_ready(d);
    });
    collector::screen_create(lv_screen_active());
    collector::screen_update({"collector-0.3.6", "192.168.1.255", "0123456789abcdef0123456789abcdef", "READY", 6, 123456, true, true});
    assert(!collector::screen_showing_status());
    collector::screen_tick(0);
    snapshot(std::string(argv[1]) + "/home.ppm");
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_tick(3000);
    lv_tick_inc(100); lv_timer_handler();
    // A changing lettering ring must not flush the entire 62,400-pixel image.
    assert(flushed_pixels > 0 && flushed_pixels <= 14000 && flush_calls <= 28);
    printf("PASS: one animation redraw flushes %zu pixels in %zu regions\n", flushed_pixels, flush_calls);
    snapshot(std::string(argv[1]) + "/home-quarter.ppm");
    collector::screen_tick(6000);
    snapshot(std::string(argv[1]) + "/home-half.ppm");
    collector::screen_tick(9000);
    snapshot(std::string(argv[1]) + "/home-three-quarter.ppm");
    collector::screen_tick(12000);
    snapshot(std::string(argv[1]) + "/home-lap.ppm");
    // Also render a continuous cycle for animation inspection.
    for (uint32_t frame = 0; frame < 120; ++frame) {
        collector::screen_tick(12000 + frame * 100);
        char name[64]; snprintf(name, sizeof(name), "/animation-%03lu.ppm", static_cast<unsigned long>(frame));
        snapshot(std::string(argv[1]) + name);
    }
    // Use a fresh monotonic touch clock after the animation samples.
    constexpr uint32_t offset = 24000;
    collector::screen_touch(true, offset + 1000);
    collector::screen_touch(true, offset + 1080);
    assert(!collector::screen_showing_status());
    collector::screen_touch(false, offset + 1100);
    assert(collector::screen_showing_status());
    snapshot(std::string(argv[1]) + "/terminal.ppm");
    collector::screen_tick(offset + 1800);
    snapshot(std::string(argv[1]) + "/terminal-paused.ppm");
    // Holding and contact bounce must not repeatedly toggle the UI.
    collector::screen_touch(true, offset + 1850);
    collector::screen_touch(false, offset + 1855);
    assert(collector::screen_showing_status());
    collector::screen_touch(true, offset + 2000);
    collector::screen_touch(false, offset + 3000);
    assert(collector::screen_showing_status());
    collector::screen_touch(true, offset + 3500);
    collector::screen_touch(false, offset + 3600);
    assert(!collector::screen_showing_status());
    puts("PASS: LVGL animation cycle, boot home, tap/release, bounce, hold, return");
    lv_deinit();
}
