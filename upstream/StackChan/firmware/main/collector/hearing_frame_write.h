/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace collector::hearing {
// RFC 6455 client frames. Keep header and payload in one write: with TCP_NODELAY,
// IDF's separate header write consumes a second lwIP pbuf for every 60 ms packet.
inline size_t pack_ws_frame(uint8_t* out,size_t capacity,const void* payload,size_t size,
                            bool binary,const uint8_t mask[4]) {
    const size_t header=size<=125?6:8;
    if(size>32768||capacity<size+header)return 0;
    out[0]=binary?0x82:0x81;
    if(size<=125)out[1]=0x80|size;
    else {out[1]=0xfe;out[2]=size>>8;out[3]=size&255;}
    std::memcpy(out+header-4,mask,4);
    const auto* src=static_cast<const uint8_t*>(payload);
    for(size_t i=0;i<size;++i)out[header+i]=src[i]^mask[i%4];
    return size+header;
}
template<class Remaining,class Write>
bool write_ws_frame(const uint8_t* frame,size_t size,Remaining remaining,Write write) {
    size_t offset=0;
    while(offset<size) {
        const int ms=remaining();if(ms<=0)return false;
        const int n=write(frame+offset,size-offset,ms);
        if(n<=0||size_t(n)>size-offset)return false;
        offset+=n;
    }
    return true;
}
}
