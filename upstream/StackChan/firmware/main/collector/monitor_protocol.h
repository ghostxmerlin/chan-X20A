#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace collector::monitor {
constexpr size_t kHeaderSize=16, kMaxPayload=131072;
inline uint32_t crc32(const void* data,size_t n) {
    uint32_t crc=0xffffffffu;const auto* p=static_cast<const uint8_t*>(data);
    while(n--) {crc^=*p++;for(int i=0;i<8;++i)crc=(crc>>1)^(0xedb88320u&uint32_t(-int32_t(crc&1)));}
    return ~crc;
}
inline void put32(uint8_t* p,uint32_t x){for(int i=0;i<4;++i)p[i]=x>>(i*8);}
inline uint32_t get32(const uint8_t* p){return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
struct Header {uint8_t kind=0;uint32_t length=0,crc=0;};
inline void header_for(uint8_t* p,uint8_t kind,const void* payload,size_t n){
    std::memcpy(p,"SCM1",4);p[4]=kind;p[5]=1;p[6]=p[7]=0;put32(p+8,n);put32(p+12,crc32(payload,n));
}
inline bool parse_header(const uint8_t* p,Header& h){
    if(std::memcmp(p,"SCM1",4)||p[4]<1||p[4]>2||p[5]!=1||p[6]||p[7])return false;
    h={p[4],get32(p+8),get32(p+12)};return h.length>0&&h.length<=kMaxPayload;
}
struct Lease {
    int64_t until=0;
    void renew(int64_t now){until=now+6000000;}
    bool active(int64_t now)const{return until&&now<until;}
    void stop(){until=0;}
};
// Small command parser; oversized claims never allocate. A 250 ms assembly
// deadline lets a truncated candidate release a valid frame that follows it.
template<size_t Capacity=2064> struct Parser {
    uint8_t bytes[Capacity]{};size_t used=0;int64_t began=0;
    void drop(size_t n){std::memmove(bytes,bytes+n,used-n);used-=n;began=0;}
    template<class F> void drain(int64_t now,F accept){
        while(used>=16){Header h;
            if(!parse_header(bytes,h)||h.kind!=1||h.length>Capacity-16){drop(1);continue;}
            if(!began)began=now;
            if(used<16+h.length){if(now-began>250000){drop(1);continue;}return;}
            if(crc32(bytes+16,h.length)!=h.crc){drop(1);continue;}
            accept(bytes+16,h.length);drop(16+h.length);
        }
    }
    template<class F> void feed(const uint8_t* p,size_t n,int64_t now,F accept){
        while(n--){if(used==Capacity)drop(1);bytes[used++]=*p++;drain(now,accept);}
    }
};
}
