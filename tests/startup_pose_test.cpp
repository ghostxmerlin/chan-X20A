#include "../upstream/StackChan/firmware/main/collector/startup_pose.h"
#include "../upstream/StackChan/firmware/main/collector/remote_modes.h"
#include <cassert>
#include <cstring>
#include <iostream>
using collector::StartupPose;
using Action = StartupPose::Action;
int main()
{
    StartupPose boot;
    assert(StartupPose::kPitch == 130);
    assert(boot.step(0,true,false,true) == Action::None);
    assert(boot.step(100000,true,true,true) == Action::Start);
    collector::RemoteModeInput modes;
    std::array<uint8_t,6> source{1,2,3,4,5,6};
    // Every representable raw pitch remains input data. The edge decoder has
    // no actuator dependency, and cannot change the boot request or its clock.
    for(int pitch=-32768;pitch<=32767;++pitch){
        collector::RemoteCommand command{1,123,static_cast<int16_t>(pitch),600,false};
        modes.accept(command,source,pitch+32769);
        assert(command.pitch==pitch);
    }
    for(int i=2;i<20;++i){
        modes.accept({1,static_cast<int16_t>(i*50),850,600,false},source,i*100000);
        assert(boot.step(i*100000,true,false,false)==Action::Update);
    }
    assert(boot.step(2000000,true,false,true)==Action::Update);
    assert(std::strcmp(boot.state(),"trajectory_complete")==0);
    assert(boot.step(2100000,true,true,true)==Action::Update);
    assert(boot.step(2200000,false,true,false)==Action::None);
    assert(boot.step(2300000,true,true,true)==Action::None); // re-enable never homes
    StartupPose unavailable;
    assert(unavailable.step(9000000,true,false,true)==Action::None);
    assert(unavailable.step(14000000,true,true,true)==Action::None);
    assert(std::strcmp(unavailable.state(),"feedback_unavailable")==0);
    assert(unavailable.step(14100000,true,true,true)==Action::None);
    std::cout<<"PASS: mode-only input across all 65536 pitch values, raw preservation, independent boot pitch, stop/re-enable and deadline\n";
}
