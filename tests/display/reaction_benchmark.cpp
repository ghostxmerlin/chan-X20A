#include <collector/screen.h>
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <vector>

namespace hal_bridge { void board_set_display_backlight_enabled(bool) {} }
namespace {
size_t pixels, flushes;
void refresh(const char* phase, unsigned iteration)
{
    pixels = flushes = 0;
    const auto start = std::chrono::steady_clock::now();
    lv_refr_now(nullptr);
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - start).count();
    assert(pixels > 0 && pixels <= 260 * 240); // unchanged black margins stay idle
    std::printf("%s,%u,%lld,%zu,%zu\n", phase, iteration,
                static_cast<long long>(elapsed), pixels, flushes);
}
}
int main()
{
    lv_init();
    auto* display = lv_display_create(320, 240);
    std::vector<uint8_t> buffer(320 * 20 * 2);
    lv_display_set_buffers(display, buffer.data(), nullptr, buffer.size(), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t* area, uint8_t*) {
        pixels += lv_area_get_size(area); ++flushes; lv_display_flush_ready(d);
    });
    collector::screen_create(lv_screen_active());
    collector::screen_tick(0);
    lv_refr_now(display);
    std::puts("phase,iteration,host_us,pixels,flushes");
    // This is host renderer cost only: immediate flush acknowledgement models
    // the board's tile size, but not its CPU, SPI transfer, or competing tasks.
    for (unsigned i = 0; i < 30; ++i) {
        const uint32_t now = 100 + i * 4000;
        collector::screen_show_reaction(collector::ScreenReaction::Heart, now);
        refresh("heart", i);
        collector::screen_show_reaction(collector::ScreenReaction::Reject, now + 1100);
        refresh("reject", i);
        collector::screen_tick(now + 2200);
        refresh("hide", i);
    }
    lv_deinit();
}
