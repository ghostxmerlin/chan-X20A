/* SPDX-License-Identifier: MIT */
#ifndef COLLECTOR_CENTER_PACKET_H
#define COLLECTOR_CENTER_PACKET_H
#include <stdint.h>

// Preserve the official first eight bytes; the extension distinguishes the
// physical center button from BtnB and makes sender restarts safe to baseline.
static inline void center_packet_extension(uint8_t packet[16], uint32_t boot_nonce, uint32_t press_sequence)
{
    for (unsigned i = 0; i < 4; ++i) {
        packet[8 + i] = (uint8_t)(boot_nonce >> (8 * i));
        packet[12 + i] = (uint8_t)(press_sequence >> (8 * i));
    }
}
#endif
