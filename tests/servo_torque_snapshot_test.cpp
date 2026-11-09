#include "../upstream/StackChan/firmware/main/stackchan/motion/servo.h"
#include <cassert>
#include <iostream>

struct ReadOnlyBus {
    unsigned long IOTimeOut=127;
    int raw=0,error=0,status=0,reads=0,received=8;
    int Read(uint8_t id,uint8_t address,uint8_t* values,uint8_t size) {
        assert(id==1&&address==40&&size==8&&IOTimeOut==2);
        const uint8_t reply[]{static_cast<uint8_t>(raw),0,0x01,0xc2,0x12,0x34,0xab,0xcd};
        for(int i=0;i<size;++i)values[i]=reply[i];
        ++reads;return received;
    }
    int getLastError(){assert(IOTimeOut==2);return error;}
    int getState(){assert(IOTimeOut==2);return status;}
    void WritePos()=delete;
    void EnableTorque()=delete;
};
struct ReadOnlyAxis {
    stackchan::motion::ServoDiagnostics cached;
    stackchan::motion::ServoTorqueSnapshot snapshot;
    int reads=0;
    auto diagnostics()const{return cached;}
    auto readTorqueSnapshot(){++reads;return snapshot;}
    void WritePos()=delete;
    void EnableTorque()=delete;
};
struct ConfigBus {
    unsigned long IOTimeOut=9; int reads=0;
    int Read(uint8_t id,uint8_t address,uint8_t* values,uint8_t size) {
        assert(id==2&&address==9&&size==19&&IOTimeOut==2);
        const uint8_t block[19]{0,20,3,235,70,140,40,3,232,1,44,5,15,0,0,0,16,1,1};
        for(int i=0;i<size;++i)values[i]=block[i];
        ++reads;return size;
    }
    int getLastError(){return 0;} int getState(){return 0;}
};
int main() {
    using stackchan::motion::readServoTorqueSnapshot;
    {   // EEPROM block 9..27 decodes limits, protection, torque, gains and punch without any write.
        ConfigBus bus; const auto s=stackchan::motion::readServoDeadbandSnapshot(bus,2,99);
        assert(s.valid&&bus.reads==1&&bus.IOTimeOut==9&&s.min_angle_raw==20&&s.max_angle_raw==1003);
        assert(s.max_temperature_raw==70&&s.max_voltage_raw==140&&s.min_voltage_raw==40&&s.max_torque_raw==1000);
        assert(s.phase_raw==1&&s.unloading_raw==44&&s.led_alarm_raw==5&&s.p_raw==15&&s.d_raw==0&&s.i_raw==0);
        assert(s.punch_raw==16&&s.cw_raw==1&&s.ccw_raw==1);
    }
    ReadOnlyBus bus;
    for(const int raw:{0,1,2,255}) {
        bus.raw=raw;
        const auto before=bus.reads;
        const auto s=readServoTorqueSnapshot(bus,1,1234);
        assert(s.supported&&s.attempted&&s.valid&&s.raw==raw);
        assert(s.timestamp_us==1234&&s.transport_error==0&&s.servo_status==0);
        assert(s.received_bytes==8&&s.goal_position_raw==450&&s.goal_time_raw==0x1234&&s.goal_speed_raw==0xabcd);
        assert(bus.IOTimeOut==127&&bus.reads==before+1);
    }
    for(const int received:{-1,0,1,7,9}) {
        bus.received=received;const auto s=readServoTorqueSnapshot(bus,1,2345);
        assert(s.attempted&&!s.valid&&s.raw==-1&&s.goal_position_raw==-1&&bus.IOTimeOut==127);
    }
    bus.received=8;bus.raw=0;bus.error=1;
    auto failed=readServoTorqueSnapshot(bus,1,3456);
    assert(!failed.valid&&failed.transport_error==1&&failed.raw==-1&&bus.IOTimeOut==127);
    bus.error=0;bus.status=4;
    failed=readServoTorqueSnapshot(bus,1,4567);
    assert(!failed.valid&&failed.servo_status==4&&bus.IOTimeOut==127);

    // Failed or unsupported probes must still be bounded; their attempted flag
    // cannot serve as the scheduling guard. All methods above expose reads only.
    ReadOnlyAxis axis;
    axis.cached.last_position_write.valid=true;
    axis.cached.last_position_write.timestamp_us=100;
    stackchan::motion::ServoTorqueCommandProbe probe;
    stackchan::motion::ServoFeedback feedback;
    feedback.valid=true;feedback.angle=-28;feedback.position_raw=452;
    probe.beforeCancel(axis);probe.afterUpdate(axis);assert(axis.reads==0);
    probe.begin(axis,7,-38,feedback);
    assert(axis.reads==1&&probe.command_count==7&&probe.target==-38&&probe.start_feedback.position_raw==452);
    for(int i=0;i<100;++i)probe.afterUpdate(axis);
    assert(axis.reads==1&&!probe.first_position_write.valid);
    axis.cached.last_position_write.timestamp_us=200;
    axis.snapshot=readServoTorqueSnapshot(bus,1,5000); // invalid status deliberately retained
    probe.afterUpdate(axis);
    assert(axis.reads==2&&probe.first_position_write.timestamp_us==200&&!probe.after_first_write.valid);
    axis.cached.last_position_write.timestamp_us=300;
    for(int i=0;i<100;++i)probe.afterUpdate(axis);
    assert(axis.reads==2&&probe.first_position_write.timestamp_us==200);
    probe.beforeCancel(axis);
    for(int i=0;i<100;++i){probe.beforeCancel(axis);probe.afterUpdate(axis);}
    assert(axis.reads==3&&probe.before_cancel.timestamp_us==5000&&probe.command_count==7);
    probe.begin(axis,8,12,feedback);
    assert(axis.reads==4&&probe.command_count==8&&!probe.after_first_write.attempted&&!probe.before_cancel.attempted);
    probe.beforeCancel(axis); // close before any write
    axis.cached.last_position_write.timestamp_us=400;
    probe.afterUpdate(axis);probe.beforeCancel(axis);
    assert(axis.reads==5&&!probe.first_position_write.valid);
    bus.status=0;axis.snapshot=readServoTorqueSnapshot(bus,1,6000);
    probe.begin(axis,9,12,feedback);
    axis.cached.last_position_write.timestamp_us=500;
    axis.cached.last_position_write.valid=false;
    probe.afterUpdate(axis);assert(axis.reads==6);
    axis.cached.last_position_write.valid=true;
    probe.afterUpdate(axis);probe.beforeCancel(axis);
    assert(axis.reads==8&&probe.before.valid&&probe.after_first_write.valid&&probe.before_cancel.valid);
    std::cout<<"PASS: read-only torque/goal snapshots, raw bytes, errors, timeout and three-read lifecycle\n";
}
