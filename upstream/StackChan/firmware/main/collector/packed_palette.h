/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>
namespace collector {
inline uint8_t palette5(const uint8_t* frame,size_t pixel) {
    const size_t bit=pixel*5,byte=bit/8;
    const unsigned shift=bit%8;
    uint16_t value=frame[byte];
    if(shift>3)value|=static_cast<uint16_t>(frame[byte+1])<<8;
    return (value>>shift)&31;
}
}
