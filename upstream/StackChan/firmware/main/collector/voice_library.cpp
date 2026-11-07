/* SPDX-License-Identifier: MIT */
#include "voice_library.h"
#include "voice_codec.h"
#include "voice_policy.h"
namespace collector {
#include "voice_assets.h"
extern const uint8_t voice_data_start[] asm("_binary_voice_library_start");
extern const uint8_t voice_data_end[] asm("_binary_voice_library_end");
static_assert(sizeof(kVoiceClips)/sizeof(kVoiceClips[0])==kVoiceCount);
static_assert(kVoiceEncodedRate==16000 && kVoicePlaybackRate==24000);
const VoiceClip* voice_clip(int id) { return id>=1 && id<=kVoiceCount ? &kVoiceClips[id-1] : nullptr; }
bool voice_decode(const VoiceClip& clip, int16_t* pcm)
{
    const size_t size=voice_data_end-voice_data_start;
    return clip.offset<=size && clip.bytes<=size-clip.offset &&
        decode_voice_16k_to_24k(voice_data_start+clip.offset,clip.bytes,pcm,clip.frames);
}
void voice_library_json(ArduinoJson::JsonObject out)
{
    out["count"]=kVoiceCount; out["endpoint"]="/v1/voice/play";
    out["asr_command"]="open"; out["stored_on_device"]=true;
    out["asr_start_delay_ms"]=kAsrVoiceStartDelayMs;
    auto clips=out["clips"].to<ArduinoJson::JsonArray>();
    for (const auto& clip:kVoiceClips) {
        auto item=clips.add<ArduinoJson::JsonObject>();
        item["id"]=clip.id; item["source_id"]=clip.source_id;
        item["title"]=clip.title; item["text"]=clip.text; item["seconds"]=clip.frames/24000.0;
    }
}
}
