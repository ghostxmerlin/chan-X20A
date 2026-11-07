#include "collector/emotion_state.h"
#include <cassert>
#include <cstring>
using namespace collector;
int main()
{
    EmotionState state;
    constexpr auto a = "0123456789abcdef0123456789abcdef";
    constexpr auto b = "fedcba9876543210fedcba9876543210";
    assert(!state.view(1000000).connected);
    assert(state.accept(a, 1, Emotion::Happy, SttState::Listening, EmotionEvidence::Text, 1000000, 2000000));
    assert(std::strcmp(emotion_caption(state.view(2000000)), "推测：开心") == 0);
    assert(!state.accept(a, 1, Emotion::Sad, SttState::Listening, EmotionEvidence::Text, 1000000, 3000000));
    assert(!state.accept(b, 1, Emotion::Sad, SttState::Listening, EmotionEvidence::Text, 1000000, 3000000));
    assert(!state.accept(a, 2, Emotion::Sad, SttState::Listening, EmotionEvidence::Text, 4000000, 3000000));
    assert(!state.accept(a, 2, Emotion::Sad, SttState::Listening, EmotionEvidence::None, 1000000, 3000000));
    assert(state.view(9999999).connected);
    assert(!state.view(10000000).connected); // Lease boundary.
    assert(state.accept(b, 1, Emotion::Happy, SttState::Listening, EmotionEvidence::Vision, 1000000, 10000000));
    assert(state.accept(b, 2, Emotion::Happy, SttState::Listening, EmotionEvidence::Vision, 1000000, 17000000));
    assert(state.view(20999999).emotion == Emotion::Happy);
    assert(state.view(21000000).emotion == Emotion::Unknown); // Heartbeat cannot extend evidence.
    assert(!state.accept(b, 3, Emotion::Happy, SttState::Listening, EmotionEvidence::Vision, 1000000, 21000000));
    assert(state.accept(b, 3, Emotion::Unknown, SttState::Sleeping, EmotionEvidence::None, 0, 21000000));
    assert(std::strcmp(emotion_caption(state.view(21000000)), "情绪：待唤醒") == 0);
    Emotion emotion;
    assert(!parse_emotion_enum("diagnosis", kEmotionNames, emotion));
    assert(!parse_emotion_enum<Emotion>(nullptr, kEmotionNames, emotion));
    assert(parse_emotion_enum("neutral", kEmotionNames, emotion) && emotion == Emotion::Neutral);
}
