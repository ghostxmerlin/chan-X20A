/* SPDX-License-Identifier: MIT */
#pragma once
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>

namespace collector::hearing {
inline int asr_action(uint8_t id, bool value) { return value ? 0 : id==0x12 ? 1 : id==0x13 ? -1 : 0; }
inline bool valid_url(const std::string& url) {
    size_t start=url.rfind("ws://",0)==0 ? 5 : url.rfind("wss://",0)==0 ? 6 : 0;
    if(!start || url.size()>256)return false;
    for(unsigned char c:url)if(c<=32 || c>=127 || c=='@' || c=='?' || c=='#' || c=='\\')return false;
    const auto end=url.find('/',start);
    const auto authority=url.substr(start,end==std::string::npos?end:end-start);
    const auto colon=authority.find(':');
    const auto host=authority.substr(0,colon);
    if(host.empty())return false;
    for(char c:host)if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='.'||c=='-'))return false;
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
inline int emotion_clip(const char* emotion,uint32_t random) {
    if(!std::strcmp(emotion,"happy")){constexpr int ids[]={6,23,28};return ids[random%3];}
    if(!std::strcmp(emotion,"sad"))return 35;
    if(!std::strcmp(emotion,"angry")){constexpr int ids[]={30,31,32};return ids[random%3];}
    if(!std::strcmp(emotion,"fearful")){constexpr int ids[]={4,26};return ids[random%2];}
    if(!std::strcmp(emotion,"disgusted")){constexpr int ids[]={24,25};return ids[random%2];}
    if(!std::strcmp(emotion,"surprised")){constexpr int ids[]={1,33};return ids[random%2];}
    return 0;
}
struct ResponsePolicy {
    uint32_t seen=0; int64_t last_response=0;
    int consider(uint32_t id,const char* emotion,bool speech,bool self,int age_ms,int64_t now,uint32_t random) {
        if(!id||id<=seen)return 0;
        seen=id;
        if(!speech||self||age_ms<0||age_ms>5000||(last_response&&now-last_response<10000000))return 0;
        return emotion_clip(emotion,random);
    }
    void accepted(int64_t now){last_response=now;}
};
class PlaybackOwner {
    std::atomic<uint32_t> active_{0};
public:
    void activate(uint32_t session){active_=session;}
    bool allowed(uint32_t session)const{return !session||session==active_.load();}
};
}
