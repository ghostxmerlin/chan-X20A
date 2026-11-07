/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector {
inline constexpr int kVoiceCount=20;
inline int choose_voice(int requested, uint32_t random, int last)
{
    if (requested<0 || requested>kVoiceCount) return 0;
    if (requested) return requested;
    if (last<1 || last>kVoiceCount) return int(random%kVoiceCount)+1;
    const int candidate=int(random%(kVoiceCount-1))+1;
    return candidate>=last ? candidate+1 : candidate;
}
inline bool voice_asr_open(uint8_t id, bool has_value) { return id==0x10 && !has_value; }
class VoiceCooldown {
public:
    bool allowed(int64_t now) const { return now>=until_; }
    void finished(int64_t now) { until_=now+1200000; }
private:
    int64_t until_=0;
};
}
