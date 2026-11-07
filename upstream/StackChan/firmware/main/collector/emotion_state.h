/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
#include <cstring>

namespace collector {
// No free-form model output, speech or images are retained on the robot.
enum class Emotion { Unknown, Neutral, Happy, Sad, Angry, Anxious, Surprised };
enum class SttState { Offline, Connecting, Listening, Sleeping, Error };
enum class EmotionEvidence { None, Text, Vision, Both };
inline constexpr const char* kEmotionNames[] = {"unknown", "neutral", "happy", "sad", "angry", "anxious", "surprised"};
inline constexpr const char* kSttNames[] = {"offline", "connecting", "listening", "sleeping", "error"};
inline constexpr const char* kEvidenceNames[] = {"none", "text", "vision", "both"};
template <typename T, size_t N>
bool parse_emotion_enum(const char* value, const char* const (&names)[N], T& out)
{
    if (!value) return false;
    for (size_t i = 0; i < N; ++i) if (std::strcmp(value, names[i]) == 0) {
        out = static_cast<T>(i); return true;
    }
    return false;
}
struct EmotionView {
    bool connected = false;
    Emotion emotion = Emotion::Unknown;
    SttState stt = SttState::Offline;
    EmotionEvidence evidence = EmotionEvidence::None;
};
struct EmotionState {
    static constexpr int64_t kLeaseUs = 8000000, kEvidenceUs = 20000000;
    char session[33] = {};
    uint32_t sequence = 0;
    int64_t received_us = -1, observed_us = 0;
    Emotion emotion = Emotion::Unknown;
    SttState stt = SttState::Offline;
    EmotionEvidence evidence = EmotionEvidence::None;

    bool accept(const char* id, uint32_t seq, Emotion value, SttState status,
                EmotionEvidence source, int64_t observed, int64_t now)
    {
        if (!id || std::strlen(id) != 32 || !seq || now < 0 || observed < 0 || observed > now) return false;
        for (size_t i = 0; i < 32; ++i)
            if (!(id[i] >= '0' && id[i] <= '9') && !(id[i] >= 'a' && id[i] <= 'f')) return false;
        if (value != Emotion::Unknown && (source == EmotionEvidence::None || !observed || now - observed >= kEvidenceUs)) return false;
        if (received_us >= 0) {
            if (std::strcmp(session, id) == 0 && seq <= sequence) return false;
            if (std::strcmp(session, id) != 0 && now - received_us < kLeaseUs) return false;
        }
        std::memcpy(session, id, 33);
        sequence = seq; received_us = now; observed_us = observed;
        emotion = value; stt = status;
        evidence = value == Emotion::Unknown ? EmotionEvidence::None : source;
        return true;
    }
    EmotionView view(int64_t now) const
    {
        EmotionView result;
        if (received_us < 0 || now < received_us || now - received_us >= kLeaseUs) return result;
        result.connected = true; result.stt = stt;
        if (observed_us > 0 && now >= observed_us && now - observed_us < kEvidenceUs) {
            result.emotion = emotion; result.evidence = evidence;
        }
        return result;
    }
};
inline const char* emotion_caption(const EmotionView& view)
{
    if (!view.connected) return "情绪：未连接";
    switch (view.emotion) {
        case Emotion::Neutral: return "推测：平静";
        case Emotion::Happy: return "推测：开心";
        case Emotion::Sad: return "推测：低落";
        case Emotion::Angry: return "推测：生气";
        case Emotion::Anxious: return "推测：紧张";
        case Emotion::Surprised: return "推测：惊讶";
        default: break;
    }
    if (view.stt == SttState::Sleeping) return "情绪：待唤醒";
    if (view.stt == SttState::Connecting) return "情绪：连接中";
    if (view.stt == SttState::Error) return "情绪：语音断开";
    return "情绪：不确定";
}
}
