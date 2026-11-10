/* SPDX-License-Identifier: MIT */
#pragma once
#include "eyes/catalog.generated.h"
#include <cstdint>
#include <cstring>

namespace collector {
// "Normal" eye mode (user, 2026-11-10):
//  - vision (M12 detection) off: the laughing man home page, always, even while she speaks;
//  - vision on: green mech eyes (v14); with nothing new to show they cycle idle → sleepy /
//    thinking / curious → idle; an emotional utterance shows its emotion once, unless that
//    utterance got a voice response, whose clip already drives the eyes.
inline constexpr eyes::Style kNormalVisionStyle = eyes::Style::V14;
inline constexpr uint32_t eye_bit(eyes::State s) { return uint32_t{1} << static_cast<uint32_t>(s); }
inline constexpr uint32_t kNormalNaturalPool =
    eye_bit(eyes::State::Sleepy) | eye_bit(eyes::State::Thinking) | eye_bit(eyes::State::Curious);

// Backend emotion label (SenseVoice / voice library) → eye state. neutral / unknown are not an
// emotion to show: Invalid.
inline eyes::State eye_state_for_emotion(const char* emotion)
{
    if (!emotion) return eyes::State::Invalid;
    struct Map { const char* label; eyes::State state; };
    static constexpr Map kMap[] = {
        {"happy", eyes::State::Happy}, {"sad", eyes::State::Sad}, {"angry", eyes::State::Angry},
        {"fearful", eyes::State::Scared}, {"disgusted", eyes::State::Refusing}, {"surprised", eyes::State::Surprised},
    };
    for (const auto& m : kMap) if (!std::strcmp(emotion, m.label)) return m.state;
    return eyes::State::Invalid;
}

// Random UUID (version 4 layout) for eyes requests; `random32` supplies 32 random bits per call.
template <typename Random>
inline void eye_request_id(char (&out)[37], Random random32)
{
    static constexpr char kHex[] = "0123456789abcdef";
    uint32_t word = 0;
    int left = 0;
    for (int i = 0; i < 36; ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { out[i] = '-'; continue; }
        if (!left) { word = random32(); left = 8; }
        int digit = word & 15; word >>= 4; --left;
        if (i == 14) digit = 4;                          // version
        if (i == 19) digit = 8 | (digit & 3);            // variant
        out[i] = kHex[digit];
    }
    out[36] = 0;
}

// Keeps the eyes configuration in line with vision. Pure: the caller submits the configure
// command when `due` says so and reports back with applied().
class EyeModeController {
public:
    static constexpr int64_t kRetryUs = 2000000;
    // True when a configure (enabled = vision) should be submitted now.
    bool due(bool vision, int64_t now) const
    {
        return (!known_ || vision != applied_) && now >= retry_at_;
    }
    void applied(bool vision, bool accepted, int64_t now)
    {
        if (accepted) { known_ = true; applied_ = vision; }
        else retry_at_ = now + kRetryUs;
    }
    bool vision() const { return known_ && applied_; }

private:
    bool known_ = false, applied_ = false;
    int64_t retry_at_ = 0;
};
}
