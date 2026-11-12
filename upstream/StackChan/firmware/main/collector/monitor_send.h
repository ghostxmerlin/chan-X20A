#pragma once
#include "monitor_protocol.h"
#include <algorithm>

namespace collector::monitor {
// Writer and clock are injected so stalled and slowly progressing USB writes
// can be exercised without an ESP32 or wall-clock sleeps.
template<class Write, class Now>
bool send_frame(uint8_t kind,const uint8_t* payload,size_t size,Write write,Now now) {
    if(!size||size>kMaxPayload)return false;
    uint8_t header[kHeaderSize];header_for(header,kind,payload,size);
    const int64_t deadline=now()+1000000;
    int64_t last_progress=now();
    auto part=[&](const uint8_t* p,size_t n){
        while(n){
            const int64_t current=now();
            // A slow reader may still make progress. Bound the whole packet,
            // but release a stopped reader after only 100 ms without progress.
            if(current>deadline||current-last_progress>100000)return false;
            const int sent=write(p,std::min<size_t>(256,n));
            if(sent>0){p+=sent;n-=sent;last_progress=now();}
        }
        return true;
    };
    return part(header,sizeof(header))&&part(payload,size);
}
}
