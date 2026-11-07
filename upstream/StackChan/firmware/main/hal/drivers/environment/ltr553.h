/* SPDX-License-Identifier: MIT */
#pragma once
#include "sensor_sample.h"
#include <cstddef>

namespace environment {
struct LightSample : Sample {
    int chip_id = -1, manufacturer_id = -1;
    uint16_t raw_ch0 = 0, raw_ch1 = 0;
    uint8_t gain = 0;
    bool saturated = false;
    float lux = 0;
};
struct ProximitySample : Sample {
    int chip_id = -1, manufacturer_id = -1;
    uint16_t raw = 0;
    bool saturated = false;
};
// Uses the board's existing IDF master bus. The adapter uses a bounded 50 ms
// IDF timeout per init/runtime transaction; no second I2C driver,
// asynchronous callbacks, allocation or per-poll logging.
struct RegisterIo {
    void* context;
    bool (*read)(void*, uint8_t, uint8_t*, size_t);
    bool (*write)(void*, uint8_t, uint8_t);
    int64_t (*now_us)(void*);
    void (*wait_ms)(void*, uint32_t) = nullptr;
};
bool ltr553_lux(uint16_t ch0, uint16_t ch1, uint8_t gain, uint16_t integration_ms, float& lux);
class Ltr553 {
public:
    explicit Ltr553(RegisterIo io) : io_(io) {}
    // Caller waits at least 100 ms after power-on; initialization is once-only.
    bool begin();
    // Only transient I/O errors may retry, at most 3 initial attempts with
    // 100 ms yielding backoff. Unknown IDs/config mismatches never retry.
    bool initialize();
    unsigned initialization_attempts() const { return initialization_attempts_; }
    void poll();
    const LightSample& light() const { return light_; }
    const ProximitySample& proximity() const { return proximity_; }
private:
    bool configure(uint8_t reg, uint8_t value);
    bool fail_both(Error error);
    RegisterIo io_;
    LightSample light_;
    ProximitySample proximity_;
    unsigned initialization_attempts_ = 0;
};
} // namespace environment
