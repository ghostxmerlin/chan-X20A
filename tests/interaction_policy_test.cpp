#include "collector/interaction_policy.h"
#include <cassert>
#include <cstring>
using namespace collector;
using namespace collector::interaction;
int main() {
    assert(camera_mode(false,false)==M12Function::standby);
    assert(camera_mode(true,false)==M12Function::detect);
    assert(camera_mode(true,true)==M12Function::detect);    // m12d: recognition = detection + names
    assert(camera_mode(false,true)==M12Function::detect);
    assert(asr_mode_action(0x10,false)==AsrModeAction::OpenTracking);
    assert(asr_mode_action(0x11,false)==AsrModeAction::CloseTracking);
    assert(asr_mode_action(0x1b,false)==AsrModeAction::None);
    assert(asr_mode_action(0x10,true)==AsrModeAction::None);
    assert(toggle_due(0xffffffff,0));assert(!toggle_due(2,4));
    M12Face face{};face.x=200;face.y=100;face.w=120;face.h=140;face.prob=.9f;std::strcpy(face.name,"known_a");
    OwnerGate g;g.reset(1000);
    OwnerFrame f{1,2000,1500,true,M12Function::detect,1,&face};
    assert(!g.observe(f,2000));assert(g.seen());assert(!g.confirmed());
    assert(!g.observe(f,3000)); // polling one frame must never confirm identity
    f.seq=2;f.at_us=200000;assert(g.observe(f,200000));assert(g.confirmed());
    f.seq=3;f.at_us=400000;assert(!g.observe(f,400000)); // one entry per enable cycle
    assert(!g.observe(f,1200000));assert(!g.seen());assert(g.confirmed());
    g.reset(1300000);assert(!g.observe(f,1300000));assert(!g.seen());
    f.seq=4;f.at_us=1400000;f.requested_us=1350000;f.current=false;          // not our confirmed detection
    assert(!g.observe(f,1400000));assert(!g.seen());
    f.current=true;f.mode=M12Function::standby;assert(!g.observe(f,1400000));
    f.mode=M12Function::detect;face.name[0]=0;assert(!g.observe(f,1400000));assert(!g.seen());   // no name: not recognised
    std::strcpy(face.name,"known_b");f.seq=5;f.at_us=1500000;assert(!g.observe(f,1500000));
    std::strcpy(face.name,"known_c");f.seq=6;f.at_us=1600000;assert(!g.observe(f,1600000));
    f.seq=7;f.at_us=1700000;assert(g.observe(f,1700000));assert(std::strcmp(g.name(),"known_c")==0);
    g.reset(2000000);f.seq=8;f.at_us=2100000;f.requested_us=2200000;assert(!g.observe(f,2300000));
    f.requested_us=2000000;f.at_us=2400000;assert(!g.observe(f,2300000)); // future result
    f.at_us=2100000;assert(!g.observe(f,3000000)); // stale
    f.seq=9;f.at_us=3100000;face.w=0;assert(!g.observe(f,3100000));
    face.w=120;f.seq=10;f.at_us=3200000;assert(!g.observe(f,3200000));
    f.seq=11;f.at_us=3100000;assert(!g.observe(f,3250000)); // reordered result cannot confirm
    f.seq=12;f.at_us=3400000;assert(g.observe(f,3400000));
}
