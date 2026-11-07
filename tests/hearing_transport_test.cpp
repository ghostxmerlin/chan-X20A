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
 OperationDeadline d;d.begin(1000,200);assert(d.remaining(1000)==200);assert(d.remaining(200999)==1);assert(d.remaining(201000)==0);
 d.cancel();d.begin(300000,2000);assert(d.remaining(300001)==0); // cancellation cannot be revived by another I/O call
 std::cout<<"PASS bounded receive fragments and irreversible transport cancellation\n";
}
