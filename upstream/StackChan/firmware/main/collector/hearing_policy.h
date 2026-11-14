/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>

namespace collector::hearing {
// start/stop no longer toggle hearing (owner 2026-11-14): they are mode inputs (event-driven,
// asr_mode_words.h) and hearing has no voice control in hello modes step 1. +1 start, -1 stop, 0 none.
inline int asr_action(uint8_t, bool) { return 0; }
inline bool valid_url(const std::string& url) {
    size_t start=url.rfind("ws://",0)==0 ? 5 : url.rfind("wss://",0)==0 ? 6 : 0;
    if(!start || url.size()>256)return false;
    for(unsigned char c:url)if(c<=32 || c>=127 || c=='@' || c=='?' || c=='#' || c=='\\')return false;
    const auto end=url.find('/',start);
    const auto authority=url.substr(start,end==std::string::npos?end:end-start);
    const auto colon=authority.find(':');
    const auto host=authority.substr(0,colon);
    if(host.empty())return false;
    unsigned octets=0,value=0,digits=0;
    for(char c:host+"."){
        if(c=='.'){if(!digits||digits>3||value>255)return false;++octets;value=digits=0;}
        else if(c>='0'&&c<='9'){if(digits==1&&value==0)return false;value=value*10+c-'0';++digits;}
        else return false;
    }
    if(octets!=4)return false;
    if(colon!=std::string::npos) {
        const auto port=authority.substr(colon+1);if(port.empty()||port.size()>5)return false;
        unsigned n=0;for(char c:port){if(c<'0'||c>'9')return false;n=n*10+c-'0';}if(!n||n>65535)return false;
    }
    return true;
}
inline bool valid_token(const std::string& token) {
    if(token.empty()||token.size()>192)return false;
    for(unsigned char c:token)if(c<=32||c>=127)return false;
    return true;
}
// Response clips come from the voice catalog: tag(id) is the emotion label of clip id (1..count),
// generated from config/voice-emotions.json. Every clip carries one of the backend's labels, so
// neutral and unknown speech can answer too (user, 2026-11-09). Uniform among that label's clips,
// avoiding `last` whenever another clip has the label.
// 嘴 mode (2026-11-11): the ear sets respond=false in TTS or off mode; an older ear without the
// field keeps answering as before.
inline bool response_allowed(bool present,bool value){return !present||value;}
template<class Tag> int emotion_clip(const char* emotion,uint32_t random,int count,Tag tag,int last=0) {
    if(!emotion||count<=0)return 0;
    int n=0;bool last_matches=false;
    for(int id=1;id<=count;++id) if(!std::strcmp(tag(id),emotion)) {if(id==last)last_matches=true;else ++n;}
    if(!n)return last_matches?last:0;
    int k=static_cast<int>(random%static_cast<uint32_t>(n));
    for(int id=1;id<=count;++id) if(id!=last&&!std::strcmp(tag(id),emotion)&&k--==0)return id;
    return 0;
}
// Happy is the most common label; it accumulates (user, 2026-11-09): within a 5-minute window the
// first valid happy may answer, after that every second one. The window starts at its first happy.
constexpr int64_t kHappyWindowUs=5*60*1000000LL;
constexpr int kHappyEvery=2;
struct ResponsePolicy {
    uint32_t seen=0; int64_t last_response=0; int last_clip=0;
    int64_t happy_window=0; int happy_count=0,happy_needed=1;
    template<class Tag>
    int consider(uint32_t id,const char* emotion,bool speech,bool self,int age_ms,int64_t now,uint32_t random,int count,Tag tag) {
        if(!id||id<=seen)return 0;
        seen=id;
        if(!speech||self||age_ms<0||age_ms>5000)return 0;
        const bool happy=emotion&&!std::strcmp(emotion,"happy");
        if(happy) {
            if(!happy_window||now-happy_window>=kHappyWindowUs){happy_window=now;happy_count=0;happy_needed=1;}
            if(++happy_count<happy_needed)return 0;
        }
        if(last_response&&now-last_response<10000000)return 0; // an owed happy stays owed
        const int clip=emotion_clip(emotion,random,count,tag,last_clip);
        if(clip){last_clip=clip;if(happy){happy_count=0;happy_needed=kHappyEvery;}}
        return clip;
    }
    void accepted(int64_t now){last_response=now;}
};
// A chosen response is held until the room is quiet: no speech for 1.5 s (the backend's VAD
// flag in every tick), never sooner, and dropped if that does not happen within 10 s.
struct QuietWait {
    enum Result {Wait,Play,Expired};
    static constexpr int64_t kQuietUs=1500000,kMaxWaitUs=10000000;
    int clip=0;int64_t chosen=0;std::string heard,felt;
    bool pending()const{return clip!=0;}
    void hold(int id,int64_t now,std::string text,std::string emotion){clip=id;chosen=now;heard=std::move(text);felt=std::move(emotion);}
    void clear(){clip=0;}
    int waited_ms(int64_t now)const{return static_cast<int>((now-chosen)/1000);}
    // last_speech: when the backend last reported speaking (0 = not since the choice).
    Result poll(int64_t now,bool speaking,int64_t last_speech) {
        if(!clip)return Wait;
        if(now-chosen>=kMaxWaitUs){clip=0;return Expired;}
        const int64_t quiet_since=std::max(chosen,last_speech);
        return !speaking&&now-quiet_since>=kQuietUs?Play:Wait;
    }
};
class PlaybackOwner {
    std::atomic<uint32_t> active_{0};
public:
    void activate(uint32_t session){active_=session;}
    bool allowed(uint32_t session)const{return !session||session==active_.load();}
};
}
