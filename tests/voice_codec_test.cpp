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
    assert(kVoiceCount==40);
    for (int last=0; last<=40; ++last) for (uint32_t seed=0;seed<1000;++seed) {
        int id=choose_voice(0,seed,last); assert(id>=1 && id<=40 && id!=last);
        assert(choose_voice(7,seed,last)==7);
    }
    assert(choose_voice(-1,0,0)==0 && choose_voice(41,0,0)==0);
    assert(choose_voice(21,0,0)==21 && choose_voice(40,0,0)==40);
    assert(voice_asr_next(0x1b,false) && !voice_asr_next(0x10,false) && !voice_asr_next(0x1b,true));
    VoiceCooldown cooldown;
    assert(cooldown.allowed(0)); cooldown.finished(1000);
    assert(!cooldown.allowed(1000) && !cooldown.allowed(1200999) && cooldown.allowed(1201000));
    struct Clock {
        int64_t time=1000; int pauses=0, waiting=0;
        int64_t now() { return time; }
        void pause() { time+=10000; ++pauses; }
        void state(const char* name) { assert(std::string(name)=="waiting"); ++waiting; }
    } clock;
    wait_voice_start(voice_not_before(false,1000),clock);
    assert(clock.pauses==0 && clock.waiting==0);
    wait_voice_start(voice_not_before(true,1000),clock);
    assert(clock.time==801000 && clock.pauses==80 && clock.waiting==1);
    wait_voice_start(voice_not_before(true,0),clock); // Late worker must not wait another 800 ms.
    assert(clock.pauses==80);
    int16_t interpolation[7]={0,300,-300,32767,-32768,0,0};
    expand_voice_16k_to_24k(interpolation,7);
    const int16_t expected[]={0,200,100,-300,21744,10922,-32768};
    for (size_t i=0;i<7;++i) assert(interpolation[i]==expected[i]);
    block[3]=0; int16_t expanded[758]{}; expanded[757]=12345;
    assert(decode_voice_16k_to_24k(block,256,expanded,757));
    assert(expanded[757]==12345);
    assert(!decode_voice_16k_to_24k(block,256,expanded,758));
    assert(!decode_voice_16k_to_24k(block,256,expanded,0));
    assert(!decode_voice_16k_to_24k(block,256,expanded,192001));
    if (argc==4) {
        std::ifstream in(argv[1],std::ios::binary);
        std::vector<uint8_t> encoded((std::istreambuf_iterator<char>(in)),{});
        std::vector<int16_t> pcm(std::stoul(argv[2]));
        assert(decode_voice_16k_to_24k(encoded.data(),encoded.size(),pcm.data(),pcm.size()));
        std::ofstream out(argv[3],std::ios::binary); out.write(reinterpret_cast<char*>(pcm.data()),pcm.size()*2);
    }
}
