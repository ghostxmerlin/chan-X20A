#include <collector/screen.h>
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

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
    std::vector<uint8_t> framebuffer(320 * 240 * 2);
    lv_display_set_buffers(display, framebuffer.data(), nullptr, framebuffer.size(), LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t*, uint8_t*) { lv_display_flush_ready(d); });
    collector::screen_create(lv_screen_active());
    collector::screen_update({"collector-0.3.5", "192.168.31.255", "0123456789abcdef0123456789abcdef", "READY", 6, 123456, true, true});
    assert(!collector::screen_showing_status());
    snapshot(std::string(argv[1]) + "/home.ppm");
    collector::screen_touch(true, 1000);
    collector::screen_touch(true, 1080);
    assert(!collector::screen_showing_status());
    collector::screen_touch(false, 1100);
    assert(collector::screen_showing_status());
    snapshot(std::string(argv[1]) + "/terminal.ppm");
    // Holding and contact bounce must not repeatedly toggle the UI.
    collector::screen_touch(true, 1150);
    collector::screen_touch(false, 1155);
    assert(collector::screen_showing_status());
    collector::screen_touch(true, 1500);
    collector::screen_touch(false, 2500);
    assert(collector::screen_showing_status());
    collector::screen_touch(true, 3000);
    collector::screen_touch(false, 3100);
    assert(!collector::screen_showing_status());
    puts("PASS: actual LVGL renders; boot home, tap/release, bounce, hold, return");
    lv_deinit();
}
