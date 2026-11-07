/* SPDX-License-Identifier: MIT */
#include "player.h"
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
void Player::finish(RequestStatus status,Error error,int64_t) {
    if (current_>=0) update_result(history_[current_],status,error);
    current_=-1;loaded_=false;began_=idle_began_=0;
    value_.state=State::Idle;value_.mode=Mode::Once;value_.source=Source::None;
    value_.phase=!value_.enabled?Phase::Legacy:gate_?Phase::Idle:Phase::Suspended;
    ++value_.generation;
}
void Player::set_gate(bool allowed,Error reason,int64_t now) {
    if (gate_==allowed && value_.suspended_reason==reason) return;
    gate_=allowed;value_.suspended_reason=allowed?Error::None:reason;
    value_.visible=value_.enabled && gate_;
    if (!allowed && current_>=0 && !configuring()) finish(RequestStatus::Cancelled,reason,now);
    else { ++value_.generation;idle_began_=0; }
    if (current_<0) value_.phase=!value_.enabled?Phase::Legacy:allowed?Phase::Idle:Phase::Suspended;
}
SubmitResult Player::submit(const Command& c,Source source,int64_t now,uint32_t job) {
    tick(now);
    if (!valid_command(c) || source==Source::None) return {false,0,Error::InvalidArgument};
    for (auto& r:history_) if (r.result.request_seq && now-r.at<60000000 && !std::strcmp(c.request_id,r.command.request_id)) {
        if (!same(c,r.command)) return {false,0,Error::IdConflict};
        return {true,r.result.request_seq,Error::None,true};
    }
    if (current_>=0 && history_[current_].source==Source::Manual && source==Source::Voice) return {false,0,Error::Busy};
    if (c.op==Operation::Play) {
        if (configuring()) return {false,0,Error::Busy};
        if (!gate_) return {false,0,Error::NotVisible};
        if (!value_.enabled && !(source==Source::Voice && auto_enable_)) return {false,0,Error::Disabled};
    }
    if (c.op==Operation::Play || (c.op==Operation::Configure && c.enabled)) {
        if (!value_.storage_ready) return {false,0,Error::StorageUnavailable};
        if (!value_.memory_ready) return {false,0,Error::NoMemory};
    }
    if (current_>=0) finish(RequestStatus::Cancelled,Error::Superseded,now);
    else { ++value_.generation;idle_began_=0; }
    auto& r=history_[next_];r={};r.command=c;r.at=now;r.source=source;r.voice_job=job;
    std::memcpy(r.result.request_id,c.request_id,37);r.result.request_seq=++sequence_;
    r.result.operation=c.op;r.result.status=RequestStatus::Accepted;
    current_=int(next_);next_=(next_+1)%history_.size();value_.last_request=r.result;
    value_.last_error=Error::None;loaded_=false;began_=0;
    if (c.op==Operation::Configure) {
        auto_enable_=false;value_.requested_style=c.style;
        if (!c.enabled) { value_.enabled=false;value_.visible=false;finish(RequestStatus::Completed,Error::None,now); }
        else { value_.state=State::Idle;value_.source=source;value_.phase=Phase::Loading; }
    } else if (c.op==Operation::Stop) finish(RequestStatus::Completed,Error::None,now);
    else {
        value_.state=c.state;value_.mode=c.mode;value_.source=source;value_.phase=Phase::Loading;
        first_deadline_=now+1500000;
    }
    return {true,r.result.request_seq,Error::None};
}
void Player::tick(int64_t now) {
    if (current_<0 || configuring()) return;
    if (!began_) { if (now>=first_deadline_) finish(RequestStatus::Expired,Error::Expired,now);return; }
    const auto& c=history_[current_].command;
    const uint32_t duration=c.mode==Mode::Loop?c.loop_for_ms:asset(value_.selected_style,c.state)->duration_ms;
    if (now-began_>=int64_t(duration)*1000) finish(RequestStatus::Completed,Error::None,now);
}
void Player::loaded(uint32_t gen,int64_t now) {
    tick(now);if (gen!=value_.generation || current_<0) return;
    loaded_=true;value_.ready=true;
    if (configuring()) {
        value_.selected_style=history_[current_].command.style;value_.enabled=true;value_.visible=gate_;
        update_result(history_[current_],RequestStatus::Completed,Error::None);
        current_=-1;value_.source=Source::None;value_.phase=gate_?Phase::Idle:Phase::Suspended;idle_began_=0;
    } else { value_.enabled=true;value_.visible=gate_; }
}
void Player::failed(uint32_t gen,Error error,int64_t now) {
    tick(now);if (gen!=value_.generation) return;
    value_.last_error=error;value_.last_error_seq=current_>=0?history_[current_].result.request_seq:0;
    finish(RequestStatus::Failed,error,now);value_.phase=Phase::Error;
}
bool Player::presented(uint32_t gen,uint16_t frame,uint32_t sequence,int64_t now) {
    tick(now);
    if (gen!=value_.generation || !gate_ || !value_.enabled || frame>=8 || (current_>=0 && !loaded_)) return false;
    if (current_>=0 && !began_) {
        began_=now?now:1;value_.phase=Phase::Playing;update_result(history_[current_],RequestStatus::Presenting,Error::None);
    } else if (current_<0 && !idle_began_) idle_began_=now?now:1;
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
