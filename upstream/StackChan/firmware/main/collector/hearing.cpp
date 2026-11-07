/* SPDX-License-Identifier: MIT */
#include "hearing.h"
#include "hearing_policy.h"
#include "playback.h"
#include <esp_network.h>
#include <web_socket.h>
#include <esp_opus_enc.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_random.h>
#include <nvs.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <vector>

namespace collector::hearing {
using namespace ArduinoJson;
namespace {
constexpr size_t kFrames=480,kQueue=32;
struct Block {uint32_t session,seq;int64_t us;bool sounding;int16_t pcm[kFrames];};
std::mutex mutex;
QueueHandle_t queue=nullptr;
StaticQueue_t queue_control;
std::atomic<uint32_t> capture_session{0},dropped{0};
std::atomic<int64_t> quiet_until{0};
std::string url,secret,device,boot;
bool ready=false,requested=false,enrolling=false,connected=false,audio_ack=false,enroll_ack=false;
uint32_t generation=1,packets=0,reconnects=0,responses=0,asr_starts=0,asr_stops=0;
int64_t last_tick=0,last_speech=0;
std::string state="idle",error,enroll_name,enroll_id,enroll_phase="idle",speaker,band="none",text,emotion;
std::vector<std::string> speakers;
bool replace_speaker=false;
int enroll_n=0;
ResponsePolicy policy;
int pending_clip=0;int64_t pending_us=0;

void new_session_locked() {
    capture_session=0;++generation;
    connected=audio_ack=enroll_ack=false;pending_clip=0;last_tick=0;
    policy.seen=0; // Keep cooldown across reconnects.
    playback_set_hearing_session(requested&&!enrolling?generation:0);
}
bool wanted_locked(){return requested||enrolling;}
bool live(uint32_t session) {std::lock_guard<std::mutex> lock(mutex);return generation==session&&wanted_locked();}
bool listen(bool enable) {
    std::lock_guard<std::mutex> lock(mutex);
    if(enable && (!ready||url.empty()||secret.empty())) {error="configure_backend_first";return false;}
    if(enable&&enrolling){requested=true;return true;}
    if(enable&&requested)return true;
    requested=enable;
    if(!enable){enrolling=false;enroll_phase="cancelled";}
    new_session_locked();state=enable?"connecting":"idle";error.clear();
    quiet_until=esp_timer_get_time()+1200000;
    return true;
}
void finish_enroll_locked(const char* phase,const char* why="") {
    enrolling=false;enroll_phase=phase;error=why;
    new_session_locked();state=requested?"connecting":"idle";
}
void read_speakers(JsonVariantConst source) {
    if(!source.is<JsonArrayConst>())return;
    speakers.clear();for(auto value:source.as<JsonArrayConst>()) {
        if(value.is<const char*>()&&speakers.size()<64&&std::strlen(value.as<const char*>())<=144)speakers.emplace_back(value.as<const char*>());
    }
}
void receive(uint32_t session,const char* data,size_t len,bool binary) {
    if(binary||!len||len>8192)return;
    JsonDocument doc;
    if(deserializeJson(doc,data,len,DeserializationOption::NestingLimit(12))||!doc.is<JsonObject>())return;
    if(std::strcmp(doc["type"]|"","tick")||!doc["events"].is<JsonArray>())return;
    const int64_t now=esp_timer_get_time();
    std::lock_guard<std::mutex> lock(mutex);
    if(generation!=session||!wanted_locked())return;
    last_tick=now;
    for(auto event:doc["events"].as<JsonArrayConst>()) {
        const char* type=event["type"]|"";
        if(!std::strcmp(type,"hello"))read_speakers(event["speakers"]);
        else if(!std::strcmp(type,"audio")) {
            audio_ack=!std::strcmp(event["format"]|"","opus")&&!std::strcmp(event["framing"]|"","sea1")&&event["input_sample_rate"].as<int>()==24000;
            if(!audio_ack)error="backend_protocol_mismatch";
        } else if(!std::strcmp(type,"enroll")&&enrolling) {
            if(enroll_id!=(event["request_id"]|""))continue;
            const char* phase=event["phase"]|"";
            if(!std::strcmp(phase,"started")||!std::strcmp(phase,"progress")) {
                enroll_ack=true;enroll_phase=phase;enroll_n=std::clamp(event["n"]|0,0,5);
            } else if(!std::strcmp(phase,"done")) {
                enroll_n=5;read_speakers(event["speakers"]);finish_enroll_locked("done");return;
            } else if(!std::strcmp(phase,"error")||!std::strcmp(phase,"cancelled")) {
                finish_enroll_locked(phase,event["error"]|"enrollment_cancelled");return;
            }
        } else if(!std::strcmp(type,"error")) {
            error=std::string(event["error"]|"backend_error").substr(0,160);
            if(enrolling){const auto why=error;finish_enroll_locked("error",why.c_str());return;}
        } else if(!std::strcmp(type,"speech")&&!std::strcmp(event["phase"]|"","end")) {
            // No identity gate: known, unsure and unknown speakers all use the same response policy.
            band=std::string(event["speaker"]["band"]|"none").substr(0,16);
            speaker=band=="known"?std::string(event["speaker"]["name"]|"").substr(0,144):"";
            text=std::string(event["text"]|"").substr(0,768);emotion=std::string(event["emotion"]|"unknown").substr(0,24);last_speech=now;
            if(!requested||enrolling)continue;
            const char* id=event["id"]|"";unsigned seq=0;char extra;
            if(std::sscanf(id,"s-%u%c",&seq,&extra)!=1)continue;
            const auto end=event["pos"][1];
            const int64_t age_samples=doc["rx"].as<int64_t>()-end.as<int64_t>();
            const double processing=(event["debug"]["server_ms"]|100000.0)+(event["debug"]["tick_wait_ms"]|100000.0);
            const bool valid=end.is<int64_t>()&&age_samples>=0&&age_samples<=80000&&processing>=0&&processing<=5000&&
                !std::strcmp(event["event"]|"","speech")&&!std::strcmp(event["voice"]["band"]|"","voice")&&!text.empty();
            const bool self= !event["self"]["sounding"].is<bool>()||event["self"]["sounding"].as<bool>()||playback_self_sounding();
            const int clip=policy.consider(seq,emotion.c_str(),valid,self,valid?static_cast<int>(processing):-1,now,esp_random());
            if(clip&&!pending_clip){pending_clip=clip;pending_us=now;}
        }
    }
}
bool send_json(WebSocket& socket,JsonDocument& doc) {std::string value;serializeJson(doc,value);return socket.Send(value);}

void worker(void*) {
    EspNetwork network;
    unsigned retry=0;
    for(;;) {
        uint32_t session;std::string endpoint,key;bool enrollment;
        {
            std::lock_guard<std::mutex> lock(mutex);
            session=wanted_locked()?generation:0;endpoint=url;key=secret;enrollment=enrolling;
            if(session)state="connecting";
        }
        if(!session){retry=0;vTaskDelay(pdMS_TO_TICKS(50));continue;}
        xQueueReset(queue);
        auto socket=network.CreateWebSocket();
        socket->SetReceiveBufferSize(8192);
        const std::string authorization="Bearer "+key;
        socket->SetHeader("Authorization",authorization.c_str());
        socket->OnData([session](const char* data,size_t len,bool binary){receive(session,data,len,binary);});
        socket->OnDisconnected([session]{std::lock_guard<std::mutex> lock(mutex);if(generation==session){connected=false;capture_session=0;}});
        bool ok=socket->Connect(endpoint.c_str());
        void* encoder=nullptr;
        esp_opus_enc_config_t cfg{};
        cfg.sample_rate=24000;cfg.channel=1;cfg.bits_per_sample=16;cfg.bitrate=40000;
        cfg.frame_duration=ESP_OPUS_ENC_FRAME_DURATION_60_MS;cfg.application_mode=ESP_OPUS_ENC_APPLICATION_AUDIO;
        cfg.complexity=0;cfg.enable_vbr=true;
        if(ok&&live(session))ok=esp_opus_enc_open(&cfg,sizeof(cfg),&encoder)==ESP_AUDIO_ERR_OK;
        int input_size=0,output_size=0;
        if(ok&&encoder)ok=esp_opus_enc_get_frame_size(encoder,&input_size,&output_size)==ESP_AUDIO_ERR_OK&&input_size==2880&&output_size>0&&output_size<=4096;
        if(ok&&live(session)) {
            JsonDocument hello;hello["type"]="audio";hello["format"]="opus";hello["sample_rate"]=24000;
            hello["channels"]=1;hello["frame_ms"]=60;hello["framing"]="sea1";hello["source"]="stackchan-"+device;
            hello["device"]["device_id"]=device;hello["device"]["boot_id"]=boot;
            hello["device"]["firmware"]=FIRMWARE_VERSION;hello["device"]["protocol"]="soul-ear/1";
            hello["device"]["channel_roles"].to<JsonArray>().add("mic");
            ok=send_json(*socket,hello);
        }
        const int64_t handshake_deadline=esp_timer_get_time()+5000000;
        bool enrollment_sent=false;
        while(ok&&live(session)&&socket->IsConnected()) {
            bool activated=false,ack=false;
            JsonDocument enrollment_msg;
            {
                std::lock_guard<std::mutex> lock(mutex);
                ack=audio_ack;
                if(ack&&enrolling&&!enrollment_sent) {
                    enrollment_msg["type"]="enroll";enrollment_msg["name"]=enroll_name;
                    enrollment_msg["request_id"]=enroll_id;enrollment_msg["count"]=5;enrollment_msg["replace"]=replace_speaker;
                }
                activated=ack&&(!enrolling||enroll_ack);
                if(activated&&generation==session){connected=true;state=enrolling?"enrolling":"listening";error.clear();capture_session=session;}
            }
            if(activated)break;
            if(!enrollment_msg.isNull()){ok=send_json(*socket,enrollment_msg);enrollment_sent=true;}
            if(esp_timer_get_time()>handshake_deadline){ok=false;break;}
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        if(ok&&live(session))retry=0;
        std::vector<uint8_t> encoded(std::max(0,output_size)+16);
        std::array<int16_t,1440> pcm{};
        unsigned blocks=0;uint32_t previous=0,wire_seq=0;bool sent=false,sounding=false;
        int64_t first_us=0;
        while(ok&&live(session)&&socket->IsConnected()) {
            int clip=0;
            {
                std::lock_guard<std::mutex> lock(mutex);
                if(last_tick&&esp_timer_get_time()-last_tick>3000000)ok=false;
                if(pending_clip&&esp_timer_get_time()-pending_us<500000)clip=pending_clip;
                pending_clip=0;
            }
            if(clip&&playback_hearing(clip,session)==ESP_OK) {
                std::lock_guard<std::mutex> lock(mutex);
                if(generation==session){policy.accepted(esp_timer_get_time());++responses;}
            }
            Block block;
            if(!ok||xQueueReceive(queue,&block,pdMS_TO_TICKS(20))!=pdTRUE)continue;
            if(block.session!=session||esp_timer_get_time()-block.us>500000){++dropped;blocks=0;if(sent)++wire_seq;continue;}
            if(previous&&block.seq!=previous+1){blocks=0;++dropped;if(sent)++wire_seq;}
            if(!blocks){first_us=block.us;sounding=false;}
            std::copy_n(block.pcm,kFrames,pcm.data()+blocks*kFrames);sounding|=block.sounding;previous=block.seq;
            if(++blocks!=3)continue;
            blocks=0;
            esp_audio_enc_in_frame_t in{reinterpret_cast<uint8_t*>(pcm.data()),2880};
            esp_audio_enc_out_frame_t out{};out.buffer=encoded.data()+16;out.len=output_size;
            if(esp_opus_enc_process(encoder,&in,&out)!=ESP_AUDIO_ERR_OK||!out.encoded_bytes||out.encoded_bytes>static_cast<unsigned>(output_size)){ok=false;break;}
            if(!live(session))break;
            JsonDocument flags;flags["type"]="self";flags["sounding"]=sounding;flags["moving"]=false;flags["t_us"]=first_us;
            ok=send_json(*socket,flags);
            std::memcpy(encoded.data(),"SEA1",4);
            for(unsigned i=0;i<4;++i)encoded[4+i]=(wire_seq>>(i*8))&255;
            for(unsigned i=0;i<8;++i)encoded[8+i]=(static_cast<uint64_t>(first_us)>>(i*8))&255;
            if(ok&&live(session))ok=socket->Send(encoded.data(),out.encoded_bytes+16,true);
            if(ok){sent=true;++wire_seq;std::lock_guard<std::mutex> lock(mutex);++packets;}
        }
        capture_session=0;
        if(encoder)esp_opus_enc_close(encoder);
        socket.reset(); // Join transport callback task without holding our state mutex.
        uint32_t wait_generation=0;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if(generation==session) {
                if(enrollment)finish_enroll_locked("error","backend_disconnected");
                else {new_session_locked();state=requested?"retrying":"idle";error="backend_disconnected";++reconnects;wait_generation=generation;}
            }
        }
        if(wait_generation) {
            const int64_t until=esp_timer_get_time()+std::min(30u,1u<<std::min(retry++,5u))*1000000LL;
            while(esp_timer_get_time()<until&&live(wait_generation))vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
}

esp_err_t respond(httpd_req_t* request,const char* status,const char* message) {
    JsonDocument doc;doc["accepted"]=!std::strcmp(status,"202 Accepted");doc["message"]=message;
    std::string body;serializeJson(doc,body);httpd_resp_set_status(request,status);httpd_resp_set_type(request,"application/json");
    httpd_resp_set_hdr(request,"Cache-Control","no-store");return httpd_resp_send(request,body.data(),body.size());
}
}

void init(const std::string& device_id,const std::string& boot_id) {
    device=device_id;boot=boot_id;
    nvs_handle_t nvs;
    if(nvs_open("collector",NVS_READONLY,&nvs)==ESP_OK) {
        char config[768];size_t size=sizeof(config);
        if(nvs_get_str(nvs,"ear_config",config,&size)==ESP_OK) {
            JsonDocument doc;if(!deserializeJson(doc,config)){
                std::string endpoint=doc["url"]|"",key=doc["token"]|"";
                if(valid_url(endpoint)&&valid_token(key)){url=endpoint;secret=key;}
            }
        }
        nvs_close(nvs);
    }
    auto* storage=static_cast<uint8_t*>(heap_caps_malloc(kQueue*sizeof(Block),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(storage)queue=xQueueCreateStatic(kQueue,sizeof(Block),storage,&queue_control);
    if(queue&&xTaskCreate(worker,"collector_ear",12288,nullptr,2,nullptr)==pdPASS)ready=true;
    else error="hearing_memory";
}

void feed_audio(const int16_t* data,size_t frames,uint32_t seq,int64_t timestamp_us) {
    const uint32_t session=capture_session.load();if(!session||!queue||frames!=kFrames)return;
    Block block{};block.session=session;block.seq=seq;block.us=timestamp_us;
    block.sounding=playback_self_sounding()||timestamp_us<quiet_until.load();
    for(size_t i=0;i<frames;++i)block.pcm[i]=data[i*2];
    if(xQueueSend(queue,&block,0)!=pdTRUE) {
        Block stale;xQueueReceive(queue,&stale,0);++dropped;xQueueSend(queue,&block,0);
    }
}
void asr_command(uint8_t id,bool has_value) {
    const int action=asr_action(id,has_value);if(!action)return;
    {std::lock_guard<std::mutex> lock(mutex);if(action>0)++asr_starts;else ++asr_stops;}
    listen(action>0);
}
void json(JsonObject out) {
    std::lock_guard<std::mutex> lock(mutex);
    out["supported"]=true;out["ready"]=ready;out["url"]=url;out["token_configured"]=!secret.empty();
    out["requested"]=requested;out["connected"]=connected;out["state"]=state;out["error"]=error;
    out["session"]=generation;out["packets"]=packets;out["dropped_blocks"]=dropped.load();out["reconnects"]=reconnects;out["responses"]=responses;
    out["asr_starts"]=asr_starts;out["asr_stops"]=asr_stops;out["speaker"]=speaker;out["speaker_band"]=band;
    out["text"]=text;out["emotion"]=emotion;out["emotion_reliable"]=false;out["respond_to"]="everyone";
    out["last_speech_age_ms"]=last_speech?(esp_timer_get_time()-last_speech)/1000:-1;
    auto list=out["speakers"].to<JsonArray>();for(const auto& name:speakers)list.add(name);
    auto enrollment=out["enrollment"].to<JsonObject>();enrollment["active"]=enrolling;enrollment["name"]=enroll_name;
    enrollment["phase"]=enroll_phase;enrollment["n"]=enroll_n;enrollment["count"]=5;
}

esp_err_t handle(httpd_req_t* request) {
    if(request->content_len<1||request->content_len>768){respond(request,"400 Bad Request","invalid_body");return ESP_FAIL;}
    char buffer[769]{};int received=0;const int64_t deadline=esp_timer_get_time()+3000000;
    while(received<request->content_len) {
        int n=httpd_req_recv(request,buffer+received,request->content_len-received);
        if(n<=0||esp_timer_get_time()>deadline){respond(request,"400 Bad Request","incomplete_body");return ESP_FAIL;}received+=n;
    }
    JsonDocument doc;
    if(deserializeJson(doc,buffer,received)||!doc.is<JsonObject>())return respond(request,"400 Bad Request","expected_object");
    const std::string op=doc["op"]|"";
    if(op=="listen"&&doc.size()==2&&doc["enabled"].is<bool>())
        return listen(doc["enabled"])?respond(request,"202 Accepted","listen_updated"):respond(request,"409 Conflict","configure_backend_first");
    std::lock_guard<std::mutex> lock(mutex);
    if(op=="config"&&doc.size()==3&&doc["url"].is<const char*>()&&doc["token"].is<const char*>()) {
        const std::string endpoint=doc["url"].as<std::string>(),key=doc["token"].as<std::string>().empty()?secret:doc["token"].as<std::string>();
        if(!valid_url(endpoint)||!valid_token(key))return respond(request,"400 Bad Request","invalid_url_or_token");
        JsonDocument config;config["url"]=endpoint;config["token"]=key;std::string value;serializeJson(config,value);
        nvs_handle_t nvs;esp_err_t err=nvs_open("collector",NVS_READWRITE,&nvs);
        if(err==ESP_OK){err=nvs_set_str(nvs,"ear_config",value.c_str());if(err==ESP_OK)err=nvs_commit(nvs);nvs_close(nvs);}
        if(err!=ESP_OK)return respond(request,"503 Service Unavailable","config_save_failed");
        requested=enrolling=false;new_session_locked();url=endpoint;secret=key;state="idle";error.clear();enroll_phase="idle";
        return respond(request,"202 Accepted","configuration_saved");
    }
    if(op=="enroll"&&doc.size()==3&&doc["name"].is<const char*>()&&doc["replace"].is<bool>()) {
        const std::string name=doc["name"].as<std::string>();
        if(name.empty()||name.size()>144||name.find_first_of("/\\\r\n\t")!=std::string::npos)return respond(request,"400 Bad Request","invalid_name");
        if(!ready||url.empty()||secret.empty()||enrolling)return respond(request,"409 Conflict","not_ready_or_enrollment_active");
        enrolling=true;enroll_name=name;replace_speaker=doc["replace"];enroll_n=0;enroll_phase="connecting";
        new_session_locked();enroll_id=boot+"-"+std::to_string(generation);state="connecting";error.clear();quiet_until=esp_timer_get_time()+1200000;
        return respond(request,"202 Accepted","enrollment_requested");
    }
    if(op=="cancel"&&doc.size()==1) {if(enrolling)finish_enroll_locked("cancelled");return respond(request,"202 Accepted","enrollment_cancelled");}
    return respond(request,"400 Bad Request","invalid_hearing_command");
}
}
