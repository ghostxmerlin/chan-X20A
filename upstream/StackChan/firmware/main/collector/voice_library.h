/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <cstddef>
#include <cstdint>
namespace collector {
struct VoiceClip {
    int id;
    const char *source_id, *title, *text;
    uint32_t offset, bytes, frames;
    const char* path;
    uint8_t sha256[32];
    const char* emotion; // response label from config/voice-emotions.json
};
const VoiceClip* voice_clip(int id);
// Emotion label of clip id, "" when out of range (see hearing_policy.h emotion_clip).
const char* voice_emotion(int id);
// Returns null only after a verified SD read and complete in-memory decode.
const char* voice_decode(const VoiceClip& clip, int16_t* pcm);
void voice_library_json(ArduinoJson::JsonObject out);
}
