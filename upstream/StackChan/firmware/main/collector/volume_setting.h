/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <cstdint>
namespace collector {
// Speaker volume for every playback (voice clips, reactions, HTTP PCM). Persisted in NVS
// collector/volume; changed from the web (POST /v1/control {"target":"volume","value":N})
// or on the device with the Unit ASR factory commands 0x40..0x44 (user, 2026-11-09).
constexpr int kVolumeDefault=80,kVolumeMin=10,kVolumeMax=100,kVolumeStep=10,kVolumeMedium=60;
inline bool volume_valid(int v){return v>=kVolumeMin&&v<=kVolumeMax;}
// New volume for an ASR command, or -1 if the command is not a volume command.
inline int volume_for_asr(uint8_t id,bool has_value,int current) {
    if(has_value)return -1;
    switch(id) {
        case 0x40: return std::min(kVolumeMax,std::max(kVolumeMin,current)+kVolumeStep); // increase volume
        case 0x41: return std::max(kVolumeMin,std::min(kVolumeMax,current)-kVolumeStep); // decrease volume
        case 0x42: return kVolumeMax;                                                    // maximum volume
        case 0x43: return kVolumeMedium;                                                 // medium volume
        case 0x44: return kVolumeMin;                                                    // minimum volume
    }
    return -1;
}
}
