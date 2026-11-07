#include "../upstream/StackChan/firmware/main/collector/eyes/eyes.h"
#include "../upstream/StackChan/firmware/main/collector/eyes/loader.h"
#include <openssl/sha.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
using namespace collector::eyes;
Command make(unsigned n,Operation op) { Command c;c.op=op;std::snprintf(c.request_id,37,"12345678-1234-4234-8234-%012x",n);return c; }
struct Host : Backend {
    std::string root;int64_t time=100;bool corrupt=false,cancel_during_read=false;size_t allocated=0;
    int64_t now_us() const override { return time; }
    bool available() const override { return true; }
    void* allocate(size_t n) override { allocated+=n;return std::malloc(n); }
    void release(void* p) override { std::free(p); }
    Error read(const Asset& a,uint8_t* out,size_t capacity) override {
        assert(capacity>=a.bytes);std::ifstream f(root+"/"+style_id(a.style)+"/"+state_id(a.state)+".soul",std::ios::binary);
        if (!f || !f.read(reinterpret_cast<char*>(out),a.bytes)) return Error::AssetMissing;
        if (corrupt) {out[0]^=1;corrupt=false;}
        if (cancel_during_read) {cancel_during_read=false;assert(submit(make(90,Operation::Stop),Source::Manual).accepted);}
        return Error::None;
    }
    bool sha256(const uint8_t* b,size_t n,uint8_t digest[32]) override { return SHA256(b,n,digest)!=nullptr; }
};
int main(int argc,char** argv) {
    assert(argc==2 || argc==3);Host host;host.root=argv[1];assert(initialize(host));
    if(argc==3) {
        startup_failed();assert(!snapshot().memory_ready && snapshot().stats.allocated_bytes==0);
        auto c=make(100,Operation::Configure);c.enabled=true;
        assert(submit(c,Source::Manual).error==Error::NoMemory);
        step();assert(!take_frame().pixels);return 0;
    }
    assert(host.allocated==2*kAssetCapacity+4*kPixels+4096);
    display_gate(true,true,false);
    auto config=make(1,Operation::Configure);config.enabled=true;
    assert(submit(config,Source::Manual).accepted);step();auto frame=take_frame();assert(frame.pixels && frame.style==Style::V05);
    auto snapshot1=snapshot();assert(snapshot1.ready && snapshot1.enabled);rendered(true);
    voice_started(7,40,3600);assert(show_bound_frame() && snapshot().visible);step();frame=take_frame();assert(frame.pixels && frame.state==State::Determined);
    assert(snapshot().source==Source::Voice && snapshot().phase==Phase::Playing);
    host.time+=500000;step();assert(!take_frame().pixels); // Both buffers still pinned until LVGL completion.
    rendered(true);step();assert(take_frame().pixels);rendered(true);
    auto manual=make(2,Operation::Play);manual.state=State::Happy;assert(submit(manual,Source::Manual).accepted);
    cancel_voice(7);assert(snapshot().source==Source::Manual);
    step();assert(take_frame().pixels);rendered(true);
    display_gate(false,true,false);assert(query(manual.request_id).status==RequestStatus::Cancelled);
    assert(!show_bound_frame());host.time+=5000000;step();assert(!take_frame().pixels);
    auto hidden=make(61,Operation::Configure);hidden.enabled=true;
    assert(submit(hidden,Source::Manual).accepted);
    display_gate(true,true,false);assert(!show_bound_frame() && !snapshot().visible);
    step();assert(take_frame().pixels && snapshot().state==State::Idle);rendered(true);
    config=make(3,Operation::Configure);config.enabled=true;config.style=Style::V14;host.corrupt=true;
    assert(submit(config,Source::Manual).accepted);step();assert(snapshot().selected_style==Style::V05);
    assert(query(config.request_id).status==RequestStatus::Failed);assert(snapshot().enabled);step();assert(take_frame().pixels);rendered(true);
    config=make(4,Operation::Configure);config.enabled=true;config.style=Style::V18;host.cancel_during_read=true;
    assert(submit(config,Source::Manual).accepted);step();assert(snapshot().selected_style==Style::V05);
    assert(query(config.request_id).status==RequestStatus::Cancelled);assert(!take_frame().pixels);
    auto disabled=make(5,Operation::Configure);assert(submit(disabled,Source::Manual).accepted);
    voice_started(8,1,4000);assert(!snapshot().enabled && !show_bound_frame());
}
