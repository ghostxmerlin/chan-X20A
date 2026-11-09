#include "../upstream/StackChan/firmware/main/collector/face_tracking.h"
#include "../upstream/StackChan/firmware/main/collector/web_control.h"
#include "../upstream/StackChan/firmware/main/stackchan/motion/servo.h"
#include <hal/hal.h>
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>
uint32_t test_tick_ms=0;
TestHal& GetHAL(){static TestHal h;return h;}
using namespace collector;
using A=FaceTracking::Action;
struct Scenario {
    FaceTracking p;
    TrackingInput in;
    Scenario() {
        in.now_us=1000000; in.control_enabled=true; in.feedback_valid=true;
        in.feedback_us=in.now_us;in.measured=0;in.lower=-1280;in.upper=1280;in.settled=true;
        in.mode=1;in.mode_requested_us=900000;in.frame_mode=1;
        in.frame_seq=1;in.frame_us=in.now_us;in.face_count=1;in.x=500;in.y=150;in.w=80;in.h=80;
        p.request(true,900001);
    }
    A tick(int64_t advance=100000,bool frame=true) {
        in.now_us+=advance;in.feedback_us=in.now_us;
        if(frame){++in.frame_seq;in.frame_us=in.now_us;}
        return p.step(in);
    }
    A start(){assert(p.step(in)==A::None);return tick();}
};
class Axis:public stackchan::motion::Servo {
public:
    std::vector<int> writes;
    int reads=0,physical=0;bool torque=false,frozen=false;
    Axis(){set_angle_limit({-1280,1280});initPassive();reads=0;}
    int getCurrentAngle() override {++reads;return physical;}
    stackchan::motion::ServoFeedback readFeedback() override {stackchan::motion::ServoFeedback f;f.valid=true;f.angle=physical;return f;}
    void setTorqueEnabled(bool v) override {torque=v;}
    bool getTorqueEnabled() override {return torque;}
protected:
    void set_angle_impl(int v) override {writes.push_back(v);if(!frozen) physical=v;torque=true;}
};
// Observe commands from the real Servo curve without inventing physical motion
// or assuming that a position write enables the real servo's torque.
class CommandAxis:public stackchan::motion::Servo {
public:
    struct Write {int angle,raw;};
    std::vector<Write> writes;
    explicit CommandAxis(int measured):measured_(measured){set_angle_limit({-1280,1280});initPassive();}
    int getCurrentAngle() override {return measured_;}
    static int encode(int angle){return 461+angle*16/5/10;} // Same integer operations as ScsServo.
protected:
    void set_angle_impl(int angle) override {writes.push_back({angle,encode(angle)});}
private:
    int measured_;
};
struct CommandRun {int first_final_ms=-1,last_raw=0;};
static CommandRun command_run(int start,int target,int cadence_ms,bool legacy_speed_zero=false){
    test_tick_ms=10000;CommandAxis yaw(start);
    stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=start;
    if(legacy_speed_zero) assert(yaw.moveFromFeedbackWithSpeed(feedback,target,0));
    else assert(apply_tracking_move(yaw,feedback,target));
    CommandRun result;result.last_raw=CommandAxis::encode(start);
    int previous=start;const int direction=target>start?1:-1;
    for(int ms=0;ms<2000;ms+=cadence_ms){
        test_tick_ms=10000+ms;const auto count=yaw.writes.size();yaw.update();
        if(yaw.writes.size()==count)continue;
        const auto command=yaw.writes.back();
        assert(command.angle>=std::min(start,target)&&command.angle<=std::max(start,target));
        assert(std::abs(command.angle-previous)<=40); // bounded slew per 20 ms tick at speed 1000
        assert((command.angle-previous)*direction>=0);
        assert(command.raw>=std::min(CommandAxis::encode(start),CommandAxis::encode(target))&&
               command.raw<=std::max(CommandAxis::encode(start),CommandAxis::encode(target)));
        if(result.first_final_ms<0&&command.raw==CommandAxis::encode(target))result.first_final_ms=ms;
        result.last_raw=command.raw;previous=command.angle;
    }
    test_tick_ms=12000;yaw.cancelMotion();const auto stopped=yaw.writes.size();
    for(int ms=2000;ms<=4000;ms+=cadence_ms){test_tick_ms=10000+ms;yaw.update();}
    assert(yaw.writes.size()==stopped); // No position write after the unchanged two-second stop.
    return result;
}
static void quantized_command_submission(){
    // .37现场: raw465 decoded to 1.2 degrees. A slot command targets 20 degrees (raw525).
    assert((465-461)*5*10/16==12&&CommandAxis::encode(200)==525);
    const auto legacy=command_run(12,200,60,true);
    assert(legacy.first_final_ms<0); // speed 0 cannot submit the slot within two seconds
    const auto candidate=command_run(12,200,20);
    assert(candidate.first_final_ms>=0&&candidate.first_final_ms<=1000&&candidate.last_raw==525);
    const auto slow=command_run(12,200,60);
    assert(slow.first_final_ms>=0&&slow.first_final_ms<=1800&&slow.last_raw==525);
    for(int start:{-280,-120,-30,0,30,120,280})for(int direction:{-1,1})for(int cadence:{20,40,60})
        command_run(start,start+direction*200,cadence);
    std::cout<<"PASS: slot command submits raw525 within 1s at 20ms and 1.8s at 60ms; bidirectional bounds and 2s stop retained\n";
}
static void guards(){
    Scenario s; assert(s.start()==A::Move);assert(s.p.anchor()==0&&s.p.target()==200);
    assert(s.tick(100000)==A::None); // interval
    assert(s.tick(150000,false)==A::None); // repeated frame cannot make another step
    s.in.measured=60;assert(s.tick(1000000)==A::None); // progress credited inside the budget
    assert(s.tick(1500000)==A::Move); // interval elapsed: next slot command
    s.in.settled=false;assert(s.tick(250000)==A::None);
    s.in.face_count=0;assert(s.tick()==A::Cancel);assert(!s.p.active()&&s.p.requested());
    assert(s.tick()==A::None); // no repeated cancel
    s.in.face_count=1;s.in.settled=true;assert(s.tick(2500000)==A::Move);assert(s.p.anchor()==0);
    s.p.request(false,s.in.now_us);s.p.request(true,s.in.now_us+1);
    assert(s.tick()==A::Cancel); // quick off/on cannot erase pending stop
    assert(s.p.step(s.in)==A::None); // same frame cannot revive
    assert(s.tick()==A::Move);
    Scenario old; old.in.frame_us=900000;assert(old.p.step(old.in)==A::None);
    assert(old.tick(100000,false)==A::None);assert(old.tick()==A::Move);
    Scenario stale;assert(stale.start()==A::Move);
    stale.in.now_us+=700000;stale.in.feedback_us=stale.in.now_us;
    assert(stale.p.step(stale.in)==A::Cancel);assert(!strcmp(stale.p.state(),"frame_stale"));
    Scenario invalid;assert(invalid.start()==A::Move);
    invalid.in.feedback_valid=false;assert(invalid.tick()==A::Cancel);
    invalid.in.feedback_valid=true;assert(invalid.tick()==A::None);
    assert(invalid.tick(2500000)==A::Move);assert(invalid.p.anchor()==0);
    Scenario jump;assert(jump.start()==A::Move);jump.in.measured=300;
    assert(jump.tick()==A::Cancel);assert(!strcmp(jump.p.state(),"fault_feedback_jump"));
    jump.in.measured=0;assert(jump.tick()==A::None);assert(jump.tick()==A::None);
    Scenario mode;assert(mode.start()==A::Move);mode.in.mode=2;mode.in.requested_mode=2;mode.in.mode_requested_us=mode.in.now_us;
    assert(mode.tick()==A::Cancel);assert(mode.p.anchor()==0);
    mode.in.frame_mode=2;assert(mode.tick(2500000)==A::Move);assert(mode.p.anchor()==0);
    Scenario bound;bound.in.measured=1277;assert(bound.start()==A::Move);assert(bound.p.target()==1280);
    bound.in.measured=1280;assert(bound.tick(1000000)==A::None); // progress credited
    assert(bound.tick(1500000)==A::Cancel); // at_limit once a new command is allowed
    Scenario dead;dead.in.x=280;assert(dead.start()==A::None);assert(!strcmp(dead.p.state(),"deadband"));
    Scenario range;assert(range.start()==A::Move);range.in.measured=601;
    assert(range.tick()==A::Cancel);assert(!strcmp(range.p.state(),"fault_anchor_range"));
    Scenario outside;assert(outside.start()==A::Move);outside.in.measured=2000;
    assert(outside.tick()==A::Cancel);assert(outside.p.faulted());
    Scenario bad;bad.in.feedback_valid=false;assert(bad.p.step(bad.in)==A::None);assert(!bad.p.has_anchor());
    Scenario coordinates;coordinates.in.x=620;assert(coordinates.start()==A::None);assert(!coordinates.p.has_anchor());
    Scenario revision;assert(revision.start()==A::Move);
    ++revision.in.mode_revision;assert(revision.tick()==A::Cancel);
    assert(revision.tick(250000)==A::None); // same function request still pending
    revision.in.applied_mode_revision=revision.in.mode_revision;
    assert(revision.tick()==A::None); // retire the old applied generation
    assert(revision.tick(2500000)==A::Move); // same function, new explicit request
    Scenario pending;assert(pending.start()==A::Move);
    pending.in.requested_mode=2;assert(pending.tick()==A::Cancel);
    assert(pending.tick(300000)==A::None); // UART still has detector result
    Scenario stale_feedback;assert(stale_feedback.start()==A::Move);
    stale_feedback.in.now_us+=300000;
    assert(stale_feedback.p.step(stale_feedback.in)==A::Cancel);
    assert(!strcmp(stale_feedback.p.state(),"feedback_stale"));
    Scenario step;assert(step.start()==A::Move);
    step.in.measured=60;assert(step.tick(1000000)==A::None);assert(step.tick(1500000)==A::Move);
    assert(step.p.target()==200); // still below the first slot centre: aim at it again
    Scenario drift;assert(drift.start()==A::Move);drift.in.measured=-30;
    assert(drift.tick(300000)==A::None); // no new command while progress remains unresolved
    assert(drift.tick(1700000)==A::Cancel);assert(drift.p.faulted());
    Scenario manual;assert(manual.start()==A::Move);
    manual.p.request(false,manual.in.now_us,"manual_override");
    assert(!manual.p.requested()&&!manual.p.active());
    assert(manual.tick()==A::Cancel);assert(manual.tick()==A::None);
    assert(!strcmp(manual.p.state(),"manual_override"));
    Scenario broken;assert(broken.start()==A::Move);broken.in.feedback_valid=false;
    assert(broken.tick()==A::Cancel);broken.in.feedback_valid=true;broken.in.measured=300;
    assert(broken.tick()==A::None);assert(broken.p.faulted()); // loss must not erase jump history
    Scenario race;assert(race.start()==A::Move);
    ManualYaw queued;
    assert(queued.submit(50,kWebYawDefaultSpeed,false,true,true,race.p.active(),0,race.in.now_us,race.in.now_us));
    race.p.request(false,race.in.now_us,"manual_override");
    assert(race.p.take_cancel()); // motor's second check after policy evaluation
    assert(queued.take(race.in.now_us,true));assert(!race.p.take_cancel());
}
static void real_servo(){
    Axis yaw;Scenario s;assert(s.start()==A::Move);
    stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=0;
    assert(apply_tracking_move(yaw,feedback,s.p.target()));assert(yaw.reads==0);
    int previous=0;int max_delta=0;
    for(unsigned i=1;i<=300;++i){test_tick_ms=i*20;yaw.update();if(!yaw.writes.empty()){
        int value=yaw.writes.back();assert(value>=0&&value<=200);
        max_delta=std::max(max_delta,std::abs(value-previous));previous=value;
    }}
    assert(max_delta<=40); // bounded slew per 20 ms tick at speed 1000
    assert(previous==200&&yaw.isTrajectoryComplete());
    assert(!yaw.torque); // factory release, not an active position hold
    feedback.angle=200;assert(apply_tracking_move(yaw,feedback,400));
    for(unsigned i=301;i<320;++i){test_tick_ms=i*20;yaw.update();}
    yaw.cancelMotion();const auto count=yaw.writes.size();
    for(unsigned i=320;i<400;++i){test_tick_ms=i*20;yaw.update();}
    assert(yaw.writes.size()==count&&!yaw.torque&&yaw.reads==0);
    feedback.valid=false;assert(!apply_tracking_move(yaw,feedback,30));
    std::cout<<"PASS: actual Servo bounded step/speed, fresh feedback only, cancel clears torque and emits no later position writes\n";
}
static void no_progress(){
    // Software completion is not evidence that the physical encoder moved.
    Scenario stalled;assert(stalled.start()==A::Move);
    for(int i=0;i<9;++i) assert(stalled.tick(200000)==A::None);
    assert(stalled.p.commands()==1);
    assert(stalled.tick(200000)==A::Cancel);
    assert(stalled.p.faulted()&&!strcmp(stalled.p.state(),"fault_no_progress"));
    const auto commands=stalled.p.commands(),cancels=stalled.p.cancels();
    for(int i=0;i<20;++i) assert(stalled.tick(200000)==A::None);
    assert(stalled.p.commands()==commands&&stalled.p.cancels()==cancels);
    stalled.p.request(true,stalled.in.now_us); // idempotent enable cannot clear the fault
    assert(stalled.tick()==A::None&&stalled.p.faulted());
    stalled.p.request(false,stalled.in.now_us);stalled.p.request(true,stalled.in.now_us+1);
    assert(stalled.tick()==A::None);assert(stalled.tick()==A::Move);

    Scenario unsettled;assert(unsettled.start()==A::Move);unsettled.in.settled=false;
    assert(unsettled.tick(1000000)==A::None);
    assert(unsettled.tick(1000000)==A::Cancel); // checked before !settled gate
    Scenario subquantum;assert(subquantum.start()==A::Move);subquantum.in.measured=2;
    assert(subquantum.tick(1000000)==A::None);
    assert(subquantum.tick(1000000)==A::Cancel); // < one encoder step cannot clear a full-size command

    Scenario normal;assert(normal.start()==A::Move);normal.in.settled=false;
    normal.in.measured=60;assert(normal.tick(1000000)==A::None); // five degrees of real progress
    assert(normal.tick(1500000)==A::None&&!normal.p.faulted());
    normal.in.settled=true;assert(normal.tick()==A::Move);
    normal.in.measured=120;assert(normal.tick(1000000)==A::None); // progress credited inside the budget; interval not yet elapsed
    assert(normal.tick(1500000)==A::Move);
    assert(!normal.p.faulted()&&normal.p.anchor()==0);

    Scenario tiny;tiny.in.upper=2;assert(tiny.start()==A::Move);assert(tiny.p.target()==2);
    tiny.in.measured=2;assert(tiny.tick(1000000)==A::None); // threshold capped at 0.2 degree: progress credited
    assert(tiny.tick(1500000)==A::Cancel); // at_limit once the interval allows a new command
    assert(tiny.tick(1500000)==A::None&&!tiny.p.faulted());

    // Reproduce the live short command followed by intentional central stop.
    Scenario center_stop;center_stop.in.x=520;assert(center_stop.start()==A::Move);
    center_stop.in.x=280;assert(center_stop.tick(290000)==A::Cancel);
    assert(!strcmp(center_stop.p.state(),"deadband"));
    for(int i=0;i<30;++i) assert(center_stop.tick(100000)==A::None&&!center_stop.p.faulted());
    center_stop.in.x=520;assert(center_stop.tick(100000)==A::Move);
    assert(center_stop.p.target()==200&&center_stop.p.anchor()==0);
    assert(center_stop.tick(1700000)==A::None);
    assert(center_stop.tick(10000)==A::Cancel); // 0.29 + 1.71 active seconds
    assert(center_stop.p.faulted()&&!strcmp(center_stop.p.state(),"fault_no_progress"));

    Scenario gap;assert(gap.start()==A::Move);gap.in.face_count=0;
    assert(gap.tick(100000)==A::Cancel);gap.in.face_count=1;
    assert(gap.tick(2500000)==A::Move);assert(gap.p.target()==200);
    assert(gap.tick(1500000)==A::None);
    assert(gap.tick(150000)==A::None);
    assert(gap.tick(250000)==A::Cancel); // 0.1 + 1.9 active seconds; pause adds no budget
    assert(gap.p.commands()==2&&gap.p.faulted()&&gap.p.anchor()==0);

    Scenario mode_gap;assert(mode_gap.start()==A::Move);mode_gap.in.requested_mode=2;
    assert(mode_gap.tick(100000)==A::Cancel);
    assert(mode_gap.tick(2400000)==A::None&&!mode_gap.p.faulted());
    mode_gap.in.requested_mode=1;assert(mode_gap.tick(100000)==A::Move);
    assert(mode_gap.tick(1900000)==A::Cancel);
    assert(mode_gap.p.faulted()&&!strcmp(mode_gap.p.state(),"fault_no_progress"));

    Scenario feedback_gap;assert(feedback_gap.start()==A::Move);feedback_gap.in.feedback_valid=false;
    assert(feedback_gap.tick(100000)==A::Cancel);
    for(int i=0;i<30;++i) assert(feedback_gap.tick(100000)==A::None&&!feedback_gap.p.faulted());
    feedback_gap.in.feedback_valid=true;assert(feedback_gap.tick(100000)==A::None);
    assert(feedback_gap.tick(100000)==A::Move);
    assert(feedback_gap.tick(1900000)==A::Cancel); // invalid samples cannot reset accumulated budget
    std::cout<<"PASS: physical no-progress deadline, software completion rejection, encoder quantum, gap retention and latched stop\n";
}
static void direction_recovery(){
    for(int direction:{-1,1}) {
        Scenario s;s.in.x=direction>0?520:120;assert(s.start()==A::Move);
        assert(s.p.target()==direction*200);
        s.in.x=280;assert(s.tick(290000)==A::Cancel);
        for(int i=0;i<30;++i) assert(s.tick(100000)==A::None&&!s.p.faulted());
        s.in.x=direction>0?120:520;
        assert(s.tick(100000)==A::Move); // resume toward the face, not the cancelled target
        assert(s.p.target()==-direction*200&&s.p.anchor()==0);
        assert(s.tick(1700000)==A::None);
        assert(s.tick(10000)==A::Cancel&&s.p.faulted()); // reversal preserves 0.29s consumed
    }
    Scenario repeated;repeated.in.x=520;assert(repeated.start()==A::Move);
    for(int i=0;i<8;++i) {
        repeated.in.x=(i%2==0)?120:520;
        assert(repeated.tick(250000)==A::Cancel); // cancel before reversing an active trajectory
        if(i==7) break;
        assert(!repeated.p.faulted());
        assert(repeated.tick(2500000)==A::Move);
        assert(repeated.p.target()==((i%2==0)?-200:200));
    }
    assert(repeated.p.faulted()&&repeated.p.commands()==8);
    assert(!strcmp(repeated.p.state(),"fault_no_progress"));
    std::cout<<"PASS: both-direction resume, active reversal cancellation, repeated reversals retain 2s budget\n";
}
static void real_stall(){
    Axis yaw;yaw.frozen=true;Scenario s;assert(s.start()==A::Move);
    stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=0;
    assert(apply_tracking_move(yaw,feedback,s.p.target()));
    int cancelled_at=0;size_t writes_at_cancel=0;
    for(int tick=1;tick<=200;++tick){
        test_tick_ms+=20;s.in.measured=yaw.physical;s.in.settled=yaw.isTrajectoryComplete();
        const auto action=s.tick(20000);
        assert(action!=A::Move); // valid but fixed encoder must not invite another trajectory
        if(action==A::Cancel){
            assert(!cancelled_at);cancelled_at=tick*20;
            yaw.cancelMotion();writes_at_cancel=yaw.writes.size();
        }
        if(s.p.active()) yaw.update();
    }
    assert(cancelled_at==2000&&s.p.faulted()&&!yaw.torque&&yaw.physical==0);
    assert(writes_at_cancel>0&&yaw.writes.size()==writes_at_cancel&&s.p.commands()==1);
    std::cout<<"PASS: actual Servo with frozen physical feedback cancels at 2s and emits no later position writes\n";
}
static void real_paused_stall(bool reverse=false){
    Axis yaw;yaw.frozen=true;Scenario s;s.in.x=520;assert(s.start()==A::Move);
    stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=0;
    assert(apply_tracking_move(yaw,feedback,s.p.target()));
    size_t paused_writes=0,final_writes=0;int cancels=0;
    for(int tick=1;tick<=600;++tick){
        test_tick_ms+=10;s.in.measured=yaw.physical;s.in.settled=yaw.isTrajectoryComplete();
        if(tick==29) s.in.x=280; // intentional central cancellation at 0.29s
        if(tick==329) s.in.x=reverse?120:520; // resume after 3s with no encoder progress
        const auto action=s.tick(10000);
        if(action==A::Cancel){
            ++cancels;yaw.cancelMotion();
            if(cancels==1){assert(tick==29);paused_writes=yaw.writes.size();}
            else {assert(cancels==2&&tick==500);final_writes=yaw.writes.size();}
        } else if(action==A::Move){
            assert(tick==329&&s.p.target()==(reverse?-200:200));assert(apply_tracking_move(yaw,feedback,s.p.target()));
        }
        if(s.p.active()) yaw.update();
        if(tick>=29&&tick<329) assert(!s.p.faulted()&&yaw.writes.size()==paused_writes&&!yaw.torque);
    }
    assert(cancels==2&&s.p.faulted()&&s.p.commands()==2&&s.p.anchor()==0);
    assert(final_writes>paused_writes&&yaw.writes.size()==final_writes&&!yaw.torque);
    std::cout<<"PASS: actual Servo pauses 3s without a false stall; 0.29s + 1.71s active time latches once\n";
}
static void real_reversal(){
    for(int direction:{-1,1}) {
        Axis yaw;stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=0;
        assert(apply_tracking_move(yaw,feedback,direction*200));
        for(int i=0;i<20;++i){test_tick_ms+=20;yaw.update();}
        const int measured=yaw.physical;yaw.cancelMotion();feedback.angle=measured;
        const int target=measured-direction*200;
        assert((target-measured)*direction<0); // reversal heads back past the measured start
        assert(apply_tracking_move(yaw,feedback,target));
        int previous=measured;
        for(int i=0;i<300;++i){
            test_tick_ms+=20;const auto count=yaw.writes.size();yaw.update();
            if(yaw.writes.size()==count) continue;
            const int value=yaw.writes.back();
            assert(value>=std::min(measured,target)&&value<=std::max(measured,target));
            assert((value-previous)*direction<=0&&std::abs(value-previous)<=40);
            previous=value;
        }
        assert(previous==target&&yaw.isTrajectoryComplete()&&yaw.reads==0);
    }
    std::cout<<"PASS: actual Servo reversals start at feedback, travel one slot and retain bounded command slew\n";
}
int main(){quantized_command_submission();direction_recovery();no_progress();guards();real_servo();real_stall();real_paused_stall();real_paused_stall(true);real_reversal();}
