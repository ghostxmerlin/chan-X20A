#include "../upstream/StackChan/firmware/main/collector/voice_codec.h"
#include "../upstream/StackChan/firmware/main/collector/voice_policy.h"
#include <cassert>
#include <fstream>
#include <iterator>
#include <vector>
int main(int argc, char** argv) {
    using namespace collector;
    uint8_t block[256]{}; int16_t output[505]{};
    assert(decode_voice_ima(block, sizeof(block), output, 505));
    for (auto sample : output) assert(sample == 0);
    assert(!decode_voice_ima(block, 255, output, 505));
    assert(!decode_voice_ima(block, 256, output, 506));
    block[2]=89; assert(!decode_voice_ima(block, 256, output, 505)); block[2]=0;
    block[3]=1; assert(!decode_voice_ima(block, 256, output, 505));
    for (int last=0; last<=20; ++last) for (uint32_t seed=0;seed<1000;++seed) {
        int id=choose_voice(0,seed,last); assert(id>=1 && id<=20 && id!=last);
        assert(choose_voice(7,seed,last)==7);
    }
    assert(choose_voice(-1,0,0)==0 && choose_voice(21,0,0)==0);
    assert(voice_asr_open(0x10,false) && !voice_asr_open(0x14,false) && !voice_asr_open(0x10,true));
    VoiceCooldown cooldown;
    assert(cooldown.allowed(0)); cooldown.finished(1000);
    assert(!cooldown.allowed(1000) && !cooldown.allowed(1200999) && cooldown.allowed(1201000));
    if (argc==4) {
        std::ifstream in(argv[1],std::ios::binary);
        std::vector<uint8_t> encoded((std::istreambuf_iterator<char>(in)),{});
        std::vector<int16_t> pcm(std::stoul(argv[2]));
        assert(decode_voice_ima(encoded.data(),encoded.size(),pcm.data(),pcm.size()));
        std::ofstream out(argv[3],std::ios::binary); out.write(reinterpret_cast<char*>(pcm.data()),pcm.size()*2);
    }
}
