#include "../upstream/StackChan/firmware/main/collector/startup_pose.h"
#include "../upstream/StackChan/firmware/main/collector/remote_actuation.h"
#include <cassert>
#include <cstring>
#include <iostream>
using collector::StartupPose;
using Action = StartupPose::Action;
struct YawAxis {
    int angle=0,speed=0,moves=0;bool sync=false;
    void setAutoAngleSyncEnabled(bool enabled){sync=enabled;}
    void moveWithSpeed(int target,int velocity){angle=target;speed=velocity;++moves;}
};
int main()
{
    StartupPose boot;
    assert(StartupPose::kPitch == 130);
    assert(!collector::kRemotePitchControlEnabled);
    assert(boot.step(0,true,false,true) == Action::None);
    assert(boot.step(100000,true,true,true) == Action::Start);
    YawAxis yaw;
    // Changing every representable remote pitch must neither reach a pitch
    // actuator nor change yaw/speed. Raw mapped input stays intact for recording.
    for(int pitch=-32768;pitch<=32767;++pitch){
        collector::RemoteCommand command{1,123,static_cast<int16_t>(pitch),600,false};
        auto target=collector::apply_remote_yaw(yaw,command,pitch==-32768);
        assert(yaw.angle==-123&&yaw.speed==600&&target.yaw==-123);
        assert(command.pitch==pitch);
        assert(yaw.sync==(pitch==-32768));
    }
    assert(yaw.moves==65536);
    // Yaw-only input cannot preempt or restart the one-shot startup pitch.
    for(int i=2;i<20;++i){
        collector::apply_remote_yaw(yaw,{1,static_cast<int16_t>(i*50),850,600,false},false);
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
    auto target=collector::apply_remote_yaw(yaw,{1,32767,-32768,1000,false},true);
    assert(target.yaw==-1280&&target.speed==1000);
    target=collector::apply_remote_yaw(yaw,{1,-32768,32767,0,false},false);
    assert(target.yaw==1280&&target.speed==0);
    std::cout<<"PASS: yaw-only actuator routing across all 65536 pitch inputs, raw preservation, limits, independent boot pitch, stop/re-enable and deadline\n";
}
