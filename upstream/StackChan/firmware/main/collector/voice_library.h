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
};
const VoiceClip* voice_clip(int id);
bool voice_decode(const VoiceClip& clip, int16_t* pcm);
void voice_library_json(ArduinoJson::JsonObject out);
}
