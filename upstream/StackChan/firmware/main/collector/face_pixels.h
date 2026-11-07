/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace collector {
constexpr int kFaceWidth = 160, kFaceHeight = 120;
constexpr int kFaceTargetFps = 4;
constexpr int64_t kFacePeriodUs = 1000000 / kFaceTargetFps;
constexpr size_t kFaceMaxBoxes = 4;
enum class FacePixelFormat { Yuyv, Rgb565, Rgb888, Unsupported };
inline uint8_t face_channel(int value) { return static_cast<uint8_t>(std::clamp(value, 0, 255)); }
// Small RGB input; nearest-neighbour samples preserve the full 4:3 camera field.
inline bool face_pixels(const uint8_t* src, size_t bytes, int w, int h, FacePixelFormat format, uint8_t* rgb)
{
    if (!src || !rgb || w <= 0 || h <= 0 || w > 1920 || h > 1920 ||
            format == FacePixelFormat::Unsupported || (format == FacePixelFormat::Yuyv && (w & 1))) return false;
    const int stride = format == FacePixelFormat::Rgb888 ? 3 : 2;
    if (bytes < static_cast<size_t>(w) * h * stride) return false;
    for (int y = 0; y < kFaceHeight; ++y) for (int x = 0; x < kFaceWidth; ++x) {
        int sx = x * w / kFaceWidth, sy = y * h / kFaceHeight;
        auto dst = rgb + (y * kFaceWidth + x) * 3;
        if (format == FacePixelFormat::Yuyv) {
            const auto p = src + (sy * w + (sx & ~1)) * 2;
            const int c = std::max(0, static_cast<int>(p[(sx & 1) * 2]) - 16), u = p[1] - 128, v = p[3] - 128;
            dst[0] = face_channel((298 * c + 409 * v + 128) >> 8);
            dst[1] = face_channel((298 * c - 100 * u - 208 * v + 128) >> 8);
            dst[2] = face_channel((298 * c + 516 * u + 128) >> 8);
        } else if (format == FacePixelFormat::Rgb565) {
            const auto p = src + (sy * w + sx) * 2;
            const uint16_t value = p[0] | (static_cast<uint16_t>(p[1]) << 8);
            dst[0] = ((value >> 11) & 31) * 255 / 31;
            dst[1] = ((value >> 5) & 63) * 255 / 63;
            dst[2] = (value & 31) * 255 / 31;
        } else {
            const auto p = src + (sy * w + sx) * 3;
            std::copy(p, p + 3, dst);
        }
    }
    return true;
}
}
