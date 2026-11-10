/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
namespace collector {
// Digital gain on the mic channel, applied in audio_task before the block reaches the ring
// (HTTP stream) and hearing (ear server), so every consumer gets the same level (user,
// 2026-11-10). The ES7210 PGA is already at its 37.5 dB maximum; at arm's length speech
// arrived at about -40 dBFS against a -26 dBFS target, so the default is +12 dB.
// Persisted in NVS collector/mic_gain; POST /v1/control {"target":"mic_gain","value":dB}.
constexpr int kMicGainDefaultDb=12,kMicGainMinDb=0,kMicGainMaxDb=24;
inline bool mic_gain_valid(int db){return db>=kMicGainMinDb&&db<=kMicGainMaxDb;}

// Gain plus a block peak limiter. The whole block's peak is known before it is scaled, so the
// gain can drop at once to keep the block under the ceiling (no look-ahead, no clipping);
// it then recovers toward the target at kReleaseDbPerBlock, ramped sample by sample.
class MicGain {
public:
    static constexpr float kCeiling=29204.0f;          // -1 dBFS
    static constexpr float kReleaseDbPerBlock=0.6f;    // 30 dB/s at 20 ms blocks
    void set_db(int db){target_=std::pow(10.0f,static_cast<float>(db)/20.0f);}
    float applied_db()const{return 20.0f*std::log10(std::max(gain_,1e-6f));}
    // Scales channel `channel` of interleaved PCM in place. Returns true if this block's peak
    // would have passed the ceiling at the target gain (the limiter acted).
    bool process(int16_t* pcm,size_t frames,size_t channels,size_t channel){
        int peak=0;
        for(size_t i=0;i<frames;++i)peak=std::max(peak,std::abs(static_cast<int>(pcm[i*channels+channel])));
        const float allowed=peak>0?kCeiling/static_cast<float>(peak):1e9f;
        const float start=std::min(gain_,allowed);
        const float rise=start*std::pow(10.0f,kReleaseDbPerBlock/20.0f);
        const float end=std::min({target_,allowed,std::max(start,rise)});
        for(size_t i=0;i<frames;++i){
            const float g=start+(end-start)*static_cast<float>(i+1)/static_cast<float>(frames);
            const float v=std::round(static_cast<float>(pcm[i*channels+channel])*g);
            pcm[i*channels+channel]=static_cast<int16_t>(std::max(-32768.0f,std::min(32767.0f,v)));
        }
        gain_=end;
        return allowed<target_;
    }
private:
    float target_=1.0f,gain_=1.0f;
};
}
