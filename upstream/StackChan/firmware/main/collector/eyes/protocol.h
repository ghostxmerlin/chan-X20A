/* SPDX-License-Identifier: MIT */
#pragma once
#include "types.h"
#include <ArduinoJson.h>
#include <cstring>
namespace collector::eyes {
inline bool parse_command(const char* body,size_t size,Command& out) {
    if (!body || !size || size>384) return false;
    struct Reader {
        const char* data;size_t size,at=0;
        int read() {return at<size?static_cast<unsigned char>(data[at++]):-1;}
        size_t readBytes(char* p,size_t n) {size_t count=0;while(count<n && at<size)p[count++]=data[at++];return count;}
    } reader{body,size};
    JsonDocument doc;
    if (deserializeJson(doc,reader,DeserializationOption::NestingLimit(2)) || !doc.is<JsonObject>()) return false;
    while(reader.at<size) {const char c=body[reader.at++];if(c!=' '&&c!='\r'&&c!='\n'&&c!='\t')return false;}
    // Only flat ASCII IDs/booleans/integers are defined. Reject duplicate keys
    // (ArduinoJson otherwise keeps the last), escaped keys and embedded NULs.
    size_t fields=0;
    for(size_t i=0;i<size;++i) {if(body[i]=='\\'||!body[i])return false;if(body[i]==':')++fields;}
    if(fields!=doc.size() || !doc["op"].is<const char*>() || !doc["request_id"].is<const char*>())return false;
    const char* id=doc["request_id"];
    if(!valid_request_id(id))return false;
    Command c;std::memcpy(c.request_id,id,37);
    const char* op=doc["op"];
    if(!std::strcmp(op,"configure")) {
        if(doc.size()!=4 || !doc["enabled"].is<bool>() || !doc["style"].is<const char*>())return false;
        c.op=Operation::Configure;c.enabled=doc["enabled"].as<bool>();c.style=parse_style(doc["style"]);
    } else if(!std::strcmp(op,"stop")) {
        if(doc.size()!=2)return false;
        c.op=Operation::Stop;
    } else if(!std::strcmp(op,"play")) {
        if(!doc["state"].is<const char*>() || !doc["mode"].is<const char*>())return false;
        c.state=parse_state(doc["state"]);const char* mode=doc["mode"];
        if(!std::strcmp(mode,"once")) {if(doc.size()!=4)return false;c.mode=Mode::Once;}
        else if(!std::strcmp(mode,"loop")) {
            if(doc.size()!=5 || !doc["loop_for_ms"].is<uint32_t>())return false;
            c.mode=Mode::Loop;c.loop_for_ms=doc["loop_for_ms"].as<uint32_t>();
        } else return false;
    } else return false;
    if(!valid_command(c))return false;
    out=c;return true;
}
}
