#include "monitor_protocol.h"
#include <cassert>
#include <cstring>
#include <vector>
using namespace collector::monitor;
int main() {
    assert(crc32("123456789",9)==0xcbf43926u);
    const uint8_t expected[]={83,67,77,49,1,1,0,0,2,0,0,0,67,191,166,163}; // CRC of {}
    uint8_t header[16]; header_for(header,1,"{}",2);
    assert(std::memcmp(header,expected,16)==0);
    Header h{}; assert(parse_header(header,h)&&h.kind==1&&h.length==2);
    header[5]=2; assert(!parse_header(header,h)); header[5]=1;
    header[8]=1;header[9]=0;header[10]=2; assert(!parse_header(header,h));
    Lease lease; assert(!lease.active(0)); lease.renew(100); assert(lease.active(5999999)); assert(!lease.active(6000100));
    lease.stop(); assert(!lease.active(100));
    Parser<> parser;int got=0;auto receive=[&](const uint8_t* p,size_t n){assert(n==2&&!std::memcmp(p,"{}",2));++got;};
    header_for(header,1,"{}",2);std::vector<uint8_t> wire(header,header+16);wire.push_back('{');wire.push_back('}');
    const uint8_t noise[]={0,1,'S','C'};parser.feed(noise,sizeof(noise),1,receive);
    for(auto c:wire)parser.feed(&c,1,2,receive);assert(got==1);
    auto bad=wire;bad.back()='x';parser.feed(bad.data(),bad.size(),3,receive);parser.feed(wire.data(),wire.size(),4,receive);assert(got==2);
    auto cut=wire;put32(cut.data()+8,1000);parser.feed(cut.data(),cut.size(),5,receive);parser.feed(wire.data(),wire.size(),6,receive);assert(got==2);parser.drain(300007,receive);assert(got==3);
}
