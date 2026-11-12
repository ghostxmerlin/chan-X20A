#include "serial_monitor.h"
#include "monitor_protocol.h"
#include "monitor_send.h"
#include "monitor_delta.h"
#include "camera_frames.h"
#include "psram_task.h"
#include "runtime_log.h"
#include <driver/usb_serial_jtag.h>
#include <driver/usb_serial_jtag_vfs.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <freertos/queue.h>
#include <atomic>
#include <algorithm>
#include <cstdio>
#include <cstdarg>
#include <cstring>

namespace collector {
namespace {
using namespace ArduinoJson;
using namespace monitor;
struct PsramAllocator:Allocator {
    void* allocate(size_t n)override{return heap_caps_malloc(n,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);}
    void deallocate(void* p)override{heap_caps_free(p);}
    void* reallocate(void* p,size_t n)override{return heap_caps_realloc(p,n,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);}
} allocator;
std::string device_id,boot_id,secret;
MonitorHooks hooks{};
std::atomic_bool subscribed{false};
std::atomic<uint32_t> tx_bytes{0},tx_dropped{0},logs_dropped{0},rx_bad{0};
struct LogLine {char text[384];};
QueueHandle_t logs=nullptr;
StaticQueue_t log_queue;
Lease lease;
struct ControlJob {uint32_t id;char body[769];};
QueueHandle_t controls=nullptr;
std::atomic<uint32_t> control_stack_free{0};
std::atomic_bool control_busy{false},result_ready{false};
ControlReply control_result;uint32_t control_id=0;
void control_worker(void*) {
    ControlJob job;
    while(true)if(xQueueReceive(controls,&job,portMAX_DELAY)==pdTRUE){
        JsonDocument doc;control_result=ControlReply{};control_id=job.id;
        if(deserializeJson(doc,job.body)) {control_result.status="400 Bad Request";control_result.body="invalid control JSON";}
        else hooks.control(doc.as<JsonObject>(),control_result);
        control_stack_free=uxTaskGetStackHighWaterMark(nullptr);
        result_ready.store(true,std::memory_order_release);
    }
}

uint32_t revision=0,asr_cursor=0,hearing_cursor=0,last_frame=0;
bool want_full=true;

int log_sink(const char* format,va_list args){
    if(!subscribed.load()||!logs)return 0;
    LogLine line{};const int n=vsnprintf(line.text,sizeof(line.text),format,args);
    // Only whole formatted records; never forward a truncated secret-bearing tail.
    if(n<=0||size_t(n)>=sizeof(line.text)){++logs_dropped;return n;}
    for(size_t i=0;i<size_t(n);++i){
        for(const char* key:{"token","password","passwd","authorization","bearer","psk"}){
            size_t k=0;while(key[k]&&i+k<size_t(n)) {char c=line.text[i+k];if(c>='A'&&c<='Z')c+=32;if(c!=key[k])break;++k;}
            if(!key[k]){std::snprintf(line.text+i,sizeof(line.text)-i,"[REDACTED]");i=n;break;}
        }
    }
    if(xQueueSend(logs,&line,0)!=pdTRUE)++logs_dropped;
    return n;
}
bool send_packet(uint8_t kind,const uint8_t* data,size_t n){
    const bool ok=send_frame(kind,data,n,[](const uint8_t* p,size_t count){
        const int sent=usb_serial_jtag_write_bytes(p,count,1);
        if(sent>0)tx_bytes+=sent;else taskYIELD();
        return sent;
    },[]{return esp_timer_get_time();});
    if(!ok)++tx_dropped;
    return ok;
}
bool send_doc(const JsonDocument& doc){
    const size_t n=measureJson(doc);
    if(doc.overflowed()||!n||n>kMaxPayload){++tx_dropped;return false;}
    auto* p=static_cast<uint8_t*>(allocator.allocate(n+1));if(!p){++tx_dropped;return false;}
    serializeJson(doc,p,n+1);const bool ok=send_packet(1,p,n);allocator.deallocate(p);return ok;
}
bool authenticated(JsonObjectConst req){
    const char* token=req["token"]|"";size_t n=std::strlen(token);
    unsigned diff=n^secret.size();for(size_t i=0;i<secret.size();++i)diff|=secret[i]^(i<n?token[i]:0);
    return diff==0;
}
void reply(uint32_t id,int status,JsonVariantConst body){
    JsonDocument out(&allocator);out["type"]="reply";out["id"]=id;out["status"]=status;out["body"].set(body);send_doc(out);
}
void error(uint32_t id,int code,const char* message){JsonDocument d(&allocator);d.set(message);reply(id,code,d.as<JsonVariantConst>());}
void command(const uint8_t* p,size_t n){
    JsonDocument req(&allocator);
    if(deserializeJson(req,p,n)||!req.is<JsonObject>()||!req["id"].is<uint32_t>()||!req["id"].as<uint32_t>()){++rx_bad;return;}
    const uint32_t id=req["id"];const char* op=req["op"]|"";
    if(!std::strcmp(op,"hello")){
        JsonDocument proof(&allocator);
        if(!hooks.proof(req["nonce"]|"",proof)){error(id,400,"invalid nonce");return;}
        proof["boot_id"]=boot_id;proof["transport"]="usb";reply(id,200,proof.as<JsonVariantConst>());return;
    }
    if(!authenticated(req.as<JsonObjectConst>())){error(id,401,"binding token required");return;}
    if(boot_id!=std::string(req["boot_id"]|"")){error(id,409,"robot restarted");return;}
    if(!std::strcmp(op,"info")){JsonDocument info(&allocator);hooks.info(info);reply(id,200,info.as<JsonVariantConst>());return;}
    if(!std::strcmp(op,"control")){
        if(!req["body"].is<JsonObject>()||measureJson(req["body"])>768){error(id,400,"invalid control body");return;}
        if(control_busy.exchange(true)){error(id,409,"another control is pending");return;}
        ControlJob job{};job.id=id;serializeJson(req["body"],job.body,sizeof(job.body));
        if(xQueueSend(controls,&job,0)!=pdTRUE){control_busy=false;error(id,503,"control queue unavailable");}
        return;
    }
    if(!std::strcmp(op,"subscribe")||!std::strcmp(op,"snapshot")){
        lease.renew(esp_timer_get_time());subscribed=true;want_full=true;
        // New subscription receives independent recent event cursors; snapshot resync preserves them.
        if(!std::strcmp(op,"subscribe")){asr_cursor=hearing_cursor=last_frame=0;}
    }else if(!std::strcmp(op,"ping")){
        if(!subscribed.load()){error(id,409,"subscription expired");return;}
        lease.renew(esp_timer_get_time());
    }else if(!std::strcmp(op,"unsubscribe")){lease.stop();subscribed=false;}
    else {error(id,400,"unknown operation");return;}
    JsonDocument result(&allocator);result["accepted"]=true;reply(id,200,result.as<JsonVariantConst>());
}
void run(void*){
    usb_serial_jtag_driver_config_t config{.tx_buffer_size=2048,.rx_buffer_size=1024};
    if(usb_serial_jtag_driver_install(&config)!=ESP_OK){vTaskDeleteWithCaps(nullptr);return;}
    usb_serial_jtag_vfs_use_driver();
    runtime_log_set_output(log_sink);
    Parser<> parser;JsonDocument previous(&allocator);
    int64_t last_state_us=0,last_full_us=0,last_events_us=0,last_frame_us=0;
    uint8_t incoming[256];
    while(true){
        const int got=usb_serial_jtag_read_bytes(incoming,sizeof(incoming),pdMS_TO_TICKS(20));
        int64_t now=esp_timer_get_time();
        if(got>0)parser.feed(incoming,got,now,command);else parser.drain(now,command);
        if(result_ready.load(std::memory_order_acquire)){
            JsonDocument body(&allocator);
            if(control_result.type=="application/json"&&!deserializeJson(body,control_result.body)){}
            else body.set(control_result.body);
            reply(control_id,control_result.code(),body.as<JsonVariantConst>());
            result_ready=false;control_busy=false;
        }
        if(!lease.active(now))subscribed=false;
        if(!subscribed.load()){previous.clear();want_full=true;LogLine discard;while(logs&&xQueueReceive(logs,&discard,0)==pdTRUE){}continue;}
        if(want_full||now-last_state_us>=1000000){
            JsonDocument state(&allocator),out(&allocator);hooks.state(state);
            out["type"]="state";out["boot_id"]=boot_id;out["revision"]=revision+1;out["base_revision"]=revision;
            const bool full=want_full||now-last_full_us>=30000000;out["full"]=full;
            if(full)out["data"].set(state);else difference(previous.as<JsonObjectConst>(),state.as<JsonObjectConst>(),out["data"].to<JsonObject>());
            if(!state.overflowed()&&send_doc(out)){previous=std::move(state);++revision;want_full=false;if(full)last_full_us=now;}
            else want_full=true;
            last_state_us=now;
        }
        if(now-last_events_us>=50000){
            JsonDocument events(&allocator);events["type"]="events";events["boot_id"]=boot_id;events["timestamp_us"]=now;
            hooks.events(events,asr_cursor,hearing_cursor);
            if(events["asr"]["events"].size()||events["hearing"]["events"].size()){
                if(send_doc(events)){
                    const JsonArrayConst asr_events=events["asr"]["events"].as<JsonArrayConst>();
                    const JsonArrayConst hearing_events=events["hearing"]["events"].as<JsonArrayConst>();
                    for(JsonObjectConst e:asr_events)asr_cursor=e["seq"].as<uint32_t>();
                    for(JsonObjectConst e:hearing_events)hearing_cursor=e["seq"].as<uint32_t>();
                }
            }last_events_us=now;
        }
        if(now-last_frame_us>=200000){
            auto frame=camera_latest_frame();
            if(frame&&frame->seq!=last_frame&&frame->length+12<=kMaxPayload){
                auto* p=static_cast<uint8_t*>(allocator.allocate(frame->length+12));
                if(p){put32(p,frame->seq);put32(p+4,uint32_t(frame->timestamp_us));put32(p+8,uint64_t(frame->timestamp_us)>>32);
                    std::memcpy(p+12,frame->bytes,frame->length);if(send_packet(2,p,frame->length+12))last_frame=frame->seq;allocator.deallocate(p);}
            }last_frame_us=now;
        }
        LogLine line;
        for(int i=0;logs&&i<4&&xQueueReceive(logs,&line,0)==pdTRUE;++i){JsonDocument d(&allocator);d["type"]="log";d["boot_id"]=boot_id;d["text"]=line.text;send_doc(d);}
    }
}
}
void serial_monitor_start(const std::string& device,const std::string& boot,const std::string& token,MonitorHooks h){
    device_id=device;boot_id=boot;secret=token;hooks=h;
    auto* storage=static_cast<uint8_t*>(allocator.allocate(16*sizeof(LogLine)));
    if(storage)logs=xQueueCreateStatic(16,sizeof(LogLine),storage,&log_queue);
    controls=xQueueCreate(1,sizeof(ControlJob));ESP_ERROR_CHECK(controls?ESP_OK:ESP_ERR_NO_MEM);
    // NVS-writing commands must run on an internal stack while flash disables cache.
    ESP_ERROR_CHECK(xTaskCreate(control_worker,"monitor_control",6144,nullptr,2,nullptr)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(create_psram_task(run,"collector_usb",12288,nullptr,2,1)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
}
void serial_monitor_stats(ArduinoJson::JsonObject out){
    out["control_stack_free_bytes"]=control_stack_free.load();
    out["transport"]="usb";out["subscribed"]=subscribed.load();out["tx_bytes"]=tx_bytes.load();out["tx_dropped"]=tx_dropped.load();
    out["logs_dropped"]=logs_dropped.load();out["rx_bad"]=rx_bad.load();out["tx_buffer_bytes"]=2048;out["rx_buffer_bytes"]=1024;
#ifdef CONFIG_COLLECTOR_NETWORK_MONITOR
    out["network_monitor"]=true;
#else
    out["network_monitor"]=false;
#endif
}
}
