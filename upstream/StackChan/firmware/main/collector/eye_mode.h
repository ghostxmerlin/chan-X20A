/* SPDX-License-Identifier: MIT */
#pragma once
#include "eyes/catalog.generated.h"
#include <cstdint>
#include <cstring>

namespace collector {
// Eyes per mode (hello modes step 1b, collector-0.4.92; replaces the 2026-11-10 "vision on = v14" rule):
//  - talk: the laughing man home page, always, even with vision on (owner 2026-11-14);
//  - chat v05, crowd v14, companion v18 (mode_rules.h kModes); the page_* modes keep the laughing man until
//    their own pages come (0.4.93);
//  - the natural cycle picks from the mode's pool: normal = sleepy / thinking / curious, lively (crowd) =
//    greeting / happy / curious / goofy / surprised (spec 3.2). Emotion reactions inside a mode stay as they were.
inline constexpr eyes::Style kNormalVisionStyle = eyes::Style::V14;
inline constexpr uint32_t eye_bit(eyes::State s) { return uint32_t{1} << static_cast<uint32_t>(s); }
inline constexpr uint32_t kNormalNaturalPool =
    eye_bit(eyes::State::Sleepy) | eye_bit(eyes::State::Thinking) | eye_bit(eyes::State::Curious);
inline constexpr uint32_t kLivelyNaturalPool = eye_bit(eyes::State::Greeting) | eye_bit(eyes::State::Happy) |
    eye_bit(eyes::State::Curious) | eye_bit(eyes::State::Goofy) | eye_bit(eyes::State::Surprised);

// What the eyes should show: enabled = mech eyes of `style`, else the laughing man.
struct EyeLook {
    bool enabled = false;
    eyes::Style style = kNormalVisionStyle;
    uint32_t pool = kNormalNaturalPool;
    bool operator==(const EyeLook& o) const { return enabled == o.enabled && (!enabled || style == o.style) && pool == o.pool; }
    bool operator!=(const EyeLook& o) const { return !(*this == o); }
};

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

// Keeps the eyes configuration in line with the mode's look. Pure: the caller sets the pool and submits the
// configure command when `due` says so, and reports back with applied().
class EyeModeController {
public:
    static constexpr int64_t kRetryUs = 2000000;
    bool due(const EyeLook& want, int64_t now) const { return (!known_ || want != applied_) && now >= retry_at_; }
    void applied(const EyeLook& want, bool accepted, int64_t now)
    {
        if (accepted) { known_ = true; applied_ = want; retry_at_ = 0; }
        else retry_at_ = now + kRetryUs;
    }
    bool known() const { return known_; }
    const EyeLook& look() const { return applied_; }

private:
    bool known_ = false;
    EyeLook applied_{};
    int64_t retry_at_ = 0;
};
}
