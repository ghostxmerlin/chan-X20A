#include "../upstream/StackChan/firmware/main/collector/remote_protocol.h"
#include <array>
#include <cassert>
#include <iostream>
#include <random>
using namespace collector;
int main() {
    // Real official-format fixtures: broadcast, negative yaw, 45 degree pitch, speed 600.
    std::array<uint8_t,8> packet{0,0x00,0xfb,0xc2,0x01,0x58,0x02,1};
    RemoteCommand command;
    assert(decode_remote(packet.data(),packet.size(),command));
    assert(command.yaw==-1280&&command.pitch==450&&command.speed==600&&command.auxiliary);
    assert(remote_for_receiver(command,1));
    for(size_t size=0;size<8;++size)assert(!decode_remote(packet.data(),size,command));
    assert(!decode_remote(packet.data(),9,command));
    packet[0]=2;assert(decode_remote(packet.data(),8,command));assert(!remote_for_receiver(command,1));
    packet[0]=1;assert(decode_remote(packet.data(),8,command));assert(remote_for_receiver(command,1));
    packet[3]=0xff;packet[4]=0xff;assert(decode_remote(packet.data(),8,command)); // mapped input can be negative
    assert(command.pitch==-1&&limit_remote_motion(command).pitch==50);
    packet={1,0,0,0x84,3,0xe8,3,0};assert(decode_remote(packet.data(),8,command)); // boundary 90°, speed 1000
    packet[3]++;assert(decode_remote(packet.data(),8,command));
    // Captured official controller input: ID 1, yaw 0, pitch 95 degrees, speed 600.
    packet={1,0,0,0xb6,3,0x58,2,0};assert(decode_remote(packet.data(),8,command));
    assert(command.pitch==950&&command.speed==600);
    auto limited=limit_remote_motion(command);assert(limited.pitch==850&&limited.speed==600);
    assert(command.pitch==950); // Do not overwrite the raw recording.
    packet={1,0,0x80,0xff,0x7f,0,0,0};assert(decode_remote(packet.data(),8,command));
    limited=limit_remote_motion(command);assert(limited.yaw==-1280&&limited.pitch==850);
    packet={1,0,0,0,0,0xff,0xff,0};assert(!decode_remote(packet.data(),8,command)); // negative speed
    std::mt19937 random(11223);
    for(int i=0;i<100000;++i){for(auto& b:packet)b=random();if(decode_remote(packet.data(),8,command)){
        assert(command.speed>=0&&command.speed<=1000);
        auto safe=limit_remote_motion(command);
        assert(safe.yaw>=-1280&&safe.yaw<=1280);assert(safe.pitch>=50&&safe.pitch<=850);assert(safe.speed==command.speed);
    }}
    std::cout<<"PASS: official packet fixtures, extrapolated angles, actuator limits, truncation, ID filtering and 100000 random payloads\n";
}
