/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <cstdint>
namespace collector {
// Speaker volume for every playback (voice clips, reactions, HTTP PCM). Persisted in NVS
// collector/volume; changed from the web (POST /v1/control {"target":"volume","value":N})
// (the Unit ASR's volume words 0x40..0x44 are gone since 2026-11-20: the StickS3 has no factory words).
constexpr int kVolumeDefault=80,kVolumeMin=10,kVolumeMax=100,kVolumeStep=10,kVolumeMedium=60;
inline bool volume_valid(int v){return v>=kVolumeMin&&v<=kVolumeMax;}
}
