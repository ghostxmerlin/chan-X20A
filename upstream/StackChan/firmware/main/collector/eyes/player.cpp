/* SPDX-License-Identifier: MIT */
#include "player.h"
#include <algorithm>
#include <cstring>
namespace collector::eyes {
namespace {
bool same(const Command& a,const Command& b) {
    if (a.op!=b.op) return false;
    if (a.op==Operation::Configure) return a.enabled==b.enabled && a.style==b.style;
    if (a.op==Operation::Play) return a.state==b.state && a.mode==b.mode && a.loop_for_ms==b.loop_for_ms;
    return true;
}
}
bool valid_request_id(const char* id) {
    if (!id || std::strlen(id)!=36) return false;
    for (size_t i=0;i<36;++i) {
        const char c=id[i];
        if (i==8 || i==13 || i==18 || i==23) { if (c!='-') return false; }
        else if (!((c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F'))) return false;
    }
    return true;
}
bool valid_command(const Command& c) {
    if (!std::memchr(c.request_id,0,sizeof(c.request_id)) || !valid_request_id(c.request_id)) return false;
    if (c.op==Operation::Configure) return valid(c.style);
    if (c.op==Operation::Stop) return true;
    return c.op==Operation::Play && valid(c.state) &&
        ((c.mode==Mode::Once && c.loop_for_ms==0) || (c.mode==Mode::Loop && c.loop_for_ms>=1000 && c.loop_for_ms<=30000));
}
void Player::set_resources(bool storage,bool memory) {
    value_.storage_ready=storage;value_.memory_ready=memory;
    if (!memory) value_.ready=false;
}
bool Player::configuring() const { return current_>=0 && history_[current_].command.op==Operation::Configure; }
void Player::update_result(Record& r,RequestStatus status,Error error) {
    r.result.status=status;r.result.error=error;
    if (r.result.request_seq==value_.last_request.request_seq) value_.last_request=r.result;
}
uint32_t Player::random_below(uint32_t limit) {
    random_^=random_<<13;random_^=random_>>17;random_^=random_<<5;
    return random_%limit;
}
void Player::update_countdown(int64_t now) {
    value_.next_change_ms=value_.natural_cycle && gate_ && !active() && natural_due_>now
        ?uint32_t((natural_due_-now+999)/1000):0;
}
void Player::begin_natural(int64_t now) {
    // Every non-idle state remains eligible. Friendly/curious/thoughtful beats
    // get more turns; hurt/angry/refusing/scared remain uncommon.
    constexpr uint8_t legacy[kStateCount]={0,6,1,2,1,5,3,3,1,2,4,4,2,5,5,3,1,2,4,2,3,3,6,5,6,4,2,1,4,3};
    // A configured pool (normal mode: sleepy / thinking / curious) replaces the weights with an
    // equal chance for each pooled state; a single-state pool may repeat.
    uint8_t weights[kStateCount];
    size_t pooled=0;
    for(size_t i=0;i<kStateCount;++i){weights[i]=natural_pool_?((natural_pool_>>i)&1u):legacy[i];pooled+=i&&weights[i];}
    weights[0]=0;
    const bool avoid_repeat=pooled>1;
    uint32_t total=0;
    for(size_t i=1;i<kStateCount;++i)if(!avoid_repeat||State(i)!=last_natural_)total+=weights[i];
    if(!total)return;
    auto draw=random_below(total);
    for(size_t i=1;i<kStateCount;++i)if(!avoid_repeat||State(i)!=last_natural_) {
        if(draw<weights[i]){value_.state=State(i);break;}draw-=weights[i];
    }
    last_natural_=value_.state;natural_rounds_=1+random_below(3);natural_frames_=0;
    natural_active_=true;loaded_=false;began_=idle_began_=natural_due_=natural_frame_due_=0;
    first_deadline_=now+int64_t(kManualFirstFrameDeadlineMs)*1000;
    value_.source=Source::Natural;value_.mode=Mode::Loop;value_.phase=Phase::Loading;++value_.generation;
}
void Player::finish(RequestStatus status,Error error,int64_t now) {
    if(natural_active_ && (status==RequestStatus::Failed || status==RequestStatus::Expired))natural_after_=now+5000000;
    if (current_>=0) update_result(history_[current_],status,error);
    current_=-1;natural_active_=false;loaded_=false;began_=idle_began_=natural_due_=natural_frame_due_=0;
    natural_frames_=0;value_.next_change_ms=0;
    value_.state=State::Idle;value_.mode=Mode::Once;value_.source=Source::None;
    value_.phase=!value_.enabled?Phase::Legacy:gate_?Phase::Idle:Phase::Suspended;
    ++value_.generation;
}
void Player::set_gate(bool allowed,Error reason,int64_t now) {
    if (gate_==allowed && value_.suspended_reason==reason) return;
    gate_=allowed;value_.suspended_reason=allowed?Error::None:reason;
    value_.visible=value_.enabled && gate_;
    natural_due_=0;value_.next_change_ms=0;
    if (!allowed && active() && !configuring()) finish(RequestStatus::Cancelled,reason,now);
    else { ++value_.generation;idle_began_=0; }
    if (current_<0) value_.phase=!value_.enabled?Phase::Legacy:allowed?Phase::Idle:Phase::Suspended;
}
SubmitResult Player::submit(const Command& c,Source source,int64_t now,uint32_t job) {
    tick(now);
    if (!valid_command(c) || (source!=Source::Manual && source!=Source::Voice && source!=Source::Hearing)) return {false,0,Error::InvalidArgument};
    for (auto& r:history_) if (r.result.request_seq && now-r.at<60000000 && !std::strcmp(c.request_id,r.command.request_id)) {
        if (!same(c,r.command)) return {false,0,Error::IdConflict};
        return {true,r.result.request_seq,Error::None,true};
    }
    if (current_>=0 && history_[current_].source==Source::Manual && (source==Source::Voice || source==Source::Hearing)) return {false,0,Error::Busy};
    // A voice cue outranks a hearing emotion; a hearing emotion never interrupts a voice cue.
    if (current_>=0 && history_[current_].source==Source::Voice && source==Source::Hearing) return {false,0,Error::Busy};
    if (c.op==Operation::Play) {
        if (configuring()) return {false,0,Error::Busy};
        if (!gate_) return {false,0,Error::NotVisible};
        if (!value_.enabled && !(source==Source::Voice && auto_enable_)) return {false,0,Error::Disabled};
    }
    if (c.op==Operation::Play || (c.op==Operation::Configure && c.enabled)) {
        if (!value_.storage_ready) return {false,0,Error::StorageUnavailable};
        if (!value_.memory_ready) return {false,0,Error::NoMemory};
    }
    if (active()) finish(RequestStatus::Cancelled,Error::Superseded,now);
    else { ++value_.generation;idle_began_=0; }
    auto& r=history_[next_];r={};r.command=c;r.at=now;r.source=source;r.voice_job=job;
    std::memcpy(r.result.request_id,c.request_id,37);r.result.request_seq=++sequence_;
    r.result.operation=c.op;r.result.status=RequestStatus::Accepted;
    current_=int(next_);next_=(next_+1)%history_.size();value_.last_request=r.result;
    value_.last_error=Error::None;loaded_=false;began_=natural_due_=decode_after_=0;value_.next_change_ms=0;
    first_deadline_=now+int64_t(source==Source::Voice?kVoiceFirstFrameDeadlineMs:kManualFirstFrameDeadlineMs)*1000;
    if (c.op==Operation::Configure) {
        auto_enable_=false;value_.requested_style=c.style;
        if (!c.enabled) { value_.natural_cycle=false;value_.enabled=false;value_.visible=false;finish(RequestStatus::Completed,Error::None,now); }
        else { value_.state=State::Idle;value_.source=source;value_.phase=Phase::Loading; }
    } else if (c.op==Operation::Stop) finish(RequestStatus::Completed,Error::None,now);
    else {
        value_.state=c.state;value_.mode=c.mode;value_.source=source;value_.phase=Phase::Loading;
    }
    return {true,r.result.request_seq,Error::None};
}
void Player::tick(int64_t now) {
    update_countdown(now);
    if(!active()) {
        if(value_.natural_cycle && value_.enabled && value_.ready && value_.storage_ready && gate_ &&
           idle_began_ && natural_due_ && now>=natural_due_)begin_natural(now);
        return;
    }
    if (!began_) { if (now>=first_deadline_) finish(RequestStatus::Expired,Error::Expired,now);return; }
    if(natural_active_) {
        if(natural_frames_>=natural_rounds_*8 && now>=natural_frame_due_)finish(RequestStatus::Completed,Error::None,now);
        return;
    }
    const auto& c=history_[current_].command;
    const uint32_t duration=c.mode==Mode::Loop?c.loop_for_ms:asset(value_.selected_style,c.state)->duration_ms;
    if (now-began_>=int64_t(duration)*1000) finish(RequestStatus::Completed,Error::None,now);
}
void Player::loaded(uint32_t gen,int64_t now) {
    tick(now);if (gen!=value_.generation) return;
    value_.ready=true;decode_after_=0;
    if(!active())return;
    loaded_=true;
    if (configuring()) {
        value_.selected_style=history_[current_].command.style;value_.enabled=true;value_.visible=gate_;value_.natural_cycle=true;
        last_natural_=State::Invalid;natural_after_=0;
        update_result(history_[current_],RequestStatus::Completed,Error::None);
        current_=-1;value_.source=Source::None;value_.phase=gate_?Phase::Idle:Phase::Suspended;idle_began_=0;
    } else {
        // A first explicit voice cue may enable the default V05 style. Once
        // that succeeds it has the same autonomous idle behavior as a selection.
        if(!value_.enabled)value_.natural_cycle=true;
        value_.enabled=true;value_.visible=gate_;
    }
}
void Player::failed(uint32_t gen,Error error,int64_t now) {
    tick(now);if (gen!=value_.generation) return;
    value_.last_error=error;value_.last_error_seq=current_>=0?history_[current_].result.request_seq:0;
    finish(RequestStatus::Failed,error,now);natural_after_=now+5000000;value_.phase=Phase::Error;
}
void Player::idle_failed(int64_t now) {
    // Retain an already enabled style/frame. A broken idle asset is retried
    // with bounded backoff, never in a worker busy loop and never via legacy.
    value_.ready=false;value_.phase=Phase::Error;decode_after_=now+5000000;
    natural_due_=0;value_.next_change_ms=0;
}
bool Player::presented(uint32_t gen,uint16_t frame,uint32_t sequence,int64_t now) {
    tick(now);
    if (gen!=value_.generation || !gate_ || !value_.enabled || frame>=8 || (active() && !loaded_)) return false;
    if(natural_active_ && (frame!=natural_frames_%8 || (began_ && now<natural_frame_due_)))return false;
    if (active() && !began_) {
        began_=now?now:1;value_.phase=Phase::Playing;
        if(current_>=0)update_result(history_[current_],RequestStatus::Presenting,Error::None);
    } else if (!active() && !idle_began_) {
        idle_began_=now?now:1;value_.phase=Phase::Idle;
        if(value_.natural_cycle)natural_due_=std::max(now+int64_t(2000+random_below(5001))*1000,natural_after_);
        update_countdown(now);
    }
    if(natural_active_) {
        ++natural_frames_;
        natural_frame_due_=now+int64_t(asset(value_.selected_style,value_.state)->durations[frame])*1000;
    }
    value_.frame_index=frame;value_.submitted_seq=sequence;value_.active_style=value_.selected_style;
    return true;
}
void Player::cancel_voice(uint32_t job,int64_t now) {
    if (current_>=0 && history_[current_].source==Source::Voice && history_[current_].voice_job==job)
        finish(RequestStatus::Cancelled,Error::VoiceEnded,now);
}
RequestResult Player::query(const char* id,int64_t now) const {
    if (id) for (const auto& r:history_) if (r.result.request_seq && now-r.at<60000000 && !std::strcmp(id,r.command.request_id)) return r.result;
    RequestResult result;result.error=Error::RequestUnknown;return result;
}
FramePlan Player::frame_plan(int64_t now,int bound_frame) const {
    const Style style=configuring()?history_[current_].command.style:value_.selected_style;
    const State state=configuring()?State::Idle:value_.state;
    if(natural_active_)return {style,state,value_.generation,uint16_t(natural_frames_%8),began_?natural_frame_due_:0};
    const auto* a=asset(style,state);const int64_t start=current_>=0?began_:idle_began_;
    if (!start) return {style,state,value_.generation,0,0};
    const int64_t elapsed=now>start?now-start:0,total=int64_t(a->duration_ms)*1000;
    const int64_t cycle=elapsed/total,within=elapsed%total;
    uint16_t frame=0;int64_t boundary=0;
    while (frame<7 && within>=boundary+int64_t(a->durations[frame])*1000) boundary+=int64_t(a->durations[frame++])*1000;
    int64_t due=start+cycle*total+boundary;
    if (int(frame)==bound_frame) { due+=int64_t(a->durations[frame])*1000;frame=(frame+1)%8; }
    return {style,state,value_.generation,frame,due};
}
}
