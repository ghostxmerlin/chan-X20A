#include "hearing_policy.h"
#include <cassert>
#include <iostream>
using namespace collector::hearing;
int main() {
  assert(asr_action(0x12,false)==1 && asr_action(0x13,false)==-1);
  assert(asr_action(0x12,true)==0 && asr_action(0x10,false)==0);
  assert(valid_url("ws://ear.local:8765/") && valid_url("wss://ear.example/robot"));
  for(auto s:{"http://ear/","ws://u:p@ear/","ws://ear/?token=x","ws://ear:abc/","ws://ear:70000/","ws://ear/\r\nX:a"}) assert(!valid_url(s));
  ResponsePolicy p;
  // Identity deliberately absent: a stranger gets exactly the same behavior.
  assert(p.consider(1,"happy",true,false,100,1000000,1)>0);
  p.accepted(1000000);
  assert(p.consider(1,"angry",true,false,100,12000000,1)==0); // duplicate
  assert(p.consider(2,"sad",true,false,100,2000000,1)==0); // cooldown
  assert(p.consider(3,"neutral",true,false,100,12000000,1)==0);
  assert(p.consider(4,"happy",true,true,100,12000000,1)==0); // self
  assert(p.consider(5,"happy",true,false,5001,12000000,1)==0); // stale
  assert(p.consider(6,"happy",false,false,100,12000000,1)==0); // non speech
  assert(p.consider(7,"sad",true,false,100,12000000,1)==35);
  for(auto s:{"happy","sad","angry","fearful","disgusted","surprised"}) assert(emotion_clip(s,123)>0);
  PlaybackOwner owner;
  owner.activate(7); assert(owner.allowed(7) && owner.allowed(0));
  owner.activate(0); assert(!owner.allowed(7) && owner.allowed(0)); // manual remains
  owner.activate(8); assert(!owner.allowed(7) && owner.allowed(8));
  std::cout<<"PASS hearing: command/url/response/ownership policies\n";
}
