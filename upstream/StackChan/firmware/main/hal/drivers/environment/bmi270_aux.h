/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>

namespace environment {
// Init-only manual AUX reader. The bundled Bosch read_aux_data keeps len
// unchanged after a failed read, so it can loop forever on an optional sensor.
// Keep that dependency untouched; use bounded transactions for our new path.
// Manual burst length must have been configured to 8 bytes.
template <typename Read, typename Write, typename Wait>
bool bmi270_read_aux(uint8_t address, uint8_t* output, size_t size, Read read, Write write, Wait wait)
{
    auto idle = [&]() {
        for (unsigned attempt = 0; attempt < 8; ++attempt) {
            uint8_t status = 0;
            if (!read(0x03, &status, 1)) return false; // BMI270 STATUS
            if (!(status & 0x04)) return true; // aux_busy
            wait();
        }
        return false;
    };
    for (size_t offset = 0; offset < size; offset += 8) {
        if (!idle() || !write(0x4d, uint8_t(address + offset))) return false; // AUX_RD_ADDR
        wait(); // allow the AUX I2C transaction to start/complete
        if (!idle()) return false;
        uint8_t bytes[8]{};
        if (!read(0x04, bytes, sizeof(bytes))) return false; // AUX_DATA
        const size_t count = size - offset < sizeof(bytes) ? size - offset : sizeof(bytes);
        for (size_t i = 0; i < count; ++i) output[offset + i] = bytes[i];
    }
    return true;
}
} // namespace environment
