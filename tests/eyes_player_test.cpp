#include "../upstream/StackChan/firmware/main/collector/eyes/player.h"
#include "../upstream/StackChan/firmware/main/collector/eyes/frame_pool.h"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace collector::eyes;
Command command(unsigned n,Operation op=Operation::Play) {
    Command c; std::snprintf(c.request_id,sizeof(c.request_id),"00000000-0000-4000-8000-%012x",n); c.op=op;return c;
}
void deadline_test() {
    Player p;p.set_resources(true,true);p.set_gate(true,Error::None,100);
    auto c=command(201,Operation::Configure);c.enabled=true;
    assert(p.submit(c,Source::Manual,100).accepted);p.loaded(p.snapshot().generation,101);
    auto slow=command(202);slow.state=State::Happy;
    assert(p.submit(slow,Source::Manual,1000).accepted);auto gen=p.snapshot().generation;
    p.loaded(gen,8201000);
    assert(p.query(slow.request_id,8201000).status==RequestStatus::Accepted);
    assert(p.presented(gen,0,1,8201000));
    auto expired=command(203);assert(p.submit(expired,Source::Manual,9000000).accepted);gen=p.snapshot().generation;
    p.loaded(gen,19000000);assert(p.query(expired.request_id,19000000).status==RequestStatus::Expired);
    assert(!p.presented(gen,0,2,19000001));
    auto voice=command(204);assert(p.submit(voice,Source::Voice,20000000,1).accepted);gen=p.snapshot().generation;
    p.loaded(gen,21500000);assert(p.query(voice.request_id,21500000).status==RequestStatus::Expired);
}
void voice_boot_test() {
    Player p;p.set_resources(true,true);p.set_gate(true,Error::None,100);
    p.tick(9000000);assert(!p.snapshot().enabled && !p.snapshot().natural_cycle);
    auto voice=command(250);voice.state=State::Happy;
    const auto request=p.submit(voice,Source::Voice,9000001,11);assert(request.accepted);
    auto gen=p.snapshot().generation;p.loaded(gen,9000010);assert(p.presented(gen,0,1,9000020));
    p.cancel_voice(11,9000030);assert(p.snapshot().enabled && p.snapshot().selected_style==Style::V05);
    assert(p.presented(p.snapshot().generation,0,2,9000040));p.tick(16000041);
    assert(p.snapshot().natural_cycle && p.snapshot().source==Source::Natural);
    assert(p.snapshot().last_request.request_seq==request.request_seq);
}
void natural_test() {
    Player p;p.set_resources(true,true);p.set_gate(true,Error::None,100);
    p.tick(9000000);assert(!p.snapshot().enabled); // No startup/background opt-in.
    auto config=command(301,Operation::Configure);config.enabled=true;
    const auto configured=p.submit(config,Source::Manual,9000100);assert(configured.accepted);
    p.loaded(p.snapshot().generation,9000200);assert(p.presented(p.snapshot().generation,0,1,9000300));
    int64_t now=16000301;p.tick(now);
    assert(std::strcmp(name(p.snapshot().source),"natural")==0);
    assert(p.snapshot().state!=State::Idle);
    assert(p.snapshot().last_request.request_seq==configured.request_seq);
    State previous=State::Invalid;unsigned counts[30]{};uint32_t sequence=2;
    bool rounds_seen[4]{};uint32_t shortest_wait=7000,longest_wait=2000;
    for(unsigned cycle=0;cycle<1200;++cycle) {
        auto s=p.snapshot();assert(std::strcmp(name(s.source),"natural")==0);
        assert(s.state!=State::Idle && s.state!=previous);previous=s.state;++counts[size_t(s.state)];
        const auto gen=s.generation;p.loaded(gen,now);
        unsigned frames=0;
        do {
            auto plan=p.frame_plan(now);if(plan.due_us>now)now=plan.due_us;
            // An arbitrarily slow render still presents every natural frame.
            now+=900000;
            assert(frames<24);assert(plan.frame==frames%8);assert(p.presented(gen,plan.frame,sequence++,now));++frames;
            const bool may_decode_next=p.can_decode(now);
            const auto next=p.frame_plan(now);now=next.due_us;p.tick(now);
            if(p.snapshot().source==Source::None)assert(!may_decode_next); // No speculative extra round after its final frame.
        } while(std::strcmp(name(p.snapshot().source),"natural")==0);
        assert(frames==8 || frames==16 || frames==24);
        rounds_seen[frames/8]=true;
        assert(p.snapshot().state==State::Idle && p.snapshot().last_request.request_seq==configured.request_seq);
        assert(p.presented(p.snapshot().generation,0,sequence++,now));
        const auto wait=p.snapshot().next_change_ms;assert(wait>=2000 && wait<=7000);
        shortest_wait=wait<shortest_wait?wait:shortest_wait;longest_wait=wait>longest_wait?wait:longest_wait;
        p.tick(now+1999999);assert(p.snapshot().state==State::Idle);
        now+=7000001;p.tick(now);
    }
    for(size_t i=1;i<30;++i)assert(counts[i]>0);
    assert(rounds_seen[1] && rounds_seen[2] && rounds_seen[3] && longest_wait>shortest_wait);
    assert(counts[size_t(State::Happy)]>counts[size_t(State::Angry)]);
    auto manual=command(302);manual.state=State::Thinking;
    assert(p.submit(manual,Source::Manual,now).accepted);auto gen=p.snapshot().generation;
    p.loaded(gen,now);assert(p.presented(gen,0,sequence++,now));
    auto voice=command(303);assert(p.submit(voice,Source::Voice,now+1,77).error==Error::Busy);
    p.tick(now+30000000);assert(p.query(manual.request_id,now+30000000).status==RequestStatus::Completed);
    now+=30000000;assert(p.presented(p.snapshot().generation,0,sequence++,now));now+=7000001;p.tick(now);
    assert(std::strcmp(name(p.snapshot().source),"natural")==0);
    assert(p.submit(voice,Source::Voice,now,77).accepted);assert(p.snapshot().source==Source::Voice);
    p.cancel_voice(77,now+1);assert(p.snapshot().state==State::Idle);
    auto stop=command(304,Operation::Stop);assert(p.submit(stop,Source::Manual,now+2).accepted);
    assert(p.snapshot().enabled && p.snapshot().state==State::Idle);
    p.set_gate(false,Error::Reaction,now+3);p.tick(now+90000000);assert(p.snapshot().phase==Phase::Suspended);
    now+=90000000;p.set_gate(true,Error::None,now);p.tick(now+8000000);assert(p.snapshot().state==State::Idle);
    assert(p.presented(p.snapshot().generation,0,sequence++,now+8000000));
    now+=15000001;p.tick(now);assert(std::strcmp(name(p.snapshot().source),"natural")==0);
    auto switch_style=command(305,Operation::Configure);switch_style.enabled=true;switch_style.style=Style::V14;
    assert(p.submit(switch_style,Source::Manual,now).accepted);p.failed(p.snapshot().generation,Error::AssetMissing,now+1);
    assert(p.snapshot().selected_style==Style::V05 && p.snapshot().enabled);
    auto off=command(306,Operation::Configure);assert(p.submit(off,Source::Manual,now+2).accepted);
    p.tick(now+90000000);assert(!p.snapshot().enabled);
}
int main(int argc,char** argv) {
    if(argc==2 && !std::strcmp(argv[1],"deadline")){deadline_test();return 0;}
    if(argc==2 && !std::strcmp(argv[1],"voice-boot")){voice_boot_test();return 0;}
    if(argc==2 && !std::strcmp(argv[1],"natural")){natural_test();return 0;}
    Player p; p.set_resources(true,true);p.set_gate(true,Error::None,0);
    auto c=command(1,Operation::Configure);c.enabled=true;c.style=Style::V14;
    auto r=p.submit(c,Source::Manual,0);assert(r.accepted);
    auto play=command(2);play.state=State::Happy;
    assert(p.submit(play,Source::Manual,1).error==Error::Busy);
    auto gen=p.snapshot().generation;p.loaded(gen,1);assert(p.snapshot().enabled && p.snapshot().selected_style==Style::V14);
    r=p.submit(play,Source::Manual,2);assert(r.accepted);
    auto duplicate=p.submit(play,Source::Manual,3);assert(duplicate.accepted && duplicate.request_seq==r.request_seq && duplicate.duplicate);
    auto conflict=play;conflict.state=State::Angry;assert(p.submit(conflict,Source::Manual,4).error==Error::IdConflict);
    auto voice=command(3);assert(p.submit(voice,Source::Voice,5,42).error==Error::Busy);
    auto lower=command(30,Operation::Stop);assert(p.submit(lower,Source::Voice,5,42).error==Error::Busy);
    gen=p.snapshot().generation;p.loaded(gen,6);p.presented(gen,0,1,1000);
    assert(p.snapshot().phase==Phase::Playing);
    p.tick(1000+1440000);assert(p.snapshot().state==State::Idle);
    assert(p.query(play.request_id,1000+1440000).status==RequestStatus::Completed);
    auto slow=command(4);r=p.submit(slow,Source::Voice,2000000);gen=p.snapshot().generation;
    p.loaded(gen,3500000);assert(p.query(slow.request_id,3500000).status==RequestStatus::Expired);
    assert(!p.presented(gen,0,2,3500001));
    auto loop=command(5);loop.mode=Mode::Loop;loop.loop_for_ms=5000;
    assert(p.submit(loop,Source::Voice,4000000,99).accepted);gen=p.snapshot().generation;
    p.loaded(gen,4000010);p.presented(gen,0,2,4000100);
    auto manual=command(6);manual.state=State::Angry;
    assert(p.submit(manual,Source::Manual,4100000).accepted);
    p.cancel_voice(99,4200000);assert(p.snapshot().source==Source::Manual);
    gen=p.snapshot().generation;p.set_gate(false,Error::PageHidden,4200001);
    assert(p.query(manual.request_id,4200001).status==RequestStatus::Cancelled);
    p.loaded(gen,4200100);assert(!p.presented(gen,0,3,4200101));
    p.set_gate(true,Error::None,4300000);assert(p.snapshot().state==State::Idle);
    auto badstyle=command(7,Operation::Configure);badstyle.enabled=true;badstyle.style=Style::V18;
    assert(p.submit(badstyle,Source::Manual,4400000).accepted);gen=p.snapshot().generation;
    p.failed(gen,Error::AssetMissing,4400010);assert(p.snapshot().selected_style==Style::V14 && p.snapshot().enabled);
    auto disabled=command(8,Operation::Configure);assert(p.submit(disabled,Source::Manual,4500000).accepted);
    assert(!p.snapshot().enabled);assert(p.submit(command(9),Source::Voice,4500010,100).error==Error::Disabled);
    Player fresh;fresh.set_resources(true,true);fresh.set_gate(true,Error::None,0);
    assert(fresh.submit(command(10),Source::Voice,0,7).accepted);gen=fresh.snapshot().generation;
    fresh.loaded(gen,100);assert(fresh.snapshot().enabled);fresh.presented(gen,0,1,100);
    fresh.cancel_voice(7,200);assert(fresh.snapshot().state==State::Idle);
    assert(fresh.query(command(10).request_id,60000001).error==Error::RequestUnknown);
    auto invalid=command(11);invalid.loop_for_ms=1000;assert(fresh.submit(invalid,Source::Manual,60000002).error==Error::InvalidArgument);
    invalid=command(11);invalid.mode=Mode::Loop;invalid.loop_for_ms=30001;assert(fresh.submit(invalid,Source::Manual,60000002).error==Error::InvalidArgument);
    FramePool pool;pool.cancel(1);int first=pool.begin(1);assert(first>=0);
    assert(pool.publish(first,1,0,0));assert(pool.take(1,0)==first);
    int second=pool.begin(1);assert(second>=0 && second!=first);assert(pool.publish(second,1,1,100));
    assert(pool.take(1,99)==-1);assert(pool.take(1,100)==second);
    assert(pool.begin(1)==-1);pool.cancel(2);assert(pool.begin(2)==-1); // Bound/retiring are never reused on cancel.
    pool.rendered(false);int free=pool.begin(2);assert(free==first);
    pool.cancel(3);assert(!pool.publish(free,2,0,0));assert(pool.begin(3)==first);
    for(int i=1;i<=40;++i)assert(valid(voice_state(i)));
}
