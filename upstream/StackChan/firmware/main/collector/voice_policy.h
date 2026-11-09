/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector {
inline constexpr int kVoiceCount=80;
inline constexpr int kAsrVoiceStartDelayMs=800;
inline int64_t voice_not_before(bool from_asr, int64_t accepted_us)
{ return accepted_us+(from_asr ? kAsrVoiceStartDelayMs*1000 : 0); }
// Called only by the playback worker, never by the UART receive task. The
// existing playback gate remains reserved while this yields to other tasks.
template<class Clock> void wait_voice_start(int64_t not_before_us, Clock& clock)
{
    if (clock.now()>=not_before_us) return;
    clock.state("waiting");
    do { clock.pause(); } while (clock.now()<not_before_us);
}
inline int choose_voice(int requested, uint32_t random, int last)
{
    if (requested<0 || requested>kVoiceCount) return 0;
    if (requested) return requested;
    if (last<1 || last>kVoiceCount) return int(random%kVoiceCount)+1;
    const int candidate=int(random%(kVoiceCount-1))+1;
    return candidate>=last ? candidate+1 : candidate;
}
inline bool voice_asr_next(uint8_t id, bool has_value) { return id==0x1b && !has_value; }
class VoiceCooldown {
public:
    bool allowed(int64_t now) const { return now>=until_; }
    void finished(int64_t now) { until_=now+1200000; }
private:
    int64_t until_=0;
};
}
