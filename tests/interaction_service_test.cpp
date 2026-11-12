#include "collector/interaction.h"
#include "collector/interaction_policy.h"
#include "collector/remote.h"
#include "collector/hearing.h"
#include "collector/m12_unit.h"
#include <cassert>
#include <cstring>
#include <string>
using namespace collector;
int64_t fake_now=1000000;
namespace {RemoteSnapshot remote;M12Snapshot face;bool tracking=false,motor_ready=true;uint32_t mode_request=0;int camera_calls=0,owner_calls=0;hearing::ListeningControl listening;}
int64_t esp_timer_get_time(){return fake_now;}
namespace collector {
RemoteSnapshot remote_snapshot(){return remote;}
esp_err_t remote_set_tracking(bool on){if(on&&!motor_ready)return ESP_ERR_INVALID_STATE;tracking=on;return ESP_OK;}
bool remote_tracking_requested(){return tracking;}
void remote_tracking_json(ArduinoJson::JsonObject out){out["requested"]=tracking;}
M12Snapshot m12_snapshot(){return face;}
uint32_t m12_mode_request(){return mode_request;}
uint32_t m12_set_background_mode(M12Function mode){++camera_calls;mode_request=next_m12_request(mode_request,mode);face.function=mode;face.requested_us=fake_now;face.mode_request_revision=mode_request;face.count=0;face.frame_us=0;return mode_request;}
void m12_set_view(bool on){m12_set_background_mode(on?M12Function::detect:M12Function::standby);}
namespace hearing {
ListeningIntent listening_intent(){return listening.snapshot();}
bool set_listening(bool on){return listening.set_listening(on,true);}
bool toggle_listening(){return listening.toggle_listening(true);}
OwnerStart start_for_owner(uint32_t expected,uint32_t& token){++owner_calls;return listening.start_for_owner(expected,true,token);}
void stop_for_owner(uint32_t token){listening.stop_for_owner(token);}
}
}
int httpd_req_recv(httpd_req_t* r,char* out,size_t n){const auto count=std::min(n,r->body.size()-r->received);std::memcpy(out,r->body.data()+r->received,count);r->received+=count;return int(count);}
esp_err_t httpd_resp_set_status(httpd_req_t* r,const char* status){r->status=status;return ESP_OK;}
esp_err_t httpd_resp_set_type(httpd_req_t*,const char*){return ESP_OK;}
esp_err_t httpd_resp_send(httpd_req_t* r,const char* out,size_t n){r->response.assign(out,n);return ESP_OK;}
void tick(){fake_now+=100000;interaction::tick(fake_now);}
void known(const char* name="registered"){fake_now+=200000;face.frame_us=fake_now;face.frame_seq++;face.online=true;face.count=1;auto& f=face.faces[0];f.x=200;f.y=100;f.w=100;f.h=100;f.prob=.9f;std::snprintf(f.name,sizeof(f.name),"%s",name);interaction::tick(fake_now);}
std::string dialogue(){ArduinoJson::JsonDocument d;interaction::json(d.to<ArduinoJson::JsonObject>());return d["dialogue"]["state"].as<std::string>();}
int main(){
    assert(interaction::set_mode("tracking",true)==ESP_ERR_INVALID_STATE);assert(!tracking);
    face.valid=true;assert(interaction::set_mode("advanced",true)==ESP_OK);assert(camera_calls==0&&!listening.requested()&&!tracking);
    assert(interaction::set_mode("invalid",false)==ESP_ERR_INVALID_ARG);
    motor_ready=false;assert(interaction::set_mode("tracking",true)==ESP_ERR_INVALID_STATE);motor_ready=true;
    interaction::asr_command(0x10,true);assert(!tracking);
    interaction::asr_command(0x10,false);assert(tracking&&face.function==M12Function::detect);
    const auto first_calls=camera_calls;interaction::asr_command(0x10,false);assert(camera_calls==first_calls);
    interaction::asr_command(0x1b,false);assert(tracking&&camera_calls==first_calls);
    assert(interaction::set_mode("recognition",true)==ESP_OK);assert(face.function==M12Function::detect);
    interaction::asr_command(0x11,false);assert(!tracking&&face.function==M12Function::detect);
    // A newly requested recognition mode must not accept frames from the previous UART command.
    const auto applied_revision=face.mode_request_revision;
    face.mode_request_revision=applied_revision-4;
    known();known();assert(!listening.requested()&&owner_calls==0);
    face.mode_request_revision=applied_revision;
    known("");assert(!listening.requested());known();assert(!listening.requested());   // nameless face does not count
    tick();assert(!listening.requested());known();assert(listening.requested()&&dialogue()=="listening"&&owner_calls==1);
    hearing::set_listening(false);known();assert(!listening.requested()&&owner_calls==1&&dialogue()=="stopped_by_user");
    interaction::set_mode("recognition",false);assert(face.function==M12Function::standby);
    interaction::set_mode("recognition",true);known();known();assert(listening.requested());
    hearing::set_listening(true);interaction::set_mode("recognition",false);assert(listening.requested()); // manual takeover
    hearing::set_listening(false);interaction::set_mode("recognition",true);known();known();assert(listening.requested());
    interaction::set_mode("recognition",false);assert(!listening.requested()); // own session is stopped
    remote.mode_left_seq++;tick();assert(tracking);const auto calls=camera_calls;
    tick();assert(tracking&&camera_calls==calls);remote.mode_left_seq+=2;tick();assert(tracking); // coalesced parity
    remote.mode_right_seq++;tick();assert(face.function==M12Function::detect);
    remote.mode_left_seq++;tick();assert(!tracking&&face.function==M12Function::detect);
    remote.mode_up_seq++;tick();assert(listening.requested());remote.mode_up_seq++;tick();assert(!listening.requested());
    remote.mode_down_seq++;tick();assert(!listening.requested()&&!tracking);
    mode_request=next_m12_request(mode_request,M12Function::detect);face.function=M12Function::detect;
    const auto manual_calls=camera_calls;tick();assert(!tracking&&camera_calls==manual_calls&&face.function==M12Function::detect);
    interaction::set_mode("tracking",true);interaction::asr_command(0x15,false);
    assert(!tracking&&face.function==M12Function::standby); // turn off applies immediately, without LVGL polling
    interaction::asr_command(0x12,false);assert(face.function==M12Function::standby);
    interaction::asr_command(0x14,false);assert(!tracking&&face.function==M12Function::detect);
    interaction::asr_command(0x10,false);tick();assert(tracking&&face.function==M12Function::detect);
    // 0.4.46: /v1/control parses the body and hands the target-free object to the module.
    auto handle=[](const std::string& body){ControlReply r;ArduinoJson::JsonDocument d;deserializeJson(d,body);interaction::handle(&r,d.as<ArduinoJson::JsonObject>());return r.status;};
    assert(handle("{\"mode\":\"advanced\",\"enabled\":false}")=="202 Accepted");
    for(const std::string body:{"{\"mode\":\"advanced\",\"enabled\":1}","{\"mode\":\"bad\",\"enabled\":false}","{\"mode\":\"advanced\",\"enabled\":false,\"extra\":1}"})
        assert(handle(body)=="400 Bad Request");
}
