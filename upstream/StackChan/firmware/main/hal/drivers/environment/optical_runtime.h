/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace environment {
// This is the IDF API timeout, not an absolute task or end-to-end deadline.
// At 100 Hz, 50 ms gives a contended shared bus more than one tick to finish.
constexpr int kOpticalTransactionTimeoutMs = 50;

struct OpticalRuntimeDiagnostic {
    uint32_t transactions = 0, errors = 0;
    int last_i2c_error = 0, last_i2c_register = -1;
    bool last_i2c_write = false;
    int64_t last_error_timestamp_us = 0;

    // Call only after initialization. A successful recovery keeps the previous
    // failure and its own time; it must not make an old failure appear fresh.
    bool observe(int error, uint8_t reg, bool write, int64_t now_us)
    {
        ++transactions;
        if (error != 0) {
            ++errors;
            last_i2c_error = error;
            last_i2c_register = reg;
            last_i2c_write = write;
            last_error_timestamp_us = now_us;
        }
        return error == 0;
    }
};

// Keep periodic normal sampling, but never run a burst of catch-up polls after
// slow/failed I/O. An overdue period still yields one tick, then starts a new
// schedule. The injected operations allow the same policy to be host-tested.
template <class Tick, class DelayUntil, class Delay, class Now>
void wait_for_optical_poll(Tick& wake, Tick period, DelayUntil delay_until, Delay delay, Now now)
{
    if (!delay_until(&wake, period)) {
        delay(Tick{1});
        wake = now();
    }
}
} // namespace environment
