#include "hearing_frame_write.h"
#include <cassert>
#include <cstring>
#include <vector>
#include <iostream>
using namespace collector::hearing;
int main() {
    for (size_t n : {0u, 1u, 125u, 126u, 318u, 8192u, 32768u}) {
        std::vector<uint8_t> input(n, 0x5a), original=input, frame(n+8), received;
        const uint8_t mask[4]={1,17,93,255};
        const size_t size=pack_ws_frame(frame.data(),frame.size(),input.data(),n,true,mask);
        assert(size==n+(n<=125?6:8));
        assert(frame[0]==0x82 && (frame[1]&0x80));
        const size_t header=size-n;
        assert(!std::memcmp(frame.data()+header-4,mask,4));
        if(n<=125)assert((frame[1]&127)==n);
        else assert(frame[1]==254 && ((size_t(frame[2])<<8)|frame[3])==n);
        for(size_t i=0;i<n;++i)assert((frame[header+i]^mask[i%4])==input[i]);
        assert(input==original);
        // A writable socket receives the header and payload together, including empty messages.
        int calls=0;
        assert(write_ws_frame(frame.data(),size,[]{return 1000;},[&](const uint8_t* p,size_t len,int){
            ++calls;received.insert(received.end(),p,p+len);return int(len);
        }));
        assert(calls==1 && received==std::vector<uint8_t>(frame.begin(),frame.begin()+size));
        // Partial TCP/TLS writes advance through the same frame, never resend its prefix.
        received.clear();calls=0;
        assert(write_ws_frame(frame.data(),size,[]{return 1000;},[&](const uint8_t* p,size_t len,int){
            ++calls;len=std::min(len,size_t(7));received.insert(received.end(),p,p+len);return int(len);
        }));
        assert(received==std::vector<uint8_t>(frame.begin(),frame.begin()+size));
        int remaining=3;
        assert(!write_ws_frame(frame.data(),size,[&]{return remaining--;},[](const uint8_t*,size_t,int){return 1;}));
        assert(!write_ws_frame(frame.data(),size,[]{return 1000;},[](const uint8_t*,size_t,int){return 0;}));
        assert(!write_ws_frame(frame.data(),size,[]{return 1000;},[](const uint8_t*,size_t,int){return -1;}));
    }
    uint8_t out[132],mask[4]={};
    assert(pack_ws_frame(out,sizeof out,"",0,false,mask)==6 && out[0]==0x81);
    assert(pack_ws_frame(out,5,"",0,false,mask)==0);
    assert(pack_ws_frame(out,sizeof out,"",32769,true,mask)==0);
    std::cout<<"PASS coalesced immutable masked frames, partial writes, deadlines and failures\n";
}
