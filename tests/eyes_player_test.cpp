#include "../upstream/StackChan/firmware/main/collector/eyes/player.h"
#include "../upstream/StackChan/firmware/main/collector/eyes/frame_pool.h"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace collector::eyes;
Command command(unsigned n,Operation op=Operation::Play) {
    Command c; std::snprintf(c.request_id,sizeof(c.request_id),"00000000-0000-4000-8000-%012x",n); c.op=op;return c;
}
int main() {
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
    auto slow=command(4);r=p.submit(slow,Source::Manual,2000000);gen=p.snapshot().generation;
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
