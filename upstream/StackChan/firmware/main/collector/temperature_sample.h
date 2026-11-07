/* SPDX-License-Identifier: MIT */
#pragma once
#include <cmath>
#include <cstdint>

namespace collector {
struct TemperatureSample {
    bool available = false, valid = false;
    float celsius = 0;
    uint32_t seq = 0;
    int64_t timestamp_us = 0;
    int error = 0;
    void update(float value, int read_error, int64_t now_us) {
        ++seq; timestamp_us = now_us; celsius = value; error = read_error;
        valid = read_error == 0 && std::isfinite(value) && value >= 20 && value <= 100;
        if (!valid && !error) error = -1;
    }
    bool fresh(int64_t now_us) const {
        return valid && timestamp_us > 0 && now_us >= timestamp_us && now_us - timestamp_us < 3000000;
    }
};
}
