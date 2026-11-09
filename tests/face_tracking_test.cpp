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
    // First step builds feedback confidence; the first frame is the look's reference.
    A start(){assert(p.step(in)==A::None);assert(tick()==A::None);return tick();}
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
// Default face: x=500,w=80 → centre 540, 220 px right of centre.
// 220 px × 62°/640 px = 21.3° to the face; the command adds 3° precompensation.
static constexpr int kAim=213,kCommand=243;
// Feed a still, measured arrival (trajectory complete) until the turn counts as finished.
static int arrive(Scenario& s,int angle){
    s.in.settled=true;s.in.measured=angle;int ticks=0;
    while(s.p.moving()){assert(++ticks<40);assert(s.tick(100000,false)==A::None);}
    return ticks;
}
static void one_turn(){
    Scenario s;assert(s.start()==A::Move);
    assert(s.p.target()==kCommand&&s.p.anchor()==0&&s.p.last_turn().aim==kAim&&!strcmp(s.p.state(),"turning"));
    // The camera is moving: lost faces, faces on the other side and glitches are ignored.
    s.in.settled=false;s.in.measured=80;s.in.face_count=0;assert(s.tick()==A::None);
    s.in.face_count=1;s.in.x=40;assert(s.tick()==A::None);
    s.in.x=600;s.in.w=40;assert(s.tick()==A::None);
    assert(s.p.moving()&&s.p.commands()==1&&s.p.cancels()==0);
    s.in.x=280;s.in.w=80; // the face is now centred in the view
    assert(!strcmp(s.p.state(),"turning"));
    const int ticks=arrive(s,kAim);
    assert(ticks==4); // first stable sample, then 300 ms ≥ 250 ms at 100 ms feedback
    assert(!strcmp(s.p.state(),"looking")&&s.p.last_turn().settled&&!s.p.last_turn().by_deadline);
    assert(s.p.last_turn().arrived==kAim&&s.p.last_turn().command==kCommand&&!s.p.last_turn().returning);
    // Frames received within 300 ms of arrival may still show the moving view.
    s.in.x=500;assert(s.tick(100000)==A::None);assert(s.tick(100000)==A::None);assert(s.p.commands()==1);
    // First frame after the look delay: face centred → release, no further turn.
    s.in.x=280;assert(s.tick(200000)==A::Cancel);assert(!strcmp(s.p.state(),"deadband"));
    for(int i=0;i<30;++i) assert(s.tick()==A::None);
    assert(s.p.commands()==1&&!s.p.faulted());
    // Calibration: the face moved 220 px for 21.3° → 10.3 px/deg (assumed 10.3).
    assert(s.p.calibration().samples==1&&s.p.calibration().last_px_per_deg_x10==103&&s.p.calibration().sum_px_per_deg_x10==103);
    std::cout<<"PASS: one look → one turn; vision ignored while turning; arrival by stable feedback; look delay; calibration\n";
}
static void continues_without_interval(){
    Scenario s;assert(s.start()==A::Move);
    s.in.settled=false;s.in.measured=60;assert(s.tick()==A::None);
    arrive(s,131); // stopped 4° short of the face estimate
    // The face is still off centre at the next look: one reference frame, then turn.
    assert(s.tick(400000)==A::None);assert(!strcmp(s.p.state(),"observing"));assert(s.tick()==A::Move);
    assert(s.p.target()==131+kAim+30&&s.p.commands()==2&&s.p.anchor()==0);
    // A large single correction is not split into 20° slots.
    Scenario wide;wide.in.x=0;wide.in.w=40; // centre 20 → 300 px left = 29.0°
    assert(wide.start()==A::Move);assert(wide.p.target()==-290-30);
    std::cout<<"PASS: next turn follows arrival immediately; a whole correction is one command\n";
}
static void deadband(){
    Scenario inside;inside.in.x=362; // centre 402: 82 px ≤ 82 px (8°)
    assert(inside.start()==A::None);assert(!strcmp(inside.p.state(),"deadband"));
    for(int i=0;i<20;++i) assert(inside.tick()==A::None);
    assert(inside.p.commands()==0&&inside.p.has_anchor());
    Scenario outside;outside.in.x=363; // centre 403: 83 px
    assert(outside.start()==A::Move);assert(outside.p.target()==80+30);
    Scenario left;left.in.x=197; // centre 237: 83 px left
    assert(left.start()==A::Move);assert(left.p.target()==-80-30);
    std::cout<<"PASS: 8° deadband (82 px) both sides\n";
}
static void lost_returns_to_anchor(bool frames_stop){
    Scenario s;assert(s.start()==A::Move);
    s.in.settled=false;s.in.measured=120;assert(s.tick()==A::None);
    arrive(s,200);assert(s.tick(400000)==A::None);assert(s.tick()==A::Move); // person still off to the right
    s.in.settled=false;s.in.measured=300;assert(s.tick()==A::None);
    arrive(s,400);
    // Face gone: release now, return only after 1.5 s.
    s.in.face_count=0;
    if(frames_stop) {
        int cancels=0;int64_t elapsed=0;
        while(!s.p.moving()){
            const auto a=s.tick(100000,false);elapsed+=100000;assert(elapsed<3500000);
            if(a==A::Cancel)++cancels;else assert(a==A::None||a==A::Move);
        }
        // 300 ms look delay + 700 ms frame lifetime + 1.5 s return delay.
        assert(cancels==1&&elapsed>=2500000&&elapsed<=2600000);
    } else {
        assert(s.tick(400000)==A::Cancel);assert(!strcmp(s.p.state(),"face_lost"));
        for(int i=0;i<14;++i) assert(s.tick()==A::None);
        assert(s.tick()==A::Move);
    }
    assert(!strcmp(s.p.state(),"returning")&&s.p.last_turn().returning);
    assert(s.p.target()==-30); // anchor 0, 3° beyond
    assert(std::abs(s.p.target()-400)>kWebYawMaxDeltaTenths); // more than the old 30° cap
    // The real Servo accepts the whole return and slews within bounds.
    Axis yaw;yaw.physical=400;stackchan::motion::ServoFeedback fb;fb.valid=true;fb.angle=400;
    assert(apply_tracking_move(yaw,fb,s.p.target()));
    int previous=400;
    for(unsigned i=1;i<=200;++i){test_tick_ms=100000+i*20;yaw.update();if(!yaw.writes.empty()){
        const int v=yaw.writes.back();assert(v<=400&&v>=-30&&std::abs(v-previous)<=40);previous=v;}}
    assert(previous==-30);
    // Longer turns slow down so the peak command rate stays at the 20° slot's.
    assert(FaceTracking::turn_speed(200)==1000&&FaceTracking::turn_speed(-430)==465&&FaceTracking::turn_speed(1200)==300);
    s.in.settled=false;
    for(int angle=300;angle>=0;angle-=100){s.in.measured=angle;assert(s.tick(100000,false)==A::None);}
    arrive(s,0);assert(!strcmp(s.p.state(),"returned"));
    for(int i=0;i<50;++i){const auto a=s.tick(100000,!frames_stop);assert(a!=A::Move);}
    assert(s.p.commands()==3&&!s.p.faulted());
    // The face comes back: tracking resumes from the same anchor.
    s.in.face_count=1;s.in.x=500;assert(s.tick()==A::None);assert(s.tick()==A::Move);assert(s.p.anchor()==0&&s.p.target()==kCommand);
    // Near the anchor already: no return turn.
    Scenario near;assert(near.start()==A::Move);near.in.settled=false;near.in.measured=30;assert(near.tick()==A::None);
    arrive(near,40);near.in.face_count=0;
    for(int i=0;i<40;++i) assert(near.tick()!=A::Move);
    assert(near.p.commands()==1);
    std::cout<<"PASS: face lost "<<(frames_stop?"(no frames)":"(num 0)")<<" → back to anchor once after 1.5 s; >30° single return; resume on face\n";
}
static void settle_deadline(){
    Scenario s;assert(s.start()==A::Move);
    s.in.settled=true;int ticks=0;
    while(s.p.moving()){
        s.in.measured=(ticks%2)?150:190; // still hunting: never within 1° for 250 ms
        assert(s.tick(100000,false)==A::None);assert(++ticks<40);
    }
    // First sample marks the trajectory end; 1 s + 30 ms × 24.3° = 1.729 s later is the 19th.
    assert(ticks==19&&s.p.last_turn().by_deadline&&!strcmp(s.p.state(),"looking")&&!s.p.faulted());
    std::cout<<"PASS: arrival deadline scales with distance when feedback never settles\n";
}
static void no_progress(){
    // Software completion is not evidence that the physical encoder moved.
    Scenario s;assert(s.start()==A::Move);
    s.in.settled=true;int64_t elapsed=0;int moves=1;
    while(!s.p.faulted()){
        const auto a=s.tick(100000);elapsed+=100000;assert(elapsed<4000000);
        if(a==A::Move)++moves;
    }
    // 2 s + 20 ms × 24.3° = 2.486 s of active command time; a fresh look cannot reset it.
    assert(elapsed==2500000&&!strcmp(s.p.state(),"fault_no_progress"));
    assert(moves<=2&&s.p.commands()==static_cast<uint32_t>(moves));
    const auto commands=s.p.commands(),cancels=s.p.cancels();
    for(int i=0;i<20;++i) assert(s.tick()==A::None);
    assert(s.p.commands()==commands&&s.p.cancels()==cancels);
    s.p.request(true,s.in.now_us); // idempotent enable cannot clear the fault
    assert(s.tick()==A::None&&s.p.faulted());
    s.p.request(false,s.in.now_us);s.p.request(true,s.in.now_us+1);
    assert(s.tick()==A::None);assert(s.tick()==A::None);assert(s.tick()==A::Move);
    // Five degrees of real progress inside the budget clear it.
    Scenario normal;assert(normal.start()==A::Move);normal.in.settled=false;
    normal.in.measured=60;assert(normal.tick(1000000)==A::None);
    arrive(normal,171);assert(!normal.p.faulted());
    // A long return gets a longer budget: 2 s + 20 ms × 43° = 2.86 s.
    assert(FaceTracking::progress_budget_us(-430)==2860000);
    std::cout<<"PASS: distance-scaled no-progress deadline, retained across looks, latched until off/on\n";
}
static void guards(){
    Scenario s;assert(s.start()==A::Move);
    s.p.request(false,s.in.now_us);s.p.request(true,s.in.now_us+1);
    assert(s.tick()==A::Cancel); // quick off/on cannot erase pending stop
    assert(s.p.step(s.in)==A::None); // same frame cannot revive
    assert(s.tick()==A::None);assert(s.tick()==A::Move);
    Scenario old;old.in.frame_us=900000;assert(old.p.step(old.in)==A::None);
    assert(old.tick(100000,false)==A::None);assert(old.tick()==A::None);assert(old.tick()==A::Move);
    Scenario stale_feedback;assert(stale_feedback.start()==A::Move);
    stale_feedback.in.now_us+=300000;
    assert(stale_feedback.p.step(stale_feedback.in)==A::Cancel);
    assert(!strcmp(stale_feedback.p.state(),"feedback_stale"));
    Scenario invalid;assert(invalid.start()==A::Move);
    invalid.in.feedback_valid=false;assert(invalid.tick()==A::Cancel);
    invalid.in.feedback_valid=true;assert(invalid.tick()==A::None);
    assert(invalid.tick()==A::None); // inside the post-turn look delay
    assert(invalid.tick(300000)==A::None);assert(invalid.tick()==A::Move);assert(invalid.p.anchor()==0);
    Scenario jump;assert(jump.start()==A::Move);jump.in.measured=300;
    assert(jump.tick()==A::Cancel);assert(!strcmp(jump.p.state(),"fault_feedback_jump"));
    jump.in.measured=0;assert(jump.tick()==A::None);assert(jump.tick()==A::None);
    Scenario mode;assert(mode.start()==A::Move);mode.in.mode=2;mode.in.requested_mode=2;mode.in.mode_requested_us=mode.in.now_us;
    assert(mode.tick()==A::Cancel);assert(mode.p.anchor()==0&&!strcmp(mode.p.state(),"mode_changed"));
    mode.in.frame_mode=2;assert(mode.tick(500000)==A::None);assert(mode.tick()==A::Move);assert(mode.p.anchor()==0);
    Scenario bound;bound.in.measured=1230;assert(bound.start()==A::Move);assert(bound.p.target()==1280);
    bound.in.settled=false;bound.in.measured=1260;assert(bound.tick()==A::None);
    arrive(bound,1270);assert(bound.tick(400000)==A::None);assert(bound.tick()==A::Cancel);assert(!strcmp(bound.p.state(),"at_limit"));
    // Clamped to a turn below 5° at the edge: no command, so no false stall latch.
    Scenario edge;edge.in.measured=1240;assert(edge.start()==A::None);assert(!strcmp(edge.p.state(),"at_limit"));
    for(int i=0;i<40;++i) assert(edge.tick()==A::None);
    assert(edge.p.commands()==0&&!edge.p.faulted());
    edge.p.request(false,edge.in.now_us);edge.p.request(true,edge.in.now_us+1);assert(!edge.p.last_turn().valid);
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
    assert(revision.tick(500000)==A::None);assert(revision.tick()==A::Move); // same function, new explicit request
    Scenario pending;assert(pending.start()==A::Move);
    pending.in.requested_mode=2;assert(pending.tick()==A::Cancel);
    assert(pending.tick(300000)==A::None); // UART still has detector result
    Scenario manual;assert(manual.start()==A::Move);
    manual.p.request(false,manual.in.now_us,"manual_override");
    assert(!manual.p.requested()&&!manual.p.active()&&!manual.p.moving());
    assert(manual.tick()==A::Cancel);assert(manual.tick()==A::None);
    assert(!strcmp(manual.p.state(),"manual_override"));
    Scenario broken;assert(broken.start()==A::Move);broken.in.feedback_valid=false;
    assert(broken.tick()==A::Cancel);broken.in.feedback_valid=true;broken.in.measured=300;
    assert(broken.tick()==A::None);assert(broken.p.faulted()); // loss must not erase jump history
    Scenario glitch;glitch.in.x=280;assert(glitch.start()==A::None); // centred, still camera
    glitch.in.x=0;glitch.in.w=40;assert(glitch.tick()==A::None);assert(!strcmp(glitch.p.state(),"frame_jump"));
    assert(glitch.tick()==A::Move); // a second consistent frame is believed
    Scenario race;assert(race.start()==A::Move);
    ManualYaw queued;
    assert(queued.submit(50,kWebYawDefaultSpeed,false,true,true,race.p.active(),0,race.in.now_us,race.in.now_us));
    race.p.request(false,race.in.now_us,"manual_override");
    assert(race.p.take_cancel()); // motor's second check after policy evaluation
    assert(queued.take(race.in.now_us,true));assert(!race.p.take_cancel());
    std::cout<<"PASS: stop races, stale/invalid/jump feedback, mode changes, limits, anchor range, glitches\n";
}
static void real_servo(){
    Axis yaw;Scenario s;assert(s.start()==A::Move);
    stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=0;
    test_tick_ms=0;assert(apply_tracking_move(yaw,feedback,s.p.target()));assert(yaw.reads==0);
    int previous=0;int max_delta=0;
    for(unsigned i=1;i<=300;++i){test_tick_ms=i*20;yaw.update();if(!yaw.writes.empty()){
        int value=yaw.writes.back();assert(value>=0&&value<=kCommand);
        max_delta=std::max(max_delta,std::abs(value-previous));previous=value;
    }}
    assert(max_delta<=40); // bounded slew per 20 ms tick at speed 1000
    assert(previous==kCommand&&yaw.isTrajectoryComplete());
    assert(!yaw.torque); // factory release, not an active position hold
    feedback.angle=kCommand;assert(apply_tracking_move(yaw,feedback,600));
    for(unsigned i=301;i<320;++i){test_tick_ms=i*20;yaw.update();}
    yaw.cancelMotion();const auto count=yaw.writes.size();
    for(unsigned i=320;i<400;++i){test_tick_ms=i*20;yaw.update();}
    assert(yaw.writes.size()==count&&!yaw.torque&&yaw.reads==0);
    feedback.valid=false;assert(!apply_tracking_move(yaw,feedback,30));
    feedback.valid=true;feedback.angle=0;assert(!apply_tracking_move(yaw,feedback,FaceTracking::kMaxCommandTenths+1));
    std::cout<<"PASS: actual Servo bounded slew, fresh feedback only, cancel clears torque and emits no later position writes\n";
}
static void real_stall(){
    Axis yaw;yaw.frozen=true;Scenario s;test_tick_ms=0;assert(s.start()==A::Move);
    stackchan::motion::ServoFeedback feedback;feedback.valid=true;feedback.angle=0;
    assert(apply_tracking_move(yaw,feedback,s.p.target()));
    int cancelled_at=0;size_t writes_at_cancel=0;
    for(int tick=1;tick<=250;++tick){
        test_tick_ms+=20;s.in.measured=yaw.physical;s.in.settled=yaw.isTrajectoryComplete();
        const auto action=s.tick(20000);
        if(action==A::Move) {assert(!cancelled_at);assert(apply_tracking_move(yaw,feedback,s.p.target()));}
        if(action==A::Cancel){
            assert(!cancelled_at);cancelled_at=tick*20;
            yaw.cancelMotion();writes_at_cancel=yaw.writes.size();
        }
        if(s.p.active()) yaw.update();
    }
    assert(cancelled_at>=2480&&cancelled_at<=2520&&s.p.faulted()&&!yaw.torque&&yaw.physical==0);
    assert(writes_at_cancel>0&&yaw.writes.size()==writes_at_cancel&&s.p.commands()<=2);
    std::cout<<"PASS: actual Servo with frozen feedback latches at 2.5 s and emits no later position writes\n";
}
static void realistic_cadence(){
    // motor_task runs every 20 ms, servo feedback every 100 ms, M12 frames every ~140 ms.
    Scenario s;s.in.now_us=1000000;s.in.feedback_us=s.in.now_us;
    int64_t next_frame=s.in.now_us+140000,next_feedback=s.in.now_us+100000;
    int moves=0,cancels=0,stale_states=0;int64_t first_move=0,cancel_at=0;
    s.in.settled=true;
    for(int tick=0;tick<250;++tick){
        s.in.now_us+=20000;
        if(s.in.now_us>=next_feedback){
            s.in.feedback_us=s.in.now_us;next_feedback+=100000;
            // After the first command the head reaches the face estimate within ~0.6 s and stays.
            if(moves&&s.in.now_us-first_move>=100000) s.in.measured=std::min(kAim,s.in.measured+60);
        }
        if(s.in.now_us>=next_frame){
            ++s.in.frame_seq;s.in.frame_us=s.in.now_us;next_frame+=140000;
            // Once arrived, the face sits at the centre of the view.
            if(moves&&s.in.measured==kAim) s.in.x=280;
        }
        s.in.settled=!moves||s.in.now_us-first_move>=600000;
        const auto a=s.p.step(s.in);
        if(a==A::Move){++moves;if(!first_move)first_move=s.in.now_us;}
        if(a==A::Cancel){++cancels;cancel_at=s.in.now_us;}
        if(!strcmp(s.p.state(),"frame_stale")||!strcmp(s.p.state(),"face_lost")) ++stale_states;
    }
    assert(moves==1&&stale_states==0);
    // Exactly one release, when the first post-arrival frame shows the face centred.
    assert(cancels==1&&cancel_at>first_move+600000&&!strcmp(s.p.state(),"deadband"));
    std::cout<<"PASS: 20 ms policy ticks between 140 ms frames never read as a lost face\n";
}
static void hybrid_look(){
    // Still face: two frames within 2° → turn straight to it, no lead.
    Scenario still;assert(still.start()==A::Move);assert(still.p.last_turn().lead==0&&still.p.target()==kCommand);
    // Moving face, not clamped: 540 → 566 px in 200 ms = 2.5° → 12.5°/s × 0.6 s = 7.5° lead.
    Scenario walk;assert(walk.p.step(walk.in)==A::None);assert(walk.tick(100000)==A::None);
    walk.in.x=526;assert(walk.tick(200000)==A::Move);
    assert(walk.p.last_turn().motion==25&&walk.p.last_turn().lead==75&&walk.p.last_turn().motion_dt_us==200000);
    assert(walk.p.last_turn().aim==238+75&&walk.p.target()==238+75+30);
    // Fast face: the lead is capped at 10°.
    Scenario fast;assert(fast.p.step(fast.in)==A::None);assert(fast.tick()==A::None);
    fast.in.x=540;assert(fast.tick()==A::Move);assert(fast.p.last_turn().lead==100);
    // Walking towards the centre: will be inside the band by itself → no turn.
    Scenario back;back.in.x=400;assert(back.p.step(back.in)==A::None);assert(back.tick()==A::None);
    back.in.x=370;assert(back.tick()==A::None);assert(!strcmp(back.p.state(),"deadband")&&back.p.commands()==0);
    // Leading turns do not feed the calibration.
    walk.in.settled=false;walk.in.measured=150;assert(walk.tick()==A::None);arrive(walk,313);
    walk.in.x=280;assert(walk.tick(400000)==A::Cancel);assert(walk.p.calibration().samples==0);
    // Frames more than 600 ms apart: the second is only a new reference.
    Scenario gap;assert(gap.p.step(gap.in)==A::None);assert(gap.tick()==A::None);
    assert(gap.tick(650000)==A::None);assert(!strcmp(gap.p.state(),"observing"));
    assert(gap.tick(100000)==A::Move);
    // At rest with the face already tracked, leaving the band needs no extra frame.
    Scenario rest;rest.in.x=280;assert(rest.start()==A::None);
    rest.in.x=500;assert(rest.tick()==A::Move);assert(rest.p.last_turn().lead==100); // 220 px in one frame: fast
    std::cout<<"PASS: hybrid two-frame look: still → direct, moving → bounded lead, inbound → wait, gap → new reference\n";
}
int main(){quantized_command_submission();hybrid_look();realistic_cadence();one_turn();continues_without_interval();deadband();
    lost_returns_to_anchor(false);lost_returns_to_anchor(true);settle_deadline();no_progress();guards();
    real_servo();real_stall();real_reversal();}
