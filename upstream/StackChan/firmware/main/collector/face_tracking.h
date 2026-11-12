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
    int mode=0,frame_mode=0,requested_mode=1; // M12 (m12d) standby=0, detect=1
    unsigned face_count=0;
    int x=0,y=0,w=0,h=0; // unscaled 640x480 M12 coordinates: the tracked face, or the top of a person box
};

// Pure policy. Protected by remote's mutex; only motor_task consumes its actions.
// Angles are integer tenths. A session's measured anchor never changes on loss,
// recovery or M12 mode changes; only an explicit off/on can create a new anchor.
//
// One look, one turn (user decision 2026-11-09, "like a human turning the head"):
// a fresh face further off centre than the deadband sets the whole correction
// at once; the head turns there in a single trajectory; vision is ignored while
// the camera moves; the turn counts as finished once the measured angle has been
// stable (or a distance-scaled deadline passes); only a frame received after
// that starts the next look. Each look uses two consecutive frames of the still
// camera (user decision 2026-11-09, "hybrid"): a face that barely moved is
// turned to directly; a moving face gets a bounded lead toward where it will be
// when the turn ends; a face heading into the centre band is left alone. A face missing for 1.5 s turns the head back to
// the anchor once.
class FaceTracking {
public:
    enum class Action { None, Move, Cancel };
    static constexpr int kRange=600; // anchor ±60°
    // The spring model finishes any distance in ~0.6 s at speed 1000, so its peak
    // command rate grows with distance (43° → ~390°/s). Keep every turn at or
    // below the peak of the validated 20° web slot (~4° per 20 ms tick): full
    // speed up to 20°, proportionally slower beyond, never below 300.
    static constexpr int kServoSpeed=kWebYawDefaultSpeed,kRateRefTenths=200,kMinTurnSpeed=300;
    static int turn_speed(int distance) {
        const int d=std::abs(distance);
        return d<=kRateRefTenths?kServoSpeed:std::max(kMinTurnSpeed,kServoSpeed*kRateRefTenths/d);
    }
    static constexpr int kFrameWidth=640,kFrameCentre=320;
    // Horizontal field of view used for pixel→angle. 0.4.47 assumed 50° and its
    // turn-by-turn calibration measured 10.3 px/° (20 turns, 2026-11-09), i.e.
    // about 62° for this M12 lens (screwed outward to focus). Telemetry keeps
    // reporting the measured px/deg after turns.
    static constexpr int kFovTenths=620;
    // Start a turn only when the face is more than 8° off centre (82 px). With
    // the 3° precompensation turns stop within ~1° (0.4.47 field data), so this
    // band does not hunt; 0.4.47 used 10° (in practice ~12° with the narrow FOV).
    static constexpr int kDeadbandTenths=80;
    static constexpr int kDeadbandPx=kDeadbandTenths*kFrameWidth/kFovTenths;
    // Aim this far beyond the face: the servo typically stops 3-4° short.
    static constexpr int kPrecompTenths=30;
    // A turn may cross the whole anchor range in one trajectory.
    static constexpr int kMaxCommandTenths=2*kRange;
    // Consecutive 100 ms samples may differ by many degrees while a turn runs.
    static constexpr int kFeedbackJumpTenths=250;
    // A real face cannot cross more than ~40% of the image between consecutive
    // frames of a still camera; larger centre jumps are detector glitches.
    static constexpr int kFrameJumpPx=260;
    static constexpr int64_t kFrameMaxAgeUs=700000,kFeedbackMaxAgeUs=300000;
    // Arrival: measured angle within 1° for 250 ms after the trajectory ended,
    // or 1 s + 30 ms per degree of the turn after it ended, whichever is first.
    static constexpr int kStableTenths=10;
    static constexpr int64_t kStableUs=250000,kSettleBaseUs=1000000,kSettlePerDegUs=30000;
    // Frames received sooner than this after arrival may show the moving view.
    static constexpr int64_t kLookDelayUs=300000;
    // Face missing this long: turn back to the anchor, once, unless already near it.
    static constexpr int64_t kReturnDelayUs=1500000;
    static constexpr int kReturnToleranceTenths=50;
    // Stall guard: a turn must show min(5°, distance) of measured motion within
    // 2 s + 20 ms per degree of the turn, counted only while a command is active.
    static constexpr int kProgressQuantum=50;
    static constexpr int64_t kProgressBaseUs=2000000,kProgressPerDegUs=20000;
    // Two-frame look: up to 2° between frames counts as still; otherwise lead by
    // the face's angular speed × 600 ms (≈ one turn), at most 10°. Frames more
    // than 600 ms apart are not used for speed (the second becomes a new first).
    static constexpr int kStillTenths=20,kMaxLeadTenths=100;
    static constexpr int64_t kLeadUs=600000,kVelocityMaxGapUs=600000,kMinVelocityDtUs=50000;
    static int64_t progress_budget_us(int distance) { return kProgressBaseUs+kProgressPerDegUs*std::abs(distance)/10; }
    static int64_t settle_deadline_us(int distance) { return kSettleBaseUs+kSettlePerDegUs*std::abs(distance)/10; }
    // Signed tenths for a face centre: positive error (face right) → positive turn.
    static int offset_tenths(int centre) { return (centre-kFrameCentre)*kFovTenths/kFrameWidth; }

    struct Turn { bool valid=false,returning=false,settled=false,by_deadline=false; int start=0,aim=0,command=0,arrived=0,lead=0,motion=0; int64_t motion_dt_us=0; };
    struct Calibration { int samples=0,last_px_per_deg_x10=0; int64_t sum_px_per_deg_x10=0; };

    void request(bool enabled,int64_t now,const char* reason="disabled") {
        if(enabled==requested_) return;
        if(!enabled) {
            requested_=false;pending_cancel_=pending_cancel_||active_;active_=false;moving_=false;
            state_=reason;return;
        }
        requested_=true;enabled_us_=now;minimum_frame_us_=now;look_from_us_=now;
        anchor_valid_=false;fault_=false;active_=false;moving_=false;feedback_good_=0;progress_pending_=false;
        last_feedback_us_=0;last_frame_seq_=0;last_frame_us_=0;lost_since_us_=0;returned_=false;cal_pending_=false;
        mode_=0;mode_revision_=0;mode_requested_us_=0;have_face_=false;state_="waiting_frame";
        turn_=Turn{}; // calibration is a lens property and is kept
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
        // Account for time under an active yaw command up to this observation.
        // Pauses retain consumed time and the original measurement baseline.
        if(progress_pending_) {
            if(active_&&in.now_us>=progress_clock_us_&&progress_active_us_<progress_budget_) {
                const int64_t elapsed=in.now_us-progress_clock_us_;
                const int64_t remaining=progress_budget_-progress_active_us_;
                if(elapsed>=remaining) {
                    progress_expired_us_=progress_clock_us_+remaining;
                    progress_active_us_=progress_budget_;
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
        // measured-progress budget before every other gate.
        if(progress_pending_) {
            const int direction=progress_target_>progress_start_?1:-1;
            const int progress=(in.measured-progress_start_)*direction;
            const int threshold=std::min(kProgressQuantum,std::abs(progress_target_-progress_start_));
            if(in.feedback_us>progress_started_us_&&(!progress_expired_us_||in.feedback_us<=progress_expired_us_)&&progress>=threshold)
                progress_pending_=false;
            else if(progress_active_us_>=progress_budget_)
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
        // While turning the camera view is meaningless: only wait for arrival.
        if(moving_) return settle(in);
        if(in.frame_mode!=in.mode||in.frame_us<in.mode_requested_us||in.frame_us<=0||in.now_us<in.frame_us)
            return interrupt("waiting_frame",in);
        if(in.frame_us<=minimum_frame_us_||in.frame_us<=enabled_us_) {
            // Nothing new yet (motor ticks run faster than M12 frames). Only when
            // no frame has arrived for a frame's lifetime since the later of the
            // look point and the last frame is the face gone.
            if(anchor_valid_&&in.now_us-std::max(look_from_us_,in.frame_us)>=kFrameMaxAgeUs) return lost("frame_stale",in);
            return Action::None;
        }
        if(in.now_us-in.frame_us>=kFrameMaxAgeUs) return lost("frame_stale",in);
        if(!in.face_count||in.w<=0||in.h<=0||in.x<0||in.y<0||in.x+in.w>640||in.y+in.h>480)
            return lost("face_lost",in);
        if(in.frame_seq==last_frame_seq_||in.frame_us<=last_frame_us_) return Action::None;
        const int64_t previous_us=last_frame_us_;
        last_frame_seq_=in.frame_seq;last_frame_us_=in.frame_us;++frames_;
        const int centre=in.x+in.w/2;
        if(have_face_ && std::abs(centre-last_centre_)>kFrameJumpPx) {
            last_centre_=centre;return interrupt("frame_jump",in);
        }
        const bool had_face=have_face_;const int previous_centre=last_centre_;
        have_face_=true;last_centre_=centre;lost_since_us_=0;returned_=false;
        if(cal_pending_) {
            cal_pending_=false;
            const int turned=in.measured-cal_start_;
            if(std::abs(turned)>=kProgressQuantum) {
                calibration_.last_px_per_deg_x10=-(centre-cal_centre_)*100/turned;
                calibration_.sum_px_per_deg_x10+=calibration_.last_px_per_deg_x10;
                ++calibration_.samples;
            }
        }
        if(!anchor_valid_) {anchor_=in.measured;target_=in.measured;anchor_valid_=true;}
        if(std::abs(centre-kFrameCentre)<=kDeadbandPx) return interrupt("deadband",in);
        if(!in.settled) return Action::None;
        // The first frame of a look (or one too long after the previous) is only a reference.
        const int64_t dt=in.frame_us-previous_us;
        if(!had_face||dt>kVelocityMaxGapUs) {state_="observing";return Action::None;}
        const int offset=offset_tenths(centre);
        const int motion=offset-offset_tenths(previous_centre);
        const int lead=std::abs(motion)<=kStillTenths?0:static_cast<int>(std::clamp<int64_t>(
            static_cast<int64_t>(motion)*kLeadUs/std::max(dt,kMinVelocityDtUs),-kMaxLeadTenths,kMaxLeadTenths));
        // A face that will be back inside the centre band by itself needs no turn.
        // (Without a lead the pixel band above already decided.)
        if(lead&&std::abs(offset+lead)<=kDeadbandTenths) return interrupt("deadband",in);
        const int aim=in.measured+offset+lead;
        const int direction=aim>in.measured?1:-1;
        cal_centre_=centre;cal_start_=in.measured;
        const auto action=begin_turn(in,aim,aim+direction*kPrecompTenths,false);
        if(action==Action::Move) {turn_.lead=lead;turn_.motion=motion;turn_.motion_dt_us=dt;}
        return action;
    }
    void command_rejected(const TrackingInput& in) { fault("command_rejected",in);pending_cancel_=true; }
    // A manual request can arrive between policy evaluation and manual intake.
    // The UART owner must consume its stop before testing whether yaw is idle.
    bool take_cancel() {
        if(!pending_cancel_) return false;
        pending_cancel_=false;active_=false;moving_=false;++cancels_;return true;
    }
    bool requested()const{return requested_;} bool active()const{return active_;}
    bool moving()const{return moving_;}
    bool has_anchor()const{return anchor_valid_;} bool faulted()const{return fault_;}
    int anchor()const{return anchor_;} int target()const{return target_;}
    const char* state()const{return state_;}
    uint32_t commands()const{return commands_;}uint32_t cancels()const{return cancels_;}
    uint32_t frames()const{return frames_;}
    const Turn& last_turn()const{return turn_;}
    const Calibration& calibration()const{return calibration_;}
private:
    Action begin_turn(const TrackingInput& in,int aim,int command,bool returning) {
        const int lower=std::max(in.lower,anchor_-kRange),upper=std::min(in.upper,anchor_+kRange);
        const int direction=command>in.measured?1:-1;
        const int target=std::clamp(command,lower,upper);
        // A turn clamped below 5° at the range edge is not worth commanding: the
        // loaded SCS0009 stops 3.5-5° short, so it would only trip the stall guard.
        if((target-in.measured)*direction<kProgressQuantum) return interrupt("at_limit",in);
        // Each trajectory starts from valid measured feedback.
        if(std::abs(target-in.measured)>kMaxCommandTenths) return fault("fault_feedback_jump",in);
        if(!progress_pending_) {progress_active_us_=0;progress_expired_us_=0;}
        // A turn issued while an earlier one never showed progress keeps the
        // consumed active time; a fresh look cannot reset it to zero.
        progress_pending_=true;progress_start_=in.measured;progress_target_=target;progress_started_us_=in.now_us;
        progress_budget_=progress_budget_us(target-in.measured);
        progress_clock_us_=in.now_us;
        turn_=Turn{};turn_.valid=true;turn_.returning=returning;turn_.start=in.measured;
        turn_.aim=std::clamp(aim,lower,upper);turn_.command=target;
        target_=target;move_started_us_=in.now_us;trajectory_done_us_=0;stable_since_us_=0;
        active_=true;moving_=true;have_face_=false;cal_pending_=false;
        state_=returning?"returning":"turning";++commands_;
        return Action::Move;
    }
    Action settle(const TrackingInput& in) {
        // The settled flag sampled before this turn's first update still
        // describes the previous trajectory.
        if(!in.settled||in.now_us<=move_started_us_) {trajectory_done_us_=0;stable_since_us_=0;return Action::None;}
        if(!trajectory_done_us_) trajectory_done_us_=in.now_us;
        if(!stable_since_us_||std::abs(in.measured-stable_angle_)>kStableTenths) {
            stable_since_us_=in.now_us;stable_angle_=in.measured;
        }
        const bool stable=!progress_pending_&&in.now_us-stable_since_us_>=kStableUs;
        const bool deadline=in.now_us-trajectory_done_us_>=settle_deadline_us(turn_.command-turn_.start);
        if(!stable&&!deadline) {state_="settling";return Action::None;}
        moving_=false;turn_.settled=true;turn_.by_deadline=!stable;turn_.arrived=in.measured;
        look_from_us_=in.now_us+kLookDelayUs;minimum_frame_us_=std::max(minimum_frame_us_,look_from_us_);
        // Calibrate only on turns to a still face: a lead mixes in the person's motion.
        cal_pending_=!turn_.returning&&turn_.lead==0;
        state_=turn_.returning?"returned":"looking";
        // active_ stays true: motor_task keeps updating so the arrival hold and
        // factory torque release still run.
        return Action::None;
    }
    Action lost(const char* reason,const TrackingInput& in) {
        if(!lost_since_us_) lost_since_us_=in.now_us;
        have_face_=false;cal_pending_=false;
        if(anchor_valid_&&!returned_&&in.now_us-lost_since_us_>=kReturnDelayUs) {
            returned_=true;
            if(in.settled&&std::abs(in.measured-anchor_)>kReturnToleranceTenths) {
                const int direction=anchor_>in.measured?1:-1;
                return begin_turn(in,anchor_,anchor_+direction*kPrecompTenths,true);
            }
        }
        if(returned_&&!active_) {state_="returned";return Action::None;}
        return interrupt(reason,in);
    }
    Action interrupt(const char* reason,const TrackingInput& in,bool feedback=false) {
        const bool cancel=active_;
        if(moving_) {
            // The camera may still be swinging after a torque release.
            look_from_us_=std::max(look_from_us_,in.now_us+kLookDelayUs);
            minimum_frame_us_=std::max(minimum_frame_us_,look_from_us_);
        }
        active_=false;moving_=false;state_=reason;
        minimum_frame_us_=std::max(minimum_frame_us_,in.frame_us);
        // Preserve the last valid angle across a missing sample. Recovery must
        // not silently bless an implausible jump that happened during the gap.
        if(feedback) {feedback_good_=0;have_face_=false;}
        if(cancel){++cancels_;return Action::Cancel;}return Action::None;
    }
    Action fault(const char* reason,const TrackingInput& in) {fault_=true;return interrupt(reason,in,true);}
    bool requested_=false,active_=false,moving_=false,pending_cancel_=false,anchor_valid_=false,fault_=false,have_face_=false,progress_pending_=false;
    bool returned_=false,cal_pending_=false;
    unsigned feedback_good_=0;
    int anchor_=0,target_=0,last_measured_=0,last_centre_=0,mode_=0,stable_angle_=0,cal_centre_=0,cal_start_=0;
    int progress_start_=0,progress_target_=0;
    int64_t progress_started_us_=0,progress_clock_us_=0,progress_active_us_=0,progress_expired_us_=0,progress_budget_=kProgressBaseUs;
    int64_t enabled_us_=0,minimum_frame_us_=0,look_from_us_=0,last_feedback_us_=0,mode_requested_us_=0,last_frame_us_=0;
    int64_t move_started_us_=0,trajectory_done_us_=0,stable_since_us_=0,lost_since_us_=0;
    uint32_t last_frame_seq_=0,mode_revision_=0,commands_=0,cancels_=0,frames_=0;
    const char* state_="disabled";
    Turn turn_;
    Calibration calibration_;
};

// Speed follows FaceTracking::turn_speed (1000, the fastest factory spring
// mapping, up to 20°). No setAutoAngleSync or fallback getCurrentAngle read occurs.
template<class Servo,class Feedback>
bool apply_tracking_move(Servo& yaw,const Feedback& feedback,int target) {
    if(!feedback.valid||std::abs(target-feedback.angle)>FaceTracking::kMaxCommandTenths) return false;
    const auto limits=yaw.getAngleLimit();
    if(target<limits.x||target>limits.y) return false;
    return yaw.moveFromFeedbackWithSpeed(feedback,target,FaceTracking::turn_speed(target-feedback.angle));
}
}
