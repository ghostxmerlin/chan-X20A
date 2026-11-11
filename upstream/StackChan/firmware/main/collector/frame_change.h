/* SPDX-License-Identifier: MIT */
#pragma once
// Body-camera change detection for the vision uplink (plan 2026-11-11-camera-upload, Task 1).
// A 16×12 luma signature per frame; the uplink sends a frame when it differs enough from the last
// one it sent and at least 10 s have passed, plus the first frame after every connect. No IDF here.
#include "face_pixels.h"
#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace collector {
constexpr int kThumbW = 16, kThumbH = 12;
// A frame "changed" when at least 6 of the 192 cells moved by 30 or more after removing the overall
// brightness change. Tuned on the first live run (2026-11-11): empty room with light and a passing
// shadow 3–4 cells, a person moving at the desk 5–26, someone leaving or arriving 144–170.
constexpr int kFrameChangeCells = 6;
constexpr int kCellChange = 30;
constexpr int64_t kFrameMinGapUs = 10000000;      // at most one frame per 10 s

struct FrameThumb {
    bool valid = false;
    uint8_t cell[kThumbW * kThumbH] = {};
};

// Luma of the pixel at the centre of each cell (RGB: (77R + 150G + 29B) >> 8; YUYV: Y itself).
inline FrameThumb frame_thumb(const uint8_t* src, size_t bytes, int w, int h, FacePixelFormat format)
{
    FrameThumb t;
    if (!src || w <= 0 || h <= 0 || w > 1920 || h > 1920 || format == FacePixelFormat::Unsupported ||
            (format == FacePixelFormat::Yuyv && (w & 1))) return t;
    const int stride = format == FacePixelFormat::Rgb888 ? 3 : 2;
    if (bytes < static_cast<size_t>(w) * h * stride) return t;
    for (int cy = 0; cy < kThumbH; ++cy) for (int cx = 0; cx < kThumbW; ++cx) {
        const int sx = (2 * cx + 1) * w / (2 * kThumbW), sy = (2 * cy + 1) * h / (2 * kThumbH);
        const uint8_t* p = src + (static_cast<size_t>(sy) * w + sx) * stride;
        int r, g, b;
        if (format == FacePixelFormat::Yuyv) {
            t.cell[cy * kThumbW + cx] = p[0];          // even or odd pixel: both bytes 0 and 2 of a pair are Y
            continue;
        } else if (format == FacePixelFormat::Rgb565) {
            const uint16_t v = p[0] | (static_cast<uint16_t>(p[1]) << 8);
            r = ((v >> 11) & 31) * 255 / 31; g = ((v >> 5) & 63) * 255 / 63; b = (v & 31) * 255 / 31;
        } else {
            r = p[0]; g = p[1]; b = p[2];
        }
        t.cell[cy * kThumbW + cx] = static_cast<uint8_t>((77 * r + 150 * g + 29 * b) >> 8);
    }
    t.valid = true;
    return t;
}

// Mean absolute difference per cell, rounded; 255 when either side has no signature.
inline int frame_diff(const FrameThumb& a, const FrameThumb& b)
{
    if (!a.valid || !b.valid) return 255;
    int sum = 0;
    for (int i = 0; i < kThumbW * kThumbH; ++i) sum += std::abs(static_cast<int>(a.cell[i]) - b.cell[i]);
    return (sum + kThumbW * kThumbH / 2) / (kThumbW * kThumbH);
}

// Cells that changed by >= kCellChange once each signature's mean is removed; 255 when either is invalid.
inline int frame_changed_cells(const FrameThumb& a, const FrameThumb& b)
{
    if (!a.valid || !b.valid) return 255;
    constexpr int n = kThumbW * kThumbH;
    int sa = 0, sb = 0;
    for (int i = 0; i < n; ++i) { sa += a.cell[i]; sb += b.cell[i]; }
    int changed = 0;
    for (int i = 0; i < n; ++i)    // compare (a − mean a) with (b − mean b), all scaled by n to stay integer
        if (std::abs((a.cell[i] * n - sa) - (b.cell[i] * n - sb)) >= kCellChange * n) ++changed;
    return changed;
}

// `changed`: frame_changed_cells against the last frame sent.
inline bool frame_should_send(bool have_last, int changed, int64_t since_last_us)
{
    return !have_last || (since_last_us >= kFrameMinGapUs && changed >= kFrameChangeCells);
}

// A frame send that failed (slow uplink, socket closed) holds frames for 60 s, so a link that cannot
// carry a frame does not reconnect every 2 s re-sending it; M12 events keep flowing meanwhile.
constexpr int64_t kFrameFailHoldUs = 60000000;
inline bool frame_held(int64_t now_us, int64_t hold_until_us) { return now_us < hold_until_us; }

// WebSocket send budget: 1 s for small messages, 3 s for frames (a 32 KB frame needs several RTTs
// through the 5.7 KB TCP send buffer).
inline int frame_send_budget_ms(size_t bytes) { return bytes > 8192 ? 3000 : 1000; }
}
