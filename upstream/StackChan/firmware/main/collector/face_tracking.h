/* SPDX-License-Identifier: MIT */
#pragma once
#include "web_control.h"
#include <algorithm>
#include <cstdint>
#include <cstdlib>

namespace collector {
struct TrackingInput {
    int64_t now_us=0, feedback_us=0, mode_requested_us=0, frame_us=0;
    uint32_t frame_seq=0,mode_revision=0,applied_mode_revision=0;
    bool control_enabled=false, feedback_valid=false, settled=false;
    int measured=0,lower=-1280,upper=1280;
    int mode=0,frame_mode=0,requested_mode=1; // M12 standby=0, detect=1, recognize=2
    unsigned face_count=0;
    int x=0,y=0,w=0,h=0; // unscaled 640x480 M12 coordinates
};

// Pure policy. Protected by remote's mutex; only motor_task consumes its actions.
// Angles are integer tenths. A session's measured anchor never changes on loss,
// recovery or M12 mode changes; only an explicit off/on can create a new anchor.
class FaceTracking {
public:
    enum class Action { None, Move, Cancel };
    // Coarse "follow the direction" tracking: one 20-degree web slot per command
    // (the loaded SCS0009 cannot resolve small steps), three slots per side.
    static constexpr int kRange=600,kMaxStep=kWebYawStepTenths,kServoSpeed=kWebYawDefaultSpeed;
    static constexpr int kSlotTenths=kWebYawStepTenths;
    // Largest distance from measured angle to the next slot centre.
    static constexpr int kMaxCommandTenths=kSlotTenths+kSlotTenths/2;
    // Face must be more than 130 px (41% of half width) off centre before a slot step;
    // a 20-degree swing then lands it well inside the band instead of hunting.
    static constexpr int kDeadbandPx=130;
    // Consecutive 100 ms samples may differ by many degrees while a slot move runs.
    static constexpr int kFeedbackJumpTenths=250;
    // A real face cannot cross more than ~40% of the image between consecutive
    // frames; larger centre jumps are detector glitches and wait for a new frame.
    static constexpr int kFrameJumpPx=260;
    static constexpr int64_t kIntervalUs=2500000,kFrameMaxAgeUs=700000,kFeedbackMaxAgeUs=300000;
    // A slot command must show at least 5 degrees of measured motion within the budget.
    static constexpr int kProgressQuantum=50;
    static constexpr int64_t kProgressTimeoutUs=2000000;
    void request(bool enabled,int64_t now,const char* reason="disabled") {
        if(enabled==requested_) return;
        if(!enabled) {
            requested_=false;pending_cancel_=pending_cancel_||active_;active_=false;
            state_=reason;return;
        }
        requested_=true;enabled_us_=now;minimum_frame_us_=now;
        anchor_valid_=false;fault_=false;active_=false;feedback_good_=0;progress_pending_=false;
        last_feedback_us_=0;last_command_us_=0;last_frame_seq_=0;last_frame_us_=0;
        mode_=0;mode_revision_=0;mode_requested_us_=0;have_face_=false;state_="waiting_frame";
        // Preserve pending_cancel_: off/on between motor ticks still stops.
    }
    Action step(const TrackingInput& in) {
        if(pending_cancel_) {
            take_cancel();minimum_frame_us_=std::max(minimum_frame_us_,in.frame_us);
            return Action::Cancel;
        }
        if(!requested_) return Action::None;
        if(!in.control_enabled) { request(false,in.now_us);return step(in); }
        if(fault_) return Action::None;
        // Account for time under an active yaw command up to this observation,
        // including the interval ending in a feedback/face/mode cancellation.
        // Pauses retain consumed time and the original measurement baseline.
        if(progress_pending_) {
            if(active_&&in.now_us>=progress_clock_us_&&progress_active_us_<kProgressTimeoutUs) {
                const int64_t elapsed=in.now_us-progress_clock_us_;
                const int64_t remaining=kProgressTimeoutUs-progress_active_us_;
                if(elapsed>=remaining) {
                    progress_expired_us_=progress_clock_us_+remaining;
                    progress_active_us_=kProgressTimeoutUs;
                } else progress_active_us_+=elapsed;
            }
            progress_clock_us_=std::max(progress_clock_us_,in.now_us);
        }
        if(in.feedback_us<=0||in.now_us<in.feedback_us||in.now_us-in.feedback_us>=kFeedbackMaxAgeUs)
            return interrupt("feedback_stale",in,true);
        if(!in.feedback_valid) return interrupt("feedback_invalid",in,true);
        if(anchor_valid_&&(in.measured<anchor_-kRange||in.measured>anchor_+kRange))
            return fault("fault_anchor_range",in);
        if(in.measured<in.lower||in.measured>in.upper)
            return anchor_valid_?fault("fault_anchor_range",in):interrupt("feedback_invalid",in,true);
        if(active_&&(target_<in.lower||target_>in.upper)) return fault("fault_anchor_range",in);
        if(in.feedback_us!=last_feedback_us_) {
            if(last_feedback_us_ && in.feedback_us<last_feedback_us_) return interrupt("feedback_stale",in,true);
            if(last_feedback_us_&&std::abs(in.measured-last_measured_)>kFeedbackJumpTenths)
                return fault("fault_feedback_jump",in);
            last_feedback_us_=in.feedback_us;last_measured_=in.measured;
            if(in.feedback_us>=enabled_us_&&feedback_good_<2) ++feedback_good_;
        }
        // Software trajectory completion never proves motor movement. Check the
        // measured-progress budget before frame/mode/settled gates. Intentional
        // cancellations pause this budget; resuming cannot reset or replenish it.
        if(progress_pending_) {
            const int direction=progress_target_>progress_start_?1:-1;
            const int progress=(in.measured-progress_start_)*direction;
            const int threshold=std::min(kProgressQuantum,std::abs(progress_target_-progress_start_));
            if(in.feedback_us>progress_started_us_&&(!progress_expired_us_||in.feedback_us<=progress_expired_us_)&&progress>=threshold)
                progress_pending_=false;
            else if(progress_active_us_>=kProgressTimeoutUs)
                return fault("fault_no_progress",in);
        }
        if(feedback_good_<2) {state_="recovering_feedback";return Action::None;}
        if((in.mode!=1&&in.mode!=2)||in.mode!=in.requested_mode||in.applied_mode_revision!=in.mode_revision)
            return interrupt("mode_changed",in);
        if(mode_ && (mode_!=in.mode||mode_revision_!=in.mode_revision||mode_requested_us_!=in.mode_requested_us)) {
            mode_=in.mode;mode_revision_=in.mode_revision;mode_requested_us_=in.mode_requested_us;
            return interrupt("mode_changed",in);
        }
        mode_=in.mode;mode_revision_=in.mode_revision;mode_requested_us_=in.mode_requested_us;
        if(in.frame_mode!=in.mode||in.frame_us<in.mode_requested_us||in.frame_us<=enabled_us_||
            in.frame_us<=0||in.now_us<in.frame_us) {
            // Retire an in-flight command immediately when its source is no
            // longer eligible, without accepting a delayed old-mode frame.
            return interrupt("waiting_frame",in);
        }
        // Keep the interruption reason visible while waiting for a newer frame.
        if(in.frame_us<=minimum_frame_us_) return Action::None;
        if(in.now_us-in.frame_us>=kFrameMaxAgeUs) return interrupt("frame_stale",in);
        if(!in.face_count||in.w<=0||in.h<=0||in.x<0||in.y<0||in.x+in.w>640||in.y+in.h>480)
            return interrupt("face_lost",in);
        if(in.frame_seq==last_frame_seq_||in.frame_us<=last_frame_us_) return Action::None;
        last_frame_seq_=in.frame_seq;last_frame_us_=in.frame_us;++frames_;
        const int center=in.x+in.w/2;
        if(have_face_ && std::abs(center-last_center_)>kFrameJumpPx) {
            last_center_=center;return interrupt("frame_jump",in);
        }
        have_face_=true;last_center_=center;
        if(!anchor_valid_) {anchor_=in.measured;target_=in.measured;anchor_valid_=true;}
        const int error=center-320;
        if(std::abs(error)<=kDeadbandPx) return interrupt("deadband",in);
        const int direction=error>0?1:-1;
        const bool reverse_pending=progress_pending_&&
            (progress_target_-progress_start_)*direction<0;
        // Cancel before changing the direction of an active trajectory. A new
        // fresh frame may then start from measured feedback in the new direction.
        if(active_&&(progress_target_-progress_start_)*direction<0)
            return interrupt("direction_changed",in);
        if(!in.settled||(last_command_us_&&in.now_us-last_command_us_<kIntervalUs)) return Action::None;
        // Do not repeatedly reissue an unchanged target merely because the
        // interpolation finished. A cancelled command may resume once per new
        // valid frame with its remaining active budget. Reversal changes the
        // measured baseline/direction, never the already consumed active time.
        if(progress_pending_&&active_) return Action::None;
        const int lower=std::max(in.lower,anchor_-kRange),upper=std::min(in.upper,anchor_+kRange);
        // Next 20-degree slot beyond the slot nearest to the measured angle.
        const int target=progress_pending_&&!reverse_pending?progress_target_:
            std::clamp((web_yaw_slot(in.measured)+direction)*kSlotTenths,lower,upper);
        // Each trajectory starts from valid measured feedback and moves at most
        // one and a half slots; there is no old-target-to-new-target move.
        if(std::abs(target-in.measured)>kMaxCommandTenths) return fault("fault_feedback_jump",in);
        if(target<lower||target>upper||(error>0&&target<=in.measured)||(error<0&&target>=in.measured))
            return interrupt("at_limit",in);
        if(!progress_pending_||reverse_pending) {
            if(!progress_pending_) {progress_active_us_=0;progress_expired_us_=0;}
            progress_pending_=true;progress_start_=in.measured;
            progress_target_=target;progress_started_us_=in.now_us;
        }
        progress_clock_us_=in.now_us;
        target_=target;last_command_us_=in.now_us;active_=true;state_="tracking";++commands_;
        return Action::Move;
    }
    void command_rejected(const TrackingInput& in) { fault("command_rejected",in);pending_cancel_=true; }
    // A manual request can arrive between policy evaluation and manual intake.
    // The UART owner must consume its stop before testing whether yaw is idle.
    bool take_cancel() {
        if(!pending_cancel_) return false;
        pending_cancel_=false;active_=false;++cancels_;return true;
    }
    bool requested()const{return requested_;} bool active()const{return active_;}
    bool has_anchor()const{return anchor_valid_;} bool faulted()const{return fault_;}
    int anchor()const{return anchor_;} int target()const{return target_;}
    const char* state()const{return state_;}
    uint32_t commands()const{return commands_;}uint32_t cancels()const{return cancels_;}
    uint32_t frames()const{return frames_;}
private:
    Action interrupt(const char* reason,const TrackingInput& in,bool feedback=false) {
        const bool cancel=active_;active_=false;state_=reason;
        minimum_frame_us_=std::max(minimum_frame_us_,in.frame_us);
        // Preserve the last valid angle across a missing sample. Recovery must
        // not silently bless an implausible jump that happened during the gap.
        if(feedback) {feedback_good_=0;have_face_=false;}
        if(cancel){++cancels_;return Action::Cancel;}return Action::None;
    }
    Action fault(const char* reason,const TrackingInput& in) {fault_=true;return interrupt(reason,in,true);}
    bool requested_=false,active_=false,pending_cancel_=false,anchor_valid_=false,fault_=false,have_face_=false,progress_pending_=false;
    unsigned feedback_good_=0;
    int anchor_=0,target_=0,last_measured_=0,last_center_=0,mode_=0;
    int progress_start_=0,progress_target_=0;
    int64_t progress_started_us_=0,progress_clock_us_=0,progress_active_us_=0,progress_expired_us_=0;
    int64_t enabled_us_=0,minimum_frame_us_=0,last_command_us_=0,last_feedback_us_=0,mode_requested_us_=0,last_frame_us_=0;
    uint32_t last_frame_seq_=0,mode_revision_=0,commands_=0,cancels_=0,frames_=0;
    const char* state_="disabled";
};

// Speed 1000 is the fastest factory spring mapping, the same as the web slot
// buttons. No setAutoAngleSync or fallback getCurrentAngle read occurs.
template<class Servo,class Feedback>
bool apply_tracking_move(Servo& yaw,const Feedback& feedback,int target) {
    if(!feedback.valid||std::abs(target-feedback.angle)>FaceTracking::kMaxCommandTenths) return false;
    const auto limits=yaw.getAngleLimit();
    if(target<limits.x||target>limits.y) return false;
    return yaw.moveFromFeedbackWithSpeed(feedback,target,FaceTracking::kServoSpeed);
}
}
