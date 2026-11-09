/* SPDX-License-Identifier: MIT */
#include "interaction.h"
#include "interaction_policy.h"
#include "m12_unit.h"
#include "remote.h"
#include "hearing.h"
#include <esp_timer.h>
#include <cstring>
#include <mutex>
namespace collector::interaction {
namespace {
std::mutex mutex;
bool recognition=false,advanced=false;
OwnerGate owner;
uint32_t owner_expected=0,owner_token=0,owned_m12=0;
uint32_t left_seen=0,right_seen=0,up_seen=0,down_seen=0;
M12Function selected=M12Function::none;
const char* dialogue="off";
const char* last_error="";
int64_t last_tick=0;

void apply_camera_locked() {
    const auto wanted=camera_mode(remote_tracking_requested(),recognition);
    if(wanted==M12Function::standby) {
        if(owned_m12 && m12_mode_request()==owned_m12)m12_set_background_mode(wanted);
        owned_m12=0;selected=M12Function::none;return;
    }
    if(!owned_m12||selected!=wanted) {
        owned_m12=m12_set_background_mode(wanted);selected=wanted;
    }
}
void release_locked() {
    remote_set_tracking(false);
    if(owner_token)hearing::stop_for_owner(owner_token);
    owner_token=0;recognition=false;owner.reset(0);dialogue="off";
    owned_m12=0;selected=M12Function::none;
}
esp_err_t set_locked(const char* mode,bool enabled,int64_t now) {
    if(!mode)return ESP_ERR_INVALID_ARG;
    if(!std::strcmp(mode,"advanced")) {advanced=enabled;last_error="";return ESP_OK;}
    if(std::strcmp(mode,"tracking") && std::strcmp(mode,"recognition"))return ESP_ERR_INVALID_ARG;
    if(enabled&&!m12_snapshot().valid) {last_error="m12_unavailable";return ESP_ERR_INVALID_STATE;}
    if(!std::strcmp(mode,"tracking")) {
        const auto result=remote_set_tracking(enabled);
        if(result!=ESP_OK){last_error="motor_unavailable";return result;}
    } else {
        if(recognition==enabled)return ESP_OK;
        recognition=enabled;owner.reset(now);
        if(enabled) {owner_expected=hearing::listening_intent().revision;owner_token=0;dialogue="waiting_owner";}
        else {if(owner_token)hearing::stop_for_owner(owner_token);owner_token=0;dialogue="off";}
    }
    last_error="";apply_camera_locked();return ESP_OK;
}
esp_err_t reply(httpd_req_t* r,const char* status,const char* error) {
    httpd_resp_set_status(r,status);httpd_resp_set_type(r,"application/json");
    ArduinoJson::JsonDocument doc;doc["accepted"]=error==nullptr;if(error)doc["error"]=error;
    std::string body;serializeJson(doc,body);return httpd_resp_send(r,body.c_str(),body.size());
}
}
Indicators indicators() {
    std::lock_guard<std::mutex> lock(mutex);
    return {remote_tracking_requested(),recognition};
}
esp_err_t set_mode(const char* mode,bool enabled) {
    std::lock_guard<std::mutex> lock(mutex);return set_locked(mode,enabled,esp_timer_get_time());
}
void release_for_manual_m12() {std::lock_guard<std::mutex> lock(mutex);release_locked();}
void asr_command(uint8_t id,bool has_value) {
    switch(asr_mode_action(id,has_value)) {
    case AsrModeAction::OpenTracking:set_mode("tracking",true);break;
    case AsrModeAction::CloseTracking:set_mode("tracking",false);break;
    case AsrModeAction::ReleaseModes: {
        // Consume the visual mode in UART order, never later from a collapsed LVGL snapshot.
        std::lock_guard<std::mutex> lock(mutex);
        release_locked();m12_set_view(id==0x14);break;
    }
    default:break;
    }
}
void tick(int64_t now) {
    std::lock_guard<std::mutex> lock(mutex);
    if(last_tick&&now-last_tick<50000)return;
    last_tick=now;
    const auto remote=remote_snapshot();
    // Any explicit preview/mode command supersedes background ownership.
    if(owned_m12&&m12_mode_request()!=owned_m12)release_locked();
    if(toggle_due(left_seen,remote.mode_left_seq))set_locked("tracking",!remote_tracking_requested(),now);
    if(toggle_due(right_seen,remote.mode_right_seq))set_locked("recognition",!recognition,now);
    if(toggle_due(up_seen,remote.mode_up_seq))hearing::toggle_listening();
    if(toggle_due(down_seen,remote.mode_down_seq))set_locked("advanced",!advanced,now);
    left_seen=remote.mode_left_seq;right_seen=remote.mode_right_seq;
    up_seen=remote.mode_up_seq;down_seen=remote.mode_down_seq;
    // Motor disable/manual web control may have released tracking independently.
    if(owned_m12)apply_camera_locked();
    if(!recognition)return;
    const auto face=m12_snapshot();
    const bool current_mode=owned_m12 && face.mode_request_revision==owned_m12 &&
                            m12_mode_request()==owned_m12;
    if(owner.observe({face.frame_seq,face.frame_us,face.requested_us,face.frame_recognition&&current_mode,
                     face.function,face.count,face.faces},now)) {
        switch(hearing::start_for_owner(owner_expected,owner_token)) {
        case hearing::OwnerStart::Started:dialogue="listening";break;
        case hearing::OwnerStart::AlreadyListening:dialogue="shared_listening";break;
        case hearing::OwnerStart::Unavailable:dialogue=hearing::listening_intent().enrolling?"enrolling":"backend_unavailable";break;
        case hearing::OwnerStart::Superseded:dialogue="stopped_by_user";break;
        }
    }
    const auto intent=hearing::listening_intent();
    if(owner_token&&intent.revision!=owner_token)owner_token=0;
    if(owner.confirmed()) {
        if(intent.enrolling)dialogue="enrolling";
        else if(intent.requested)dialogue=owner_token?"listening":"shared_listening";
        else if(std::strcmp(dialogue,"backend_unavailable"))dialogue="stopped_by_user";
    }
}
void json(ArduinoJson::JsonObject out) {
    std::lock_guard<std::mutex> lock(mutex);
    out["supported"]=true;out["last_error"]=last_error;
    remote_tracking_json(out["tracking"].to<ArduinoJson::JsonObject>());
    auto r=out["recognition"].to<ArduinoJson::JsonObject>();r["requested"]=recognition;
    r["state"]=!recognition?"off":owner.confirmed()?"owner_confirmed":owner.seen()?"confirming":"waiting_face";
    r["owner_seen"]=recognition&&owner.seen();r["owner_name"]=recognition?owner.name():"";
    r["owner_policy"]="any_registered_face";r["confirmation_frames"]=2;
    const auto intent=hearing::listening_intent();
    out["dialogue"]["state"]=dialogue;out["dialogue"]["owned"]=owner_token&&intent.revision==owner_token;
    out["advanced"]["requested"]=advanced;out["advanced"]["supported"]=false;out["advanced"]["state"]="not_implemented";
}
esp_err_t handle(httpd_req_t* request,ArduinoJson::JsonObject doc) {
    if(doc.size()!=2||!doc["mode"].is<const char*>()||!doc["enabled"].is<bool>())return reply(request,"400 Bad Request","invalid_command");
    const auto result=set_mode(doc["mode"],doc["enabled"]);
    return result==ESP_OK?reply(request,"202 Accepted",nullptr):
        reply(request,result==ESP_ERR_INVALID_ARG?"400 Bad Request":"409 Conflict",result==ESP_ERR_INVALID_ARG?"invalid_mode":"mode_unavailable");
}
}
