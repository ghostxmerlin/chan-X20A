/* SPDX-License-Identifier: MIT */
#pragma once
#include "types.h"
#include <ArduinoJson.h>
#include <cstring>
#include <string>
namespace collector::eyes {
inline void request_json(JsonObject out,const RequestResult& r) {
    // const char arrays are borrowed by ArduinoJson. Own this snapshot string
    // so telemetry and query responses remain valid after the result expires.
    out["request_id"]=std::string(r.request_id);out["request_seq"]=r.request_seq;out["op"]=name(r.operation);
    out["status"]=name(r.status);out["error"]=r.error==Error::None?nullptr:name(r.error);
}
// ArduinoJson accepts some JavaScript extensions. This API defines only flat
// JSON strings, booleans and integers, with double-quoted keys and no trailing
// comma. No defined identifier needs escape sequences.
inline bool strict_command_json(const char* body,size_t size) {
    size_t at=0;
    auto whitespace=[&] {while(at<size && (body[at]==' '||body[at]=='\t'||body[at]=='\r'||body[at]=='\n'))++at;};
    auto quoted=[&] {
        if(at>=size||body[at++]!='"')return false;
        while(at<size && body[at]!='"') {
            if(static_cast<unsigned char>(body[at])<32 || body[at]=='\\')return false;
            ++at;
        }
        if(at>=size)return false;
        ++at;return true;
    };
    whitespace();if(at>=size||body[at++]!='{')return false;whitespace();
    if(at<size && body[at]=='}') {++at;whitespace();return at==size;}
    for(;;) {
        if(!quoted())return false;
        whitespace();if(at>=size||body[at++]!=':')return false;whitespace();if(at>=size)return false;
        if(body[at]=='"') {if(!quoted())return false;}
        else if(size-at>=4 && !std::memcmp(body+at,"true",4))at+=4;
        else if(size-at>=5 && !std::memcmp(body+at,"false",5))at+=5;
        else {
            if(body[at]=='-')++at;
            const size_t begin=at;
            while(at<size && body[at]>='0' && body[at]<='9')++at;
            if(at==begin || (body[begin]=='0' && at-begin>1))return false;
        }
        whitespace();if(at>=size)return false;
        if(body[at]=='}') {++at;whitespace();return at==size;}
        if(body[at++]!=',')return false;
        whitespace();
    }
}
inline bool parse_command(const char* body,size_t size,Command& out) {
    if (!body || !size || size>384 || !strict_command_json(body,size)) return false;
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
