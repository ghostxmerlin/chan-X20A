/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace environment {
constexpr int64_t kFreshUs = 1000000;

enum class Error : uint8_t {
    None, NotInitialized, ChipId, I2c, Config, Trim, Invalid, Overflow, Saturated, Stale
};
inline const char* error_name(Error error)
{
    switch (error) {
        case Error::None: return "none";
        case Error::NotInitialized: return "not_initialized";
        case Error::ChipId: return "chip_id";
        case Error::I2c: return "i2c";
        case Error::Config: return "config";
        case Error::Trim: return "trim";
        case Error::Invalid: return "invalid";
        case Error::Overflow: return "overflow";
        case Error::Saturated: return "saturated";
        case Error::Stale: return "stale";
    }
    return "invalid";
}
struct Sample {
    bool available = false;
    bool valid = false;
    bool has_raw = false;
    uint32_t seq = 0;
    int64_t timestamp_us = 0;
    Error error = Error::NotInitialized;
};
inline void fail(Sample& sample, Error error)
{
    sample.valid = false;
    sample.error = error;
}
inline void expire(Sample& sample, int64_t now_us)
{
    if (sample.valid && (now_us < sample.timestamp_us || now_us - sample.timestamp_us >= kFreshUs)) {
        fail(sample, Error::Stale);
    }
}
// Only call after a complete, newly available hardware measurement was read.
// Invalid/overflow measurements still retain their raw counts and acquisition time.
inline void acquired(Sample& sample, int64_t now_us)
{
    sample.has_raw = true;
    sample.timestamp_us = now_us;
    ++sample.seq;
}
} // namespace environment
