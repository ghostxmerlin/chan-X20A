/**
 * Copyright (c) 2020 Bosch Sensortec GmbH. All rights reserved.
 *
 * BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 *    contributors may be used to endorse or promote products derived from
 *    this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
 * STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
 * IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */
// Compensation equations/trim decoding adapted from Bosch BMM150 SensorAPI v2.0.0:
// https://github.com/boschsensortec/BMM150_SensorAPI/blob/master/bmm150.c
// This is factory sensitivity/temperature compensation, NOT assembled robot
// hard-/soft-iron calibration or a calibrated heading.
#include "bmm150_data.h"
#include <cmath>

namespace environment {
Bmm150Trim bmm150_trim(const uint8_t a[2], const uint8_t b[4], const uint8_t c[10])
{
    Bmm150Trim t;
    t.x1 = static_cast<int8_t>(a[0]); t.y1 = static_cast<int8_t>(a[1]);
    t.x2 = static_cast<int8_t>(b[2]); t.y2 = static_cast<int8_t>(b[3]);
    t.z4 = static_cast<int16_t>(b[0] | (uint16_t(b[1]) << 8));
    t.z2 = static_cast<int16_t>(c[0] | (uint16_t(c[1]) << 8));
    t.z1 = c[2] | (uint16_t(c[3]) << 8);
    t.xyz1 = c[4] | (uint16_t(c[5] & 0x7f) << 8);
    t.z3 = static_cast<int16_t>(c[6] | (uint16_t(c[7]) << 8));
    t.xy2 = static_cast<int8_t>(c[8]); t.xy1 = c[9];
    return t;
}

bool bmm150_compensate(const Bmm150Trim& t, const int16_t raw[3], uint16_t rhall, float output[3])
{
    if (!t.valid() || rhall == 0 || raw[0] == -4096 || raw[1] == -4096 || raw[2] == -16384) return false;
    const float ratio = float(t.xyz1) * 16384.0f / rhall - 16384.0f;
    const float xy = float(t.xy2) * (ratio * ratio / 268435456.0f) + ratio * float(t.xy1) / 16384.0f;
    const float x = (raw[0] * ((xy + 256.0f) * (float(t.x2) + 160.0f)) / 8192.0f + float(t.x1) * 8.0f) / 16.0f;
    const float y = (raw[1] * ((xy + 256.0f) * (float(t.y2) + 160.0f)) / 8192.0f + float(t.y1) * 8.0f) / 16.0f;
    const float divisor = float(t.z2) + float(t.z1) * float(rhall) / 32768.0f;
    if (divisor == 0.0f) return false;
    const float z = ((float(raw[2]) - float(t.z4)) * 131072.0f -
                     float(t.z3) * (float(rhall) - float(t.xyz1))) / (divisor * 4.0f) / 16.0f;
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) return false;
    output[0] = x; output[1] = y; output[2] = z;
    return true;
}

void bmm150_consume(MagneticSample& sample, const Bmm150Trim& trim, uint8_t status,
                    const uint8_t aux[8], int64_t now_us)
{
    if (!sample.available) return;
    if (!(status & 0x20) || !(aux[6] & 1)) { expire(sample, now_us); return; }
    // Multiplication instead of left shifting negative signed values.
    sample.raw[0] = static_cast<int8_t>(aux[1]) * 32 + (aux[0] >> 3);
    sample.raw[1] = static_cast<int8_t>(aux[3]) * 32 + (aux[2] >> 3);
    sample.raw[2] = static_cast<int8_t>(aux[5]) * 128 + (aux[4] >> 1);
    sample.rhall = (uint16_t(aux[7]) << 6) | (aux[6] >> 2);
    sample.overflow = sample.raw[0] == -4096 || sample.raw[1] == -4096 || sample.raw[2] == -16384;
    acquired(sample, now_us);
    sample.valid = bmm150_compensate(trim, sample.raw, sample.rhall, sample.field_ut);
    sample.error = sample.valid ? Error::None : sample.overflow ? Error::Overflow : Error::Invalid;
}
} // namespace environment
