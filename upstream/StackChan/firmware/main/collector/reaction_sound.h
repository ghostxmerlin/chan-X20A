/* SPDX-License-Identifier: MIT */
#pragma once
#include "remote_gestures.h"
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace collector {
constexpr int kPlaybackVolume = 80;
constexpr size_t kReactionFrames = 24000 * 360 / 1000;

// At most the latest pending cue; never replay a backlog after another job.
// Access is serialized by playback's status mutex.
class PendingReaction {
public:
    bool put(RemoteReaction value, int64_t now) { bool replaced = value_ != RemoteReaction::None; value_ = value; at_ = now; return replaced; }
    RemoteReaction take(int64_t now) { const auto value = value_; value_ = RemoteReaction::None; return now >= at_ && now - at_ <= 750000 ? value : RemoteReaction::None; }
    bool clear() { bool had = value_ != RemoteReaction::None; value_ = RemoteReaction::None; return had; }
private:
    RemoteReaction value_ = RemoteReaction::None;
    int64_t at_ = 0;
};

// Two original, short synthesized cues. Raised-cosine attacks/releases keep
// both note boundaries at zero; no full-scale samples or persistent audio task.
inline void synthesize_reaction(RemoteReaction reaction, int16_t* pcm, size_t frames)
{
    constexpr float pi = 3.14159265358979323846f;
    for (size_t i = 0; i < frames; ++i) {
        const float t = static_cast<float>(i) / 24000.0f;
        const bool second = t >= .18f;
        const float local = t - (second ? .18f : 0.0f);
        const float duration = second ? .18f : .15f;
        if (reaction == RemoteReaction::None || local >= duration || t >= .36f) { pcm[i] = 0; continue; }
        const float edge = std::min({1.0f, local / .012f, (duration - local) / .035f});
        const float envelope = .5f - .5f * std::cos(pi * std::max(0.0f, edge));
        const bool heart = reaction == RemoteReaction::Heart;
        const float frequency = heart ? (second ? 880.0f : 659.255f) : (second ? 415.305f : 622.254f);
        const float phase = 2 * pi * frequency * local;
        const float tone = heart ? .8f * std::sin(phase) + .2f * std::sin(2 * phase) :
                                   .62f * std::sin(phase) + .25f * std::sin(3 * phase) + .13f * std::sin(5 * phase);
        pcm[i] = static_cast<int16_t>(std::lround((heart ? 14000 : 26000) * envelope * tone));
    }
    if (frames) pcm[frames - 1] = 0;
}
}
