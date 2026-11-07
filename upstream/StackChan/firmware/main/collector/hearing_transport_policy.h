/* SPDX-License-Identifier: MIT */
#pragma once
#include <atomic>
#include <cstdint>
#include <string>
namespace collector::hearing {
struct OperationDeadline {
    std::atomic<bool> cancelled{false};int64_t until=0;
    void begin(int64_t now,int ms){until=now+int64_t(ms)*1000;}
    void cancel(){cancelled=true;}
    int remaining(int64_t now)const {return cancelled||now>=until?0:static_cast<int>((until-now+999)/1000);}
};
struct MessageBuffer {
    enum Result {Invalid,More,Complete};
    std::string message;
    size_t remaining=0;bool fragmented=false;
    void clear(){message.clear();remaining=0;fragmented=false;}
    Result add(int opcode,bool fin,size_t payload,const char* data,size_t len) {
        if(!remaining) {
            if((opcode==1&&fragmented)||(opcode==0&&!fragmented)||(opcode!=0&&opcode!=1)||payload>8192-message.size())return Invalid;
            remaining=payload;
        }
        if(len>remaining)return Invalid;
        message.append(data,len);remaining-=len;
        if(!remaining){fragmented=!fin;return fin?Complete:More;}
        return More;
    }
};
}
