/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace collector {
// IMA WAV mono blocks produced by the local converter, 256 bytes / 505 samples.
// Trim only the final padded block to the original PCM length. No allocation,
// container probing or hardware writes; reject malformed headers before playback.
inline bool decode_voice_ima(const uint8_t* data, size_t bytes, int16_t* out, size_t frames)
{
    if (!data || !out || !frames || frames > 192000 || !bytes || bytes % 256 ||
        (frames + 504) / 505 != bytes / 256) return false;
    static constexpr int steps[] = {
        7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,
        73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,
        408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,
        1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,
        6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,
        20350,22385,24623,27086,29794,32767};
    static constexpr int indices[] = {-1,-1,-1,-1,2,4,6,8};
    size_t written = 0;
    for (size_t at=0; at<bytes; at+=256) {
        int index=data[at+2];
        if (index>88 || data[at+3]!=0) return false;
        int value=static_cast<int16_t>(uint16_t(data[at]) | uint16_t(data[at+1])<<8);
        out[written++]=value;
        for (size_t b=at+4; b<at+256 && written<frames; ++b) {
            for (int shift : {0,4}) {
                const int nibble=(data[b]>>shift)&15;
                const int delta=((2*(nibble&7)+1)*steps[index])>>3;
                value=std::clamp(value+((nibble&8)?-delta:delta),-32768,32767);
                index=std::clamp(index+indices[nibble&7],0,88);
                out[written++]=value;
                if (written==frames) break;
            }
        }
    }
    return written==frames;
}
}
