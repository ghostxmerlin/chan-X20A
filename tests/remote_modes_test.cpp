#include "../upstream/StackChan/firmware/main/collector/remote_modes.h"
#include <cassert>
#include <iostream>
using namespace collector;
int main() {
    RemoteModeInput input;
    std::array<uint8_t,6> mac{1,2,3,4,5,6};
    RemoteCommand c{1,700,471,600,false,true,123,0};
    int64_t t=100;
    auto send=[&](int yaw,int pitch,int64_t gap=100) {t+=gap;c.yaw=yaw;c.pitch=pitch;return input.accept(c,mac,t);};
    assert(send(700,471)==RemoteModeDirection::None); // first held packet
    for(int i=0;i<20;++i) assert(send(700,471)==RemoteModeDirection::None);
    assert(send(0,471)==RemoteModeDirection::None);
    assert(send(700,471)==RemoteModeDirection::Left);
    assert(send(-700,471)==RemoteModeDirection::None); // no neutral between directions
    assert(send(0,700)==RemoteModeDirection::None);
    send(0,471);
    assert(send(700,700)==RemoteModeDirection::None); // diagonal consumes arm
    assert(send(0,700)==RemoteModeDirection::None);
    send(0,471); assert(send(0,570)==RemoteModeDirection::None);
    assert(send(0,700)==RemoteModeDirection::Up);
    send(0,471); assert(send(0,200)==RemoteModeDirection::Down);
    send(0,471); assert(send(-700,471)==RemoteModeDirection::Right);
    send(0,471); assert(send(700,471,3000000)==RemoteModeDirection::None);
    assert(send(700,471)==RemoteModeDirection::None);
    send(0,471); c.sender_boot_nonce++; assert(send(700,471)==RemoteModeDirection::None);
    send(0,471); mac[0]++; assert(send(700,471)==RemoteModeDirection::None);
    send(0,471); c.center_v1=false; assert(send(700,471)==RemoteModeDirection::None);
    send(0,471); assert(send(700,471)==RemoteModeDirection::Left); // legacy guarded too
    assert(input.left_sequence()==2 && input.right_sequence()==1 && input.up_sequence()==1 && input.down_sequence()==1);
    RemoteModeInput first_neutral;
    c.center_v1=true;
    assert(first_neutral.accept(c={1,0,471,600,false,true,77,0},mac,1)==RemoteModeDirection::None);
    c.yaw=-700; assert(first_neutral.accept(c,mac,2)==RemoteModeDirection::Right);
    std::cout << "PASS: physical directions emit mode edges only; neutral, held, reconnect, nonce, source and diagonal guards\n";
}
