#include "collector/voice_library.h"
#include "hal/board/shared_storage.h"
#include <openssl/sha.h>
#include <cassert>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
bool fail_voice_allocation=false;
int voice_allocations=0;
static std::string folder;
static const char* injected_error=nullptr;
namespace shared_storage {
const char* read_file(const FileSpec& a,uint8_t* out,size_t capacity) {
    if(injected_error)return injected_error;
    assert(capacity==a.bytes);
    const std::string path=a.path;
    assert(path.rfind("/sdcard/soul/voice/PLAY-",0)==0);
    std::ifstream f(folder+path.substr(path.rfind('/')),std::ios::binary);
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(f)),{});
    assert(bytes.size()==a.bytes);uint8_t hash[32];SHA256(bytes.data(),bytes.size(),hash);
    assert(!std::memcmp(hash,a.sha256,32));
    std::memcpy(out,bytes.data(),bytes.size());return nullptr;
}
}
int main(int argc,char** argv) {
    assert(argc==2);folder=argv[1];using namespace collector;
    assert(!voice_clip(0)&&!voice_clip(41));
    for(int id=1;id<=40;++id) {
        const auto& clip=*voice_clip(id);std::vector<int16_t> pcm(clip.frames);
        assert(!voice_decode(clip,pcm.data()));assert(!voice_allocations);
        std::ifstream f(folder+"/"+clip.source_id+".reference.pcm",std::ios::binary);
        std::vector<char> ref((std::istreambuf_iterator<char>(f)),{});
        assert(ref.size()==pcm.size()*2&&!std::memcmp(ref.data(),pcm.data(),ref.size()));
    }
    const auto& clip=*voice_clip(1);std::vector<int16_t> pcm(clip.frames,123);
    for(const char* error:{"asset_missing","asset_invalid","storage_unavailable","io_error"}) {
        injected_error=error;assert(!std::strcmp(voice_decode(clip,pcm.data()),error));assert(!voice_allocations);
        for(auto p:pcm)assert(p==123); // No playback-ready samples on failed SD reads.
    }
    injected_error=nullptr;fail_voice_allocation=true;
    assert(!std::strcmp(voice_decode(clip,pcm.data()),"voice_no_memory"));assert(!voice_allocations);
    auto forged=clip;assert(!std::strcmp(voice_decode(forged,pcm.data()),"voice_invalid_clip"));
}
