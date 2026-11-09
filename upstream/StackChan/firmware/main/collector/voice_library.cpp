/* SPDX-License-Identifier: MIT */
#include "voice_library.h"
#include "voice_codec.h"
#include "voice_policy.h"
#include "hal/board/shared_storage.h"
#include <esp_heap_caps.h>
namespace collector {
#include "voice_assets.h"
static_assert(sizeof(kVoiceClips)/sizeof(kVoiceClips[0])==kVoiceCount);
static_assert(kVoiceEncodedRate==16000 && kVoicePlaybackRate==24000);
const VoiceClip* voice_clip(int id) { return id>=1 && id<=kVoiceCount ? &kVoiceClips[id-1] : nullptr; }
const char* voice_emotion(int id) { const auto* clip=voice_clip(id); return clip ? clip->emotion : ""; }
const char* voice_decode(const VoiceClip& clip, int16_t* pcm)
{
    if (voice_clip(clip.id)!=&clip || !pcm) return "voice_invalid_clip";
    auto* encoded=static_cast<uint8_t*>(heap_caps_malloc(clip.bytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if (!encoded) return "voice_no_memory";
    const auto* error=shared_storage::read_file({clip.path,clip.bytes,clip.sha256},encoded,clip.bytes);
    if (!error && !decode_voice_16k_to_24k(encoded,clip.bytes,pcm,clip.frames)) error="voice_decode";
    heap_caps_free(encoded);
    return error;
}
void voice_library_json(ArduinoJson::JsonObject out)
{
    out["count"]=kVoiceCount; out["endpoint"]="/v1/voice/play";
    out["asr_command"]="open"; out["stored_on_device"]=true;
    out["storage"]="sd";out["asset_install"]=true;out["asset_endpoint"]="/v1/voice/assets";
    out["asr_start_delay_ms"]=kAsrVoiceStartDelayMs;
    auto clips=out["clips"].to<ArduinoJson::JsonArray>();
    for (const auto& clip:kVoiceClips) {
        auto item=clips.add<ArduinoJson::JsonObject>();
        item["id"]=clip.id; item["source_id"]=clip.source_id;
        item["title"]=clip.title; item["text"]=clip.text; item["emotion"]=clip.emotion; item["seconds"]=clip.frames/24000.0;
    }
}
}
