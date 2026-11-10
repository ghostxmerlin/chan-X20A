// Host test for collector::MicGain (mic digital gain + block peak limiter); no hardware.
#include "../upstream/StackChan/firmware/main/collector/mic_gain.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <vector>
using collector::MicGain;
constexpr size_t kFrames=480,kCh=2;
static double rms_db(const std::vector<int16_t>& b,size_t ch){
    double s=0;for(size_t i=0;i<kFrames;++i){double v=b[i*kCh+ch];s+=v*v;}
    return 20*std::log10(std::sqrt(s/kFrames)/32768.0);
}
static std::vector<int16_t> sine(double dbfs,int16_t ref=1234,size_t phase=0){
    std::vector<int16_t> b(kFrames*kCh);
    const double a=32768.0*std::pow(10.0,dbfs/20.0)*std::sqrt(2.0);
    for(size_t i=0;i<kFrames;++i){b[i*kCh]=static_cast<int16_t>(std::lround(a*std::sin(2*M_PI*440*(i+phase)/24000.0)));b[i*kCh+1]=ref;}
    return b;
}
int main(){
    assert(collector::mic_gain_valid(0)&&collector::mic_gain_valid(24));
    assert(!collector::mic_gain_valid(-1)&&!collector::mic_gain_valid(25));
    {   // 0 dB leaves the samples untouched
        MicGain g;g.set_db(0);auto b=sine(-30),orig=b;
        for(int k=0;k<5;++k){b=orig;assert(!g.process(b.data(),kFrames,kCh,0));}
        assert(b==orig);
    }
    {   // quiet speech level is raised by the target once the gain has ramped up
        MicGain g;g.set_db(12);
        std::vector<int16_t> b;
        for(int k=0;k<40;++k){b=sine(-40,1234,k*kFrames);assert(!g.process(b.data(),kFrames,kCh,0));}
        const double out=rms_db(b,0);
        std::printf("quiet: -40 dBFS in -> %.2f dBFS out, applied %.2f dB\n",out,g.applied_db());
        assert(std::fabs(out-(-28.0))<0.2);
        for(size_t i=0;i<kFrames;++i)assert(b[i*kCh+1]==1234);   // reference channel untouched
    }
    {   // a loud block never passes the ceiling, and the gain recovers afterwards
        MicGain g;g.set_db(12);
        std::vector<int16_t> b;
        for(int k=0;k<40;++k){b=sine(-40);g.process(b.data(),kFrames,kCh,0);}
        b=sine(-6);assert(g.process(b.data(),kFrames,kCh,0));
        int peak=0;for(size_t i=0;i<kFrames;++i)peak=std::max(peak,std::abs(static_cast<int>(b[i*kCh])));
        std::printf("loud: -6 dBFS sine peak after limiter %d (ceiling %.0f)\n",peak,MicGain::kCeiling);
        assert(peak<=static_cast<int>(MicGain::kCeiling)+1);
        int blocks=0;
        do{b=sine(-40);g.process(b.data(),kFrames,kCh,0);++blocks;}while(g.applied_db()<11.99f&&blocks<100);
        std::printf("recovered to target in %d blocks (%d ms)\n",blocks,blocks*20);
        assert(blocks<=25);
    }
    {   // a full-scale square wave cannot wrap around
        MicGain g;g.set_db(24);
        std::vector<int16_t> b(kFrames*kCh);
        for(size_t i=0;i<kFrames;++i)b[i*kCh]=(i/24)%2?32767:-32768;
        g.process(b.data(),kFrames,kCh,0);
        for(size_t i=0;i<kFrames;++i)assert(std::abs(static_cast<int>(b[i*kCh]))<=static_cast<int>(MicGain::kCeiling)+1);
    }
    {   // lowering the target takes effect within one block
        MicGain g;g.set_db(12);std::vector<int16_t> b;
        for(int k=0;k<40;++k){b=sine(-40);g.process(b.data(),kFrames,kCh,0);}
        g.set_db(0);b=sine(-40);g.process(b.data(),kFrames,kCh,0);
        assert(std::fabs(g.applied_db())<0.01f);
    }
    std::puts("mic_gain_test ok");
}
