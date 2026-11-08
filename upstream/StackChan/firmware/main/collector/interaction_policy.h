/* SPDX-License-Identifier: MIT */
#pragma once
#include "m12_protocol.h"
#include <cmath>
#include <cstring>
namespace collector::interaction {
enum class AsrModeAction { None, OpenTracking, CloseTracking, ReleaseModes };
inline AsrModeAction asr_mode_action(uint8_t id,bool has_value) {
    if(has_value)return AsrModeAction::None;
    return id==0x10?AsrModeAction::OpenTracking:id==0x11?AsrModeAction::CloseTracking:
        id==0x14||id==0x15?AsrModeAction::ReleaseModes:AsrModeAction::None;
}
inline M12Function camera_mode(bool tracking,bool recognition) {
    return recognition?M12Function::recognize:tracking?M12Function::detect:M12Function::standby;
}
inline bool toggle_due(uint32_t previous,uint32_t current) {return ((current-previous)&1u)!=0;}
struct OwnerFrame {
    uint32_t seq;int64_t at_us,requested_us;bool recognition;M12Function mode;
    size_t count;const M12Face* faces;
};
class OwnerGate {
public:
    void reset(int64_t enabled_at) { *this={};enabled_at_=enabled_at; }
    bool observe(const OwnerFrame& f,int64_t now) {
        if(!f.recognition||f.mode!=M12Function::recognize||f.at_us<=enabled_at_||
           f.at_us<f.requested_us||f.at_us>now||now-f.at_us>700000) {
            seen_=false;hits_=0;candidate_[0]=0;return false;
        }
        if(f.seq==last_seq_||f.at_us<=last_at_)return false;
        last_seq_=f.seq;last_at_=f.at_us;
        const M12Face* best=nullptr;
        for(size_t i=0;i<std::min(f.count,kM12MaxFaces);++i) {
            const auto& face=f.faces[i];
            if(!std::memchr(face.name,0,sizeof(face.name))||!m12_known_name(face.name,face.match_prob)||
               !std::isfinite(face.prob)||face.prob<.5f||face.prob>1||face.x<0||face.y<0||face.w<=0||face.h<=0||
               int(face.x)+face.w>kM12FrameWidth||int(face.y)+face.h>kM12FrameHeight)continue;
            if(!std::strcmp(candidate_,face.name)) {best=&face;break;}
            if(!best||face.match_prob>best->match_prob)best=&face;
        }
        seen_=best!=nullptr;
        if(!best){hits_=0;candidate_[0]=0;return false;}
        if(std::strcmp(candidate_,best->name)) {std::memcpy(candidate_,best->name,sizeof(candidate_));hits_=0;}
        if(hits_<2)++hits_;
        if(!confirmed_&&hits_>=2) {confirmed_=true;std::memcpy(owner_,candidate_,sizeof(owner_));return true;}
        return false;
    }
    bool seen()const{return seen_;}
    bool confirmed()const{return confirmed_;}
    const char* name()const{return confirmed_?owner_:candidate_;}
private:
    int64_t enabled_at_=0,last_at_=0;uint32_t last_seq_=0;unsigned hits_=0;
    bool seen_=false,confirmed_=false;char candidate_[16]{},owner_[16]{};
};
}
