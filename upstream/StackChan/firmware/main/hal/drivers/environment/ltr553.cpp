/* SPDX-License-Identifier: MIT */
#include "ltr553.h"
#include <cmath>

namespace environment {
// LTR-553ALS-WA register definitions: Lite-On datasheet rev 1.0 (2013),
// distributed by M5Stack with CoreS3. Lux coefficients verified against the
// Zephyr LTR553 primary implementation (ltr55x_channel_light_get):
// https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/sensor/liteon/ltr55x/ltr55x.c
// The conversion has no assembled-device cover/window calibration factor.
bool ltr553_lux(uint16_t ch0, uint16_t ch1, uint8_t gain, uint16_t integration_ms, float& lux)
{
    if (gain == 0 || integration_ms == 0 || ch0 == 65535 || ch1 == 65535) return false;
    if (ch0 == 0 && ch1 == 0) { lux = 0; return true; }
    const float ratio = float(ch1) / (uint32_t(ch0) + ch1);
    float value;
    if (ratio < 0.45f) value = 1.7743f * ch0 + 1.1059f * ch1;
    else if (ratio < 0.64f) value = 4.2785f * ch0 - 1.9548f * ch1;
    else if (ratio < 0.85f) value = 0.5926f * ch0 + 0.1185f * ch1;
    else return false; // IR dominated: do not label an out-of-model reading 0 lx.
    value /= float(gain) * (float(integration_ms) / 100.0f);
    if (!std::isfinite(value) || value < 0) return false;
    lux = value;
    return true;
}

bool Ltr553::fail_both(Error error)
{
    fail(light_, error); fail(proximity_, error);
    return false;
}
bool Ltr553::configure(uint8_t reg, uint8_t value)
{
    if (!io_.write(io_.context, reg, value)) return fail_both(Error::I2c);
    uint8_t actual = 0;
    if (!io_.read(io_.context, reg, &actual, 1)) return fail_both(Error::I2c);
    if (actual != value) return fail_both(Error::Config);
    return true;
}
bool Ltr553::begin()
{
    ++initialization_attempts_;
    light_.available = proximity_.available = false;
    uint8_t id[2]{};
    if (!io_.read(io_.context, 0x86, id, sizeof(id))) return fail_both(Error::I2c);
    light_.chip_id = proximity_.chip_id = id[0];
    light_.manufacturer_id = proximity_.manufacturer_id = id[1];
    if (id[0] != 0x92 || id[1] != 0x05) return fail_both(Error::ChipId);
    // Stop both measurement engines before changing integration/rate/gain.
    // Fixed 1x / 100 ms avoids gain-change artifacts and keeps raw counts useful.
    if (!configure(0x80, 0) || !configure(0x81, 0) ||
        !configure(0x8f, 0) || // no interrupts; data-ready polling
        !configure(0x82, 0x7a) || // 60 kHz, 100% duty, 20 mA LED peak
        !configure(0x83, 4) || !configure(0x84, 2) || // four pulses / 100 ms PS
        !configure(0x85, 1) || // ALS integration/repeat = 100 ms
        !configure(0x80, 1) || // ALS active, gain 1x
        !configure(0x81, 0x22)) { // PS active, gain 16x, saturation indicator on
        // Best-effort standby after partial setup; never publish availability.
        io_.write(io_.context, 0x80, 0); io_.write(io_.context, 0x81, 0);
        return false;
    }
    light_.available = proximity_.available = true;
    // No measurement has completed yet. Do not create a valid zero reading.
    light_.error = proximity_.error = Error::NotInitialized;
    return true;
}
bool Ltr553::initialize()
{
    for (unsigned attempt = 0; attempt < 3; ++attempt) {
        if (begin()) return true;
        if (light_.error != Error::I2c) return false;
        if (attempt < 2 && io_.wait_ms) io_.wait_ms(io_.context, 100);
    }
    return false;
}
void Ltr553::poll()
{
    if (!light_.available || !proximity_.available) return;
    uint8_t status = 0;
    if (!io_.read(io_.context, 0x8c, &status, 1)) { fail_both(Error::I2c); return; }
    if (status & 0x04) {
        uint8_t data[4]{};
        // Read CH1 then CH0 atomically; the chip locks all 4 until 0x8B is read.
        if (!io_.read(io_.context, 0x88, data, sizeof(data))) fail(light_, Error::I2c);
        else {
            light_.raw_ch1 = data[0] | (uint16_t(data[1]) << 8);
            light_.raw_ch0 = data[2] | (uint16_t(data[3]) << 8);
            constexpr uint8_t gains[] = {1, 2, 4, 8, 0, 0, 48, 96};
            light_.gain = gains[(status >> 4) & 7];
            light_.saturated = light_.raw_ch0 == 65535 || light_.raw_ch1 == 65535;
            acquired(light_, io_.now_us(io_.context));
            light_.valid = !(status & 0x80) && !light_.saturated &&
                ltr553_lux(light_.raw_ch0, light_.raw_ch1, light_.gain, 100, light_.lux);
            light_.error = light_.valid ? Error::None : light_.saturated ? Error::Saturated : Error::Invalid;
        }
    } else if (status & 0x80) fail(light_, Error::Invalid);
    if (status & 0x01) {
        uint8_t data[2]{};
        if (!io_.read(io_.context, 0x8d, data, sizeof(data))) fail(proximity_, Error::I2c);
        else {
            proximity_.raw = data[0] | (uint16_t(data[1] & 7) << 8);
            proximity_.saturated = (data[1] & 0x80) != 0;
            acquired(proximity_, io_.now_us(io_.context));
            proximity_.valid = !proximity_.saturated;
            proximity_.error = proximity_.valid ? Error::None : Error::Saturated;
        }
    }
    const int64_t now = io_.now_us(io_.context);
    expire(light_, now); expire(proximity_, now);
}
} // namespace environment
