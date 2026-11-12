#include "../upstream/StackChan/firmware/main/collector/timed_yaw_turn.h"
#include "../upstream/StackChan/firmware/main/stackchan/motion/timed_rotation_io.h"
#include <cassert>
#include <vector>
using namespace collector;
struct Axis {
    int cancels=0, starts=0, stops=0, pwm=0, cuts=0; bool start_ok=true, stop_ok=true;
    void cancelMotion(){ ++cancels; }
    bool beginTimedRotation(int value){ ++starts; pwm=value; return start_ok; }
    void cutRotationPower(){ ++cuts; pwm=0; }
    bool endTimedRotation(){ ++stops; if(stop_ok)pwm=0; return stop_ok; }
};
struct Bus {
    int low=20,high=1003,torque=0,pwm=0,fail_register=0;
    int status=0; bool mode_fail=false,stop_fail=false,mode_reject=false,mode_ignore=false,restore_ignore=false; std::vector<int> restore_torque;
    int getState(){return status;} int getLastError(){return 0;}
    int readByte(int,int reg){status=0;return reg==40?torque:1;}
    int readWord(int,int reg){status=0;return reg==44?pwm:reg==9?low:high;}
    int EnableTorque(int,int value){status=0;if(stop_fail&&!value)return 0;torque=value;return 1;}
    int WritePWM(int,int value){status=0;pwm=value;return 1;}
    int PWMMode(int){status=mode_reject?32:0;if(mode_ignore||mode_reject)return 1;low=high=0;return mode_fail?0:1;}
    int writeWord(int,int reg,int value){status=0;restore_torque.push_back(torque);if(restore_ignore)return 1;if(fail_register==reg)return 0;(reg==9?low:high)=value;return 1;}
};
int main(){
    TimedYawTurn t; Axis a;
    assert(!t.submit(99,true,100)); assert(!t.submit(2001,true,100)); assert(!t.submit(1000,false,100));
    assert(t.submit(1000,true,100)); assert(!t.submit(1000,true,101));
    t.tick(a,101,true); assert(t.busy()&&a.starts==1&&a.pwm==400);
    t.tick(a,1000100,true); assert(a.stops==0);
    a.stop_ok=false;t.tick(a,1000101,true);assert(t.busy()&&a.stops==1);
    t.tick(a,1000102,true);assert(a.stops==1);
    a.stop_ok=true;t.tick(a,1020101,true);assert(!t.busy()&&a.pwm==0&&t.phase==TimedYawTurn::Phase::Complete);
    t.tick(a,1040101,true);assert(a.starts==1); // no replay
    assert(t.submit(500,true,1100000));t.tick(a,1100001,true);assert(a.pwm==-400);
    t.cancel();t.tick(a,1100100,true);assert(!t.busy()&&a.pwm==0&&t.phase==TimedYawTurn::Phase::Cancelled); // stop then enable race
    assert(t.submit(500,true,1200000));t.cancel();t.tick(a,1200001,true);assert(a.starts==2); // queued stop never starts
    assert(t.submit(500,true,1300000));t.tick(a,2300000,true);assert(t.phase==TimedYawTurn::Phase::Expired);
    assert(t.submit(500,true,2400000));a.start_ok=false;t.tick(a,2400001,true);assert(t.phase==TimedYawTurn::Phase::Failed&&a.pwm==0);
    TimedYawTurn broken; Axis broken_axis; broken_axis.stop_ok=false;
    assert(broken.submit(100,true,1));broken.tick(broken_axis,2,true);broken.tick(broken_axis,100002,true);
    broken.tick(broken_axis,400002,true);assert(broken.phase==TimedYawTurn::Phase::PowerCut&&broken_axis.pwm==0&&broken_axis.cuts==1);
    assert(!broken.submit(100,true,500000));broken.tick(broken_axis,600002,true);assert(broken_axis.cuts==2&&broken_axis.starts==1);
    Bus b;stackchan::motion::TimedRotationIO io;
    assert(io.positionMode(b,1));
    assert(io.begin(b,1,400)&&b.pwm==409&&b.torque==1&&b.low==0);
    assert(!io.positionMode(b,1)); // rebooted ESP must reject 0/0 wheel mode
    b.stop_fail=true;assert(!io.end(b,1)&&io.saved&&b.low==0); // never restore while torque uncertain
    b.stop_fail=false;b.fail_register=11;assert(!io.end(b,1)&&io.saved&&b.torque==0);
    b.fail_register=0;assert(io.end(b,1)&&!io.saved&&b.low==20&&b.high==1003);
    for(int torque:b.restore_torque)assert(torque==0);
    b.restore_ignore=true;assert(io.begin(b,1,400));assert(!io.end(b,1)&&io.saved);b.restore_ignore=false;assert(io.end(b,1));
    b.mode_reject=true;assert(!io.begin(b,1,400)&&b.torque==0);assert(io.end(b,1));b.mode_reject=false;
    b.mode_ignore=true;assert(!io.begin(b,1,400)&&b.torque==0);assert(io.end(b,1));b.mode_ignore=false;
    b.mode_fail=true;assert(!io.begin(b,1,-400)&&io.saved);assert(io.end(b,1)&&b.low==20&&b.high==1003&&b.pwm==0);
}
