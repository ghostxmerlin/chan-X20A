#include "hearing_transport_policy.h"
#include <cassert>
#include <iostream>
using namespace collector::hearing;
int main(){
 MessageBuffer b;
 assert(b.add(1,false,3,"ab",2)==MessageBuffer::More);
 assert(b.add(1,false,3,"c",1)==MessageBuffer::More);
 assert(b.add(0,true,2,"de",2)==MessageBuffer::Complete);
 assert(b.message=="abcde");b.clear();
 assert(b.add(0,true,1,"x",1)==MessageBuffer::Invalid);
 b.clear();assert(b.add(1,true,9000,"x",1)==MessageBuffer::Invalid);
 b.clear();assert(b.add(1,true,3,"abc",3)==MessageBuffer::Complete);
 OperationDeadline d;d.begin(1000,200);
 assert(d.poll_timeout(0,1000)==0); // Zero is a real nonblocking socket probe.
 assert(d.poll_timeout(2000,1000)==200);
 assert(d.poll_timeout(0,201000)==-1); // Expiry is distinct from nonblocking.
 assert(d.remaining(1000)==200);assert(d.remaining(200999)==1);assert(d.remaining(201000)==0);
 d.cancel();d.begin(300000,2000);assert(d.remaining(300001)==0); // cancellation cannot be revived by another I/O call
 // Self flags go out once per session and then only on change: every extra TCP segment
 // counts against lwIP's 8-segment writable threshold (2026-11-09: 2 per 60 ms tripped
 // the 200 ms send budget whenever path RTT spiked past ~240 ms).
 SelfFlagGate g;
 assert(g.should_send(false));assert(!g.should_send(false));assert(!g.should_send(false));
 assert(g.should_send(true));assert(!g.should_send(true));assert(g.should_send(false));
 g.reset();assert(g.should_send(false)); // a new session states its flags again
 std::cout<<"PASS bounded receive fragments and irreversible transport cancellation\n";
}
