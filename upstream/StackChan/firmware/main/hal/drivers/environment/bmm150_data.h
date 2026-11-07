/* SPDX-License-Identifier: MIT */
#pragma once
#include "sensor_sample.h"

namespace environment {
struct Bmm150Trim {
    int8_t x1 = 0, y1 = 0, x2 = 0, y2 = 0, xy2 = 0;
    uint8_t xy1 = 0;
    uint16_t z1 = 0, xyz1 = 0;
    int16_t z2 = 0, z3 = 0, z4 = 0;
    bool valid() const { return z1 != 0 && z2 != 0 && xyz1 != 0; }
};
struct MagneticSample : Sample {
    int chip_id = -1;
    int16_t raw[3]{};
    uint16_t rhall = 0;
    bool overflow = false;
    float field_ut[3]{};
};
Bmm150Trim bmm150_trim(const uint8_t x1y1[2], const uint8_t z4xy2[4], const uint8_t rest[10]);
bool bmm150_compensate(const Bmm150Trim& trim, const int16_t raw[3], uint16_t rhall, float field_ut[3]);
// BMI270 STATUS.drdy_aux and the BMM150 DATA.rhall.data_ready must both be set.
// Re-reading a cached BMI270 AUX block must never manufacture a new sample.
void bmm150_consume(MagneticSample& sample, const Bmm150Trim& trim, uint8_t bmi_status,
                    const uint8_t aux[8], int64_t now_us);
} // namespace environment
