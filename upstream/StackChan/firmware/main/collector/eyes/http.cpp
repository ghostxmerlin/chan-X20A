/* SPDX-License-Identifier: MIT */
#include "http.h"
#include "eyes.h"
#include "protocol.h"
#include "hal/board/shared_storage.h"
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <lwip/sockets.h>
#include <algorithm>
#include <atomic>
#include <new>
#include <string>

namespace collector::eyes {
namespace {
std::atomic_bool installing{false};
esp_err_t send(httpd_req_t* r,const JsonDocument& doc) {
    std::string body;serializeJson(doc,body);httpd_resp_set_type(r,"application/json");
    httpd_resp_set_hdr(r,"Cache-Control","no-store");return httpd_resp_send(r,body.data(),body.size());
}
esp_err_t reject(httpd_req_t* r,const char* status,const char* error) {
    httpd_resp_set_status(r,status);httpd_resp_set_hdr(r,"Connection","close");
    JsonDocument doc;doc["accepted"]=false;doc["error"]=error;send(r,doc);return ESP_FAIL;
}
void request_json(JsonObject out,const RequestResult& r) {
    out["request_id"]=r.request_id;out["request_seq"]=r.request_seq;out["op"]=name(r.operation);
    out["status"]=name(r.status);out["error"]=r.error==Error::None?nullptr:name(r.error);
}
struct Upload {httpd_req_t* request=nullptr;const Asset* asset=nullptr;uint8_t* bytes=nullptr;};
void upload_task(void* parameter) {
    auto* u=static_cast<Upload*>(parameter);auto* r=u->request;
    const int64_t deadline=esp_timer_get_time()+10000000;size_t at=0;
    while(at<u->asset->bytes && esp_timer_get_time()<deadline) {
        const int got=httpd_req_recv(r,reinterpret_cast<char*>(u->bytes+at),std::min<size_t>(4096,u->asset->bytes-at));
        if(got==HTTPD_SOCK_ERR_TIMEOUT) {vTaskDelay(1);continue;}
        if(got<=0)break;
        at+=got;vTaskDelay(1);
    }
    const bool incomplete=at!=u->asset->bytes || esp_timer_get_time()>=deadline;
    bool unchanged=false;
    const char* error=incomplete?"incomplete_or_timed_out_upload":shared_storage::install(*u->asset,u->bytes,at,unchanged);
    JsonDocument doc;doc["accepted"]=!error;doc["installed"]=!error;doc["unchanged"]=unchanged;
    doc["style"]=style_id(u->asset->style);doc["state"]=state_id(u->asset->state);doc["error"]=error;
    httpd_resp_set_status(r,error?(incomplete||!std::strcmp(error,"asset_invalid")?"400 Bad Request":"503 Service Unavailable"):"200 OK");
    if(incomplete)httpd_resp_set_hdr(r,"Connection","close");
    const auto sent=send(r,doc);
    if(incomplete || sent!=ESP_OK)shutdown(httpd_req_to_sockfd(r),SHUT_RDWR);
    httpd_req_async_handler_complete(r);heap_caps_free(u->bytes);delete u;
    installing=false;vTaskDelete(nullptr);
}
}
void catalog_json(JsonObject out) {
    out["supported"]=true;out["version"]=1;
    auto styles=out["styles"].to<JsonArray>();
    for(size_t i=0;i<kStyleCount;++i) {auto o=styles.add<JsonObject>();o["id"]=style_id(Style(i));o["label"]=kStyleLabels[i];}
    auto states=out["states"].to<JsonArray>();
    for(size_t i=0;i<kStateCount;++i) {auto o=states.add<JsonObject>();o["id"]=state_id(State(i));o["label"]=kStateLabels[i];}
    out["modes"][0]="once";out["modes"][1]="loop";out["loop_min_ms"]=1000;out["loop_max_ms"]=30000;
    out["first_frame_deadline_ms"]=1500;out["request_history_count"]=16;out["request_history_ms"]=60000;
    out["asset_install"]=true;out["max_asset_bytes"]=kAssetCapacity;out["asr_open"]=true;out["persistent"]=false;
}
void status_json(JsonObject out) {
    const auto s=snapshot();out["supported"]=s.supported;out["ready"]=s.ready;out["enabled"]=s.enabled;
    out["storage_ready"]=s.storage_ready;out["memory_ready"]=s.memory_ready;
    out["requested_style"]=style_id(s.requested_style);out["selected_style"]=style_id(s.selected_style);
    out["active_style"]=valid(s.active_style)?style_id(s.active_style):nullptr;
    out["state"]=state_id(s.state);out["mode"]=name(s.mode);out["source"]=name(s.source);out["phase"]=name(s.phase);
    out["visible"]=s.visible;out["suspended_reason"]=s.suspended_reason==Error::None?nullptr:name(s.suspended_reason);
    out["generation"]=s.generation;out["frame_index"]=s.frame_index;out["submitted_seq"]=s.submitted_seq;out["rendered_seq"]=s.rendered_seq;
    request_json(out["last_request"].to<JsonObject>(),s.last_request);
    out["last_error"]=s.last_error==Error::None?nullptr:name(s.last_error);out["last_error_seq"]=s.last_error_seq;
    auto stats=out["stats"].to<JsonObject>();stats["load_us"]=s.stats.load_us;stats["decode_us"]=s.stats.decode_us;
    stats["max_decode_us"]=s.stats.max_decode_us;stats["max_lateness_us"]=s.stats.max_lateness_us;
    stats["decoded"]=s.stats.decoded;stats["skipped"]=s.stats.skipped;stats["failures"]=s.stats.failures;
    stats["allocated_bytes"]=s.stats.allocated_bytes;stats["internal_low_water_bytes"]=s.stats.internal_low_water_bytes;
    auto sd=shared_storage::status();auto storage=out["storage"].to<JsonObject>();storage["mounted"]=sd.mounted;
    storage["mount_error"]=sd.mount_error;storage["reads"]=sd.reads;storage["installs"]=sd.installs;
    storage["failures"]=sd.failures;storage["read_us"]=sd.read_us;storage["write_us"]=sd.write_us;storage["installing"]=installing.load();
}
esp_err_t catalog_handler(httpd_req_t* r) {JsonDocument doc;catalog_json(doc.to<JsonObject>());return send(r,doc);}
esp_err_t status_handler(httpd_req_t* r) {
    JsonDocument doc;status_json(doc.to<JsonObject>());
    const size_t length=httpd_req_get_url_query_len(r);
    if(length) {
        char query_string[64],id[37];
        if(length>=sizeof(query_string) || httpd_req_get_url_query_str(r,query_string,sizeof(query_string))!=ESP_OK ||
           std::strncmp(query_string,"request_id=",11) || std::strlen(query_string)!=47 ||
           httpd_query_key_value(query_string,"request_id",id,sizeof(id))!=ESP_OK || !valid_request_id(id))
            return reject(r,"400 Bad Request","invalid_argument");
        request_json(doc["request"].to<JsonObject>(),query(id));
    }
    return send(r,doc);
}
esp_err_t command_handler(httpd_req_t* r) {
    if(r->content_len>384)return reject(r,"413 Payload Too Large","body_too_large");
    if(!r->content_len)return reject(r,"400 Bad Request","invalid_argument");
    char body[385];size_t at=0;const int64_t deadline=esp_timer_get_time()+3000000;
    while(at<r->content_len) {
        if(esp_timer_get_time()>=deadline)return reject(r,"408 Request Timeout","body_timeout");
        const int got=httpd_req_recv(r,body+at,r->content_len-at);
        if(got<=0)return reject(r,"400 Bad Request","incomplete_body");
        at+=got;
    }
    Command c;if(!parse_command(body,at,c))return reject(r,"400 Bad Request","invalid_argument");
    const auto result=submit(c,Source::Manual);
    if(!result.accepted) return reject(r,result.error==Error::StorageUnavailable||result.error==Error::NoMemory?"503 Service Unavailable":"409 Conflict",name(result.error));
    JsonDocument doc;request_json(doc.to<JsonObject>(),query(c.request_id));doc["accepted"]=true;doc["duplicate"]=result.duplicate;
    httpd_resp_set_status(r,"202 Accepted");return send(r,doc);
}
esp_err_t install_handler(httpd_req_t* r) {
    char style[8],state[32];
    if(httpd_req_get_hdr_value_str(r,"X-Eyes-Style",style,sizeof(style))!=ESP_OK ||
       httpd_req_get_hdr_value_str(r,"X-Eyes-State",state,sizeof(state))!=ESP_OK)
        return reject(r,"400 Bad Request","invalid_argument");
    const auto* a=asset(parse_style(style),parse_state(state));
    if(!a || r->content_len!=a->bytes || r->content_len>kAssetCapacity)return reject(r,"400 Bad Request","invalid_asset_length");
    if(!shared_storage::available())return reject(r,"503 Service Unavailable","storage_unavailable");
    if(installing.exchange(true))return reject(r,"409 Conflict","install_busy");
    auto* u=new(std::nothrow)Upload;
    if(u) {u->asset=a;u->bytes=static_cast<uint8_t*>(heap_caps_malloc(a->bytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));}
    if(!u || !u->bytes) {delete u;installing=false;return reject(r,"503 Service Unavailable","no_memory");}
    if(httpd_req_async_handler_begin(r,&u->request)!=ESP_OK) {
        heap_caps_free(u->bytes);delete u;installing=false;return reject(r,"503 Service Unavailable","request_unavailable");
    }
    if(xTaskCreate(upload_task,"eyes_install",12288,u,1,nullptr)!=pdPASS) {
        reject(u->request,"503 Service Unavailable","task_unavailable");shutdown(httpd_req_to_sockfd(u->request),SHUT_RDWR);
        httpd_req_async_handler_complete(u->request);heap_caps_free(u->bytes);delete u;installing=false;
    }
    return ESP_OK;
}
}
