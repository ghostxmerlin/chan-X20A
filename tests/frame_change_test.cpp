#include "../upstream/StackChan/firmware/main/collector/frame_change.h"
#include <cassert>
#include <cstdio>
#include <vector>
int main()
{
    using namespace collector;
    const int w = 32, h = 24;
    // RGB888 grey 0x80 everywhere, then 20 brighter
    std::vector<uint8_t> a(w * h * 3, 0x80), b(w * h * 3, 0x80 + 20);
    const FrameThumb ta = frame_thumb(a.data(), a.size(), w, h, FacePixelFormat::Rgb888);
    const FrameThumb tb = frame_thumb(b.data(), b.size(), w, h, FacePixelFormat::Rgb888);
    assert(ta.valid && tb.valid);
    assert(frame_diff(ta, ta) == 0);
    assert(frame_diff(ta, tb) == 20);
    // RGB565 little-endian bytes: white 0xFFFF and black 0x0000
    std::vector<uint8_t> white(w * h * 2, 0xff), black(w * h * 2, 0);
    const FrameThumb tw = frame_thumb(white.data(), white.size(), w, h, FacePixelFormat::Rgb565);
    const FrameThumb tk = frame_thumb(black.data(), black.size(), w, h, FacePixelFormat::Rgb565);
    assert(tw.valid && tw.cell[0] >= 250 && tw.cell[191] >= 250);
    assert(tk.valid && tk.cell[0] == 0);
    // YUYV with Y = 100: the cell is the luma itself
    std::vector<uint8_t> yuyv(w * h * 2);
    for (size_t i = 0; i < yuyv.size(); i += 2) { yuyv[i] = 100; yuyv[i + 1] = 128; }
    const FrameThumb ty = frame_thumb(yuyv.data(), yuyv.size(), w, h, FacePixelFormat::Yuyv);
    assert(ty.valid && ty.cell[0] == 100 && ty.cell[100] == 100);
    // too few bytes, unsupported formats, bad sizes
    assert(!frame_thumb(a.data(), a.size() - 1, w, h, FacePixelFormat::Rgb888).valid);
    assert(!frame_thumb(a.data(), a.size(), w, h, FacePixelFormat::Unsupported).valid);
    assert(!frame_thumb(nullptr, 0, w, h, FacePixelFormat::Rgb888).valid);
    assert(!frame_thumb(a.data(), a.size(), 0, h, FacePixelFormat::Rgb888).valid);
    const FrameThumb bad{};
    assert(frame_diff(ta, bad) == 255 && frame_diff(bad, ta) == 255);
    // policy: first frame after connecting, 10 s gap, change threshold
    assert(frame_should_send(false, 0, 0));
    assert(!frame_should_send(true, 30, 9999999));
    assert(!frame_should_send(true, 5, 60000000));
    assert(frame_should_send(true, 6, 10000000));
    assert(kFrameChangeThreshold == 6 && kFrameMinGapUs == 10000000);
    // a failed frame send holds frames for 60 s (no reconnect storm); big messages get 3 s to send
    assert(kFrameFailHoldUs == 60000000);
    assert(frame_held(1000, 2000) && !frame_held(2000, 2000) && !frame_held(5, 0));
    assert(frame_send_budget_ms(8192) == 1000 && frame_send_budget_ms(8193) == 3000 && frame_send_budget_ms(100) == 1000);
    puts("PASS: thumb per format, diff, bounds, upload policy, failure hold and send budget");
}
