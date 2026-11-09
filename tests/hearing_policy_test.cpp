#include "hearing_policy.h"
#include <cassert>
#include <cstdint>
#include <iostream>
using namespace collector::hearing;
int main() {
  assert(asr_action(0x12,false)==1 && asr_action(0x13,false)==-1);
  assert(asr_action(0x12,true)==0 && asr_action(0x10,false)==0);
  assert(valid_url("ws://127.0.0.1:8765/") && valid_url("wss://192.168.1.2/robot"));
  for(auto s:{"http://ear/","ws://u:p@ear/","ws://ear/?token=x","ws://ear:abc/","ws://ear:70000/","ws://ear/\r\nX:a"}) assert(!valid_url(s));
  assert(!valid_url("ws://01.2.3.4/")); // no DNS fallback for ambiguous numeric spelling
  // Clip emotions come from the voice catalog (config/voice-emotions.json); this fake catalog
  // has ids 1..6. Every label the backend returns, neutral and unknown included, can map.
  static const char* const tags[]={"surprised","happy","neutral","sad","unknown","happy"};
  const auto tag=[](int id){return tags[id-1];};
  ResponsePolicy p;
  // Identity deliberately absent: a stranger gets exactly the same behavior.
  assert(p.consider(1,"happy",true,false,100,1000000,1,6,tag)>0);
  p.accepted(1000000);
  assert(p.consider(1,"angry",true,false,100,12000000,1,6,tag)==0); // duplicate
  assert(p.consider(2,"sad",true,false,100,2000000,1,6,tag)==0); // cooldown
  assert(p.consider(3,"neutral",true,false,100,12000000,1,6,tag)==3); // neutral now has clips
  p.accepted(12000000);
  assert(p.consider(4,"happy",true,true,100,30000000,1,6,tag)==0); // self
  assert(p.consider(5,"happy",true,false,5001,30000000,1,6,tag)==0); // stale
  assert(p.consider(6,"happy",false,false,100,30000000,1,6,tag)==0); // non speech
  assert(p.consider(7,"sad",true,false,100,30000000,1,6,tag)==4);
  p.accepted(30000000);
  assert(p.consider(8,"unknown",true,false,100,50000000,9,6,tag)==5);
  assert(p.consider(9,"angry",true,false,100,70000000,1,6,tag)==0); // no clip carries this label
  // Picks spread over every clip of a label and avoid repeating the previous one.
  for(uint32_t r=0;r<8;++r){const int a=emotion_clip("happy",r,6,tag,2),b=emotion_clip("happy",r,6,tag,6);assert(a==6&&b==2);}
  bool seen2=false,seen6=false;for(uint32_t r=0;r<8;++r){const int c=emotion_clip("happy",r,6,tag);seen2|=c==2;seen6|=c==6;assert(c==2||c==6);}
  assert(seen2&&seen6);
  assert(emotion_clip("sad",5,6,tag,4)==4); // the only clip may repeat
  assert(emotion_clip("calm",5,6,tag)==0&&emotion_clip("happy",0,0,tag)==0);
  // Happy accumulates (user, 2026-11-09): within a 5-minute window the first happy answers,
  // then only every second valid happy; the window restarts 5 min after its first happy.
  {
    static const char* const t2[]={"happy","happy","neutral"};
    const auto tg=[](int id){return t2[id-1];};
    ResponsePolicy h;int64_t t=1000000;uint32_t n=0;
    auto say=[&](const char* e,bool valid=true){t+=11000000;const uint32_t id=++n;return h.consider(id,e,valid,false,100,t,id,3,tg);};
    assert(say("happy")>0);h.accepted(t);                       // first in window
    assert(say("happy")==0);                                    // 1 of 2
    assert(say("happy")>0);h.accepted(t);                       // 2 of 2
    assert(say("happy",false)==0);assert(say("happy")==0);      // invalid ones do not count
    assert(say("neutral")==3);h.accepted(t);                    // other labels unaffected
    assert(say("happy")>0);h.accepted(t);
    assert(h.happy_count==0&&h.happy_needed==2);
    t+=5*60*1000000LL;                                          // window over
    assert(say("happy")>0);h.accepted(t);                       // first of a new window
    assert(say("happy")==0);
    // A happy that reaches its count during the cooldown keeps it for the next one.
    ResponsePolicy c;c.consider(1,"happy",true,false,100,1000000,1,3,tg);c.accepted(1000000);
    assert(c.consider(2,"happy",true,false,100,3000000,1,3,tg)==0); // 1 of 2
    assert(c.consider(3,"happy",true,false,100,5000000,1,3,tg)==0); // 2 of 2 but cooldown
    assert(c.consider(4,"happy",true,false,100,12000000,1,3,tg)>0);  // still owed: answers now
  }
  // A chosen response waits for quiet: 1.5 s without speech, at most 10 s after it was chosen.
  {
    QuietWait w;
    assert(!w.pending());w.hold(7,1000000,"hi","happy");assert(w.pending());
    assert(w.poll(1000000,false,0)==QuietWait::Wait);            // never immediately
    assert(w.poll(2400000,false,0)==QuietWait::Wait);            // quiet only 1.4 s
    assert(w.poll(2500000,false,0)==QuietWait::Play&&w.clip==7&&w.waited_ms(2500000)==1500);
    w.hold(8,10000000,"x","sad");
    assert(w.poll(11000000,true,11000000)==QuietWait::Wait);     // someone talking
    assert(w.poll(12000000,false,11900000)==QuietWait::Wait);    // quiet since 11.9 s
    assert(w.poll(13400000,false,11900000)==QuietWait::Play);
    w.hold(9,20000000,"y","angry");
    for(int64_t at=20000000;at<30000000;at+=500000) assert(w.poll(at,true,at)==QuietWait::Wait);
    assert(w.poll(30000000,true,30000000)==QuietWait::Expired&&!w.pending());
  }
  PlaybackOwner owner;
  owner.activate(7); assert(owner.allowed(7) && owner.allowed(0));
  owner.activate(0); assert(!owner.allowed(7) && owner.allowed(0)); // manual remains
  owner.activate(8); assert(!owner.allowed(7) && owner.allowed(8));
  std::cout<<"PASS hearing: command/url/response/ownership policies\n";
}
