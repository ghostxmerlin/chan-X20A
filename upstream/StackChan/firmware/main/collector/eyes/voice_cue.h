/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
namespace collector::eyes {
struct VoiceCue {
    bool from_asr=false,started=false;
    // Called by the playback runner only after successful media PCM writes;
    // setup, ASR waiting and trailing silence never call this method.
    bool submitted(size_t bytes) {
        if(!from_asr || started || !bytes)return false;
        started=true;return true;
    }
};
}
