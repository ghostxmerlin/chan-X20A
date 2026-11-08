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
    std::string root;int64_t time=100,read_delay=0;bool corrupt=false,cancel_during_read=false,fail_effects=false;size_t allocated=0,reads=0;
    int64_t now_us() const override { return time; }
    bool available() const override { return true; }
    void* allocate(size_t n) override { allocated+=n;return std::malloc(n); }
    void release(void* p) override { std::free(p); }
    Error read(const Asset& a,uint8_t* out,size_t capacity) override {
        ++reads;time+=read_delay;read_delay=0;
        if(fail_effects && a.state!=State::Idle)return Error::AssetMissing;
        assert(capacity>=a.bytes);std::ifstream f(root+"/"+style_id(a.style)+"/"+state_id(a.state)+".soul",std::ios::binary);
        if (!f || !f.read(reinterpret_cast<char*>(out),a.bytes)) return Error::AssetMissing;
        if (corrupt) {out[0]^=1;corrupt=false;}
        if (cancel_during_read) {cancel_during_read=false;assert(submit(make(90,Operation::Stop),Source::Manual).accepted);}
        return Error::None;
    }
    bool sha256(const uint8_t* b,size_t n,uint8_t digest[32]) override { return SHA256(b,n,digest)!=nullptr; }
};
void configure_and_show(Host&) {
    display_gate(true,true,false);auto c=make(201,Operation::Configure);c.enabled=true;
    assert(submit(c,Source::Manual).accepted);step();assert(take_frame().pixels);rendered(true);
}
void transitions(Host& host) {
    configure_and_show(host);
    auto play=make(202,Operation::Play);play.state=State::Happy;
    assert(submit(play,Source::Manual).accepted);step();assert(take_frame().pixels);rendered(true);
    host.time+=1440000;
    assert(query(play.request_id).status==RequestStatus::Completed);
    assert(show_bound_frame() && snapshot().visible); // Completing keeps a complete eye frame over the legacy home.
    step();auto frame=take_frame();assert(frame.pixels && frame.state==State::Idle);rendered(true);
    play=make(203,Operation::Play);play.state=State::Curious;
    assert(submit(play,Source::Manual).accepted);step();assert(take_frame().pixels);rendered(true);
    assert(submit(make(204,Operation::Stop),Source::Manual).accepted);
    assert(show_bound_frame() && snapshot().visible);step();frame=take_frame();assert(frame.pixels && frame.state==State::Idle);rendered(true);
    play=make(205,Operation::Play);play.state=State::Thinking;
    assert(submit(play,Source::Manual).accepted);host.time+=10000000;
    assert(query(play.request_id).status==RequestStatus::Expired && show_bound_frame());
    step();assert(take_frame().pixels);rendered(true);
    for(unsigned reason=0;reason<3;++reason) {
        display_gate(reason!=0,reason!=1,reason==2);assert(!show_bound_frame());
        host.time+=20000000;step();assert(!take_frame().pixels);
        display_gate(true,true,false);assert(!show_bound_frame() && !snapshot().visible);
        step();frame=take_frame();assert(frame.pixels && frame.state==State::Idle);rendered(true);
    }
    auto bad=make(206,Operation::Configure);bad.enabled=true;bad.style=Style::V14;host.corrupt=true;
    assert(submit(bad,Source::Manual).accepted);step();
    assert(query(bad.request_id).status==RequestStatus::Failed);
    assert(snapshot().selected_style==Style::V05 && snapshot().enabled && show_bound_frame());
    assert(submit(make(207,Operation::Configure),Source::Manual).accepted);
    assert(!show_bound_frame());display_gate(false,true,false);display_gate(true,true,false);step();assert(!take_frame().pixels);
}
void slow_and_recovery(Host& host) {
    configure_and_show(host);
    auto play=make(301,Operation::Play);play.state=State::Happy;
    host.read_delay=8200000;assert(submit(play,Source::Manual).accepted);step();
    auto frame=take_frame();assert(frame.pixels && frame.state==State::Happy);
    assert(query(play.request_id).status==RequestStatus::Presenting);rendered(true);
    assert(submit(make(302,Operation::Stop),Source::Manual).accepted);step();assert(take_frame().pixels);rendered(true);
    host.time+=7000001;assert(std::strcmp(name(snapshot().source),"natural")==0);
    const auto last_request=snapshot().last_request;const auto manual_seq=last_request.request_seq;
    host.fail_effects=true;step();const auto failed=snapshot();assert(failed.stats.failures==1 && failed.enabled && show_bound_frame());
    const auto read_count=host.reads;const auto failed_at=host.time;
    for(unsigned i=0;i<20;++i){step();if(take_frame().pixels)rendered(true);host.time+=100000;}
    assert(host.reads==read_count && snapshot().last_request.request_seq==manual_seq);
    host.time=failed_at+4999999;step();assert(host.reads==read_count && snapshot().stats.failures==1);
    const auto unchanged=snapshot().last_request;
    assert(!std::strcmp(unchanged.request_id,last_request.request_id) && unchanged.operation==last_request.operation &&
           unchanged.status==last_request.status && unchanged.error==last_request.error);
    host.time+=7000001;step();assert(snapshot().stats.failures==2 && snapshot().enabled);
    host.fail_effects=false;
    step();if(take_frame().pixels)rendered(true);
    host.time+=7000001;step();assert(take_frame().pixels);rendered(true);
    assert(std::strcmp(name(snapshot().source),"natural")==0);
    const auto request=submit(make(303,Operation::Stop),Source::Manual);
    assert(request.accepted && request.request_seq==manual_seq+1);
}
void natural_service(Host& host) {
    configure_and_show(host);unsigned request_id=400;
    for(const auto style:{Style::V05,Style::V14,Style::V18}) {
        auto config=make(request_id++,Operation::Configure);config.enabled=true;config.style=style;
        const auto request=submit(config,Source::Manual);assert(request.accepted);step();
        auto frame=take_frame();assert(frame.pixels && frame.style==style && frame.state==State::Idle);rendered(true);
        auto s=snapshot();assert(s.natural_cycle && s.next_change_ms>=2000 && s.next_change_ms<=7000);
        host.time+=int64_t(s.next_change_ms)*1000-1;assert(snapshot().source==Source::None);
        ++host.time;s=snapshot();assert(s.source==Source::Natural && s.next_change_ms==0);
        const auto state=s.state;unsigned frames=0;
        while(snapshot().source==Source::Natural) {
            assert(frames<24);step();frame=take_frame();
            assert(frame.pixels && frame.style==style && frame.state==state);
            assert(snapshot().frame_index==frames%8);++frames;rendered(true);
            // Every frame is deliberately late, beyond the entire asset's
            // nominal duration. Natural playback must still finish all frames.
            host.time+=8000000;
        }
        assert(frames==8 || frames==16 || frames==24);
        assert(show_bound_frame() && snapshot().last_request.request_seq==request.request_seq);
        step();frame=take_frame();assert(frame.pixels && frame.style==style && frame.state==State::Idle);rendered(true);
    }
}
int main(int argc,char** argv) {
    assert(argc==2 || argc==3);Host host;host.root=argv[1];assert(initialize(host));
    if(argc==3 && std::string(argv[2])=="--transitions") {transitions(host);return 0;}
    if(argc==3 && std::string(argv[2])=="--recovery") {slow_and_recovery(host);return 0;}
    if(argc==3 && std::string(argv[2])=="--natural") {natural_service(host);return 0;}
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
