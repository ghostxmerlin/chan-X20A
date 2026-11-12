/* SPDX-License-Identifier: MIT */
#include "eye_mode.h"
#include "eyes/eyes.h"
#include "hearing.h"
#include "hearing_policy.h"
#include "playback.h"
#include "voice_library.h"
#include "voice_policy.h"
#include "hearing_socket.h"
#include "hearing_transport_policy.h"
#include <esp_opus_enc.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_random.h>
#include <esp_log.h>
#include <nvs.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <algorithm>
#include <cmath>
#include <string>
#include <array>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <vector>

namespace collector::hearing {
using namespace ArduinoJson;
namespace {
// Capture queue in PSRAM: 150 x 20 ms = 3 s, so a send that waits up to 1 s loses no audio.
constexpr size_t kFrames=480,kQueue=150;
constexpr int64_t kMaxBlockAgeUs=3000000;
struct Block {uint32_t session,seq;int64_t us;bool sounding;int16_t pcm[kFrames];};
std::mutex mutex;
std::shared_ptr<HearingSocket> active_socket;
QueueHandle_t queue=nullptr;
StaticQueue_t queue_control;
constexpr size_t kWorkerStackBytes=32768;
StaticTask_t worker_control;
StackType_t* worker_stack=nullptr;
std::atomic<uint32_t> worker_stack_free{0},encode_us{0},send_us{0},poll_us{0};
std::atomic<uint32_t> capture_session{0},dropped{0};
std::atomic<int64_t> quiet_until{0};
std::string url,secret,device,boot;
bool ready=false,connected=false,audio_ack=false,enroll_ack=false;
ListeningControl control;
uint32_t packets=0,reconnects=0,responses=0,asr_starts=0,asr_stops=0;
uint32_t responses_muted=0;   // speech events with respond=false (嘴 in TTS or off mode)
int64_t last_tick=0,last_speech=0;
std::string state="idle",error,enroll_name,enroll_id,enroll_phase="idle",speaker,band="none",text,emotion;
std::vector<std::string> speakers;
bool replace_speaker=false;
int enroll_n=0;
// Voiceprint deletion (2026-11-10): pending name sent on the live session; result for telemetry.
std::string forget_pending,forget_name,forget_state="idle",forget_error;
std::string enroll_rejected; // why the last sentence did not count (server reason code), "" if it did
ResponsePolicy policy;
QuietWait quiet;              // chosen response waiting for a quiet room (hearing_policy.h)
bool server_speaking=false;int64_t last_speaking_us=0; // backend VAD state from every tick
// Heartbeat: the backend sends a tick every 100 ms; 3 s without one ends the session.
constexpr int64_t kTickTimeoutUs=3000000;
uint32_t ticks=0,tick_gap_max_ms=0,tick_gap_max_boot_ms=0,send_max_us=0,session_dropped_base=0;
int64_t session_started=0;
bool server_level_valid=false;float server_level_db=0,server_noise_db=0;int server_rx_gap_ms=-1;
// Why sessions ended (each counted once per session; "stopped" is a requested stop).
struct EndCounts {uint32_t heartbeat_timeout=0,send_failed=0,transport_closed=0,connect_failed=0,handshake_timeout=0,encode_failed=0,stopped=0;} ends;
std::string last_end_reason,last_end_error;int64_t last_end_us=0;uint32_t last_end_duration_ms=0,last_end_dropped=0;
// Recent hearing events for the monitor's combined log (telemetry cursor hearing_after).
constexpr size_t kEvents=32;
struct HearingEvent {uint32_t seq=0;int64_t us=0;const char* kind="";std::string label,detail;int p=-1;};
std::array<HearingEvent,kEvents> events;uint32_t event_seq=0;
// At most `bytes` bytes without cutting a UTF-8 character (a cut one made telemetry JSON invalid).
std::string utf8_prefix(const std::string& text,size_t bytes) {
    if(text.size()<=bytes)return text;
    size_t n=bytes;
    while(n>0&&(static_cast<unsigned char>(text[n])&0xC0)==0x80)--n;
    return text.substr(0,n);
}
void record_locked(const char* kind,std::string label,std::string detail={},int p=-1) {
    auto& e=events[++event_seq%kEvents];
    e.seq=event_seq;e.us=esp_timer_get_time();e.kind=kind;e.label=std::move(label);e.detail=std::move(detail);e.p=p;
}

void new_session_locked() {
    capture_session=0;control.next_session();
    if(active_socket)active_socket->Abort();
    connected=audio_ack=enroll_ack=false;quiet.clear();server_speaking=false;last_speaking_us=0;last_tick=0;ticks=0;tick_gap_max_ms=0;send_max_us=0;
    server_level_valid=false;server_rx_gap_ms=-1;
    policy.seen=0; // Keep cooldown across reconnects.
    playback_set_hearing_session(control.requested()&&!control.enrolling()?control.generation():0);
}
bool wanted_locked(){return control.requested()||control.enrolling();}
bool live(uint32_t session) {std::lock_guard<std::mutex> lock(mutex);return control.generation()==session&&wanted_locked();}
bool configured_locked(){return ready&&!url.empty()&&!secret.empty();}
void apply_listening_locked(bool enable) {
    if(!enable)enroll_phase="cancelled";
    new_session_locked();state=enable?"connecting":"idle";error.clear();
    quiet_until=esp_timer_get_time()+1200000;
}
bool explicit_listening_locked(bool enable,bool toggle=false) {
    const auto before=control.snapshot();
    const bool accepted=toggle?control.toggle_listening(configured_locked()):control.set_listening(enable,configured_locked());
    if(!accepted){error="configure_backend_first";return false;}
    if(!(enable&&(before.requested||before.enrolling)))apply_listening_locked(enable);
    return true;
}
void show_emotion_eyes(const char* emotion) {
    const auto state=eye_state_for_emotion(emotion);
    if(!eyes::valid(state))return;
    eyes::Command c;c.op=eyes::Operation::Play;c.state=state;c.mode=eyes::Mode::Once;
    eye_request_id(c.request_id,[]{return esp_random();});
    eyes::submit(c,eyes::Source::Hearing);
}
void finish_enroll_locked(const char* phase,const char* why="") {
    control.finish_enrollment();enroll_phase=phase;error=why;
    new_session_locked();state=control.requested()?"connecting":"idle";
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
    if(control.generation()!=session||!wanted_locked())return;
    if(last_tick) {
        const uint32_t gap=static_cast<uint32_t>((now-last_tick)/1000);
        tick_gap_max_ms=std::max(tick_gap_max_ms,gap);tick_gap_max_boot_ms=std::max(tick_gap_max_boot_ms,gap);
    }
    last_tick=now;++ticks;
    if(doc["level_db"].is<float>()&&doc["noise_db"].is<float>()) {
        server_level_valid=true;server_level_db=doc["level_db"];server_noise_db=doc["noise_db"];
    }
    if(doc["rx_gap_ms"].is<int>())server_rx_gap_ms=doc["rx_gap_ms"];
    if(doc["speaking"].is<bool>()){server_speaking=doc["speaking"];if(server_speaking)last_speaking_us=now;}
    for(auto event:doc["events"].as<JsonArrayConst>()) {
        const char* type=event["type"]|"";
        if(!std::strcmp(type,"hello"))read_speakers(event["speakers"]);
        else if(!std::strcmp(type,"speakers")&&!std::strcmp(event["op"]|"","forget")) {
            read_speakers(event["speakers"]);
            if(forget_name==(event["name"]|"")){forget_state=(event["ok"]|false)?"done":"error";forget_error=event["error"]|"";}
            const bool ok=event["ok"]|false;
            record_locked("link",std::string(ok?"voiceprint deleted: ":"voiceprint delete failed: ")+utf8_prefix(forget_name,48));
        }
        else if(!std::strcmp(type,"audio")) {
            audio_ack=!std::strcmp(event["format"]|"","opus")&&!std::strcmp(event["framing"]|"","sea1")&&event["input_sample_rate"].as<int>()==24000;
            if(!audio_ack)error="backend_protocol_mismatch";
        } else if(!std::strcmp(type,"enroll")&&control.enrolling()) {
            if(enroll_id!=(event["request_id"]|""))continue;
            const char* phase=event["phase"]|"";
            if(!std::strcmp(phase,"started")||!std::strcmp(phase,"progress")) {
                enroll_ack=true;enroll_phase=phase;enroll_n=std::clamp(event["n"]|0,0,5);
                if(!std::strcmp(phase,"started"))enroll_rejected.clear();
                else enroll_rejected=event["rejected"]|"";
            } else if(!std::strcmp(phase,"done")) {
                enroll_n=5;read_speakers(event["speakers"]);finish_enroll_locked("done");return;
            } else if(!std::strcmp(phase,"error")||!std::strcmp(phase,"cancelled")) {
                finish_enroll_locked(phase,event["error"]|"enrollment_cancelled");return;
            }
        } else if(!std::strcmp(type,"tingwu")) {
            // 听悟 realtime (server, 2026-11-10): shown in the monitor's combined log under its own tag.
            const char* phase=event["phase"]|"";
            if(!std::strcmp(phase,"sentence"))
                record_locked("tingwu",utf8_prefix(event["text"]|"",240),(event["self"]["sounding"]|false)?"self":"");
            else if(!std::strcmp(phase,"error"))
                record_locked("tingwu","",std::string("error: ")+utf8_prefix(event["error"]|"",120));
        } else if(!std::strcmp(type,"audio_gap")) {
            record_locked("gap","",std::to_string(event["missing_packets"]|0));
        } else if(!std::strcmp(type,"sound")&&!std::strcmp(event["phase"]|"","start")) {
            record_locked("sound",utf8_prefix(event["label"]|"",48),std::string(event["kind"]|"").substr(0,24),
                          static_cast<int>(std::lround((event["p"]|0.0f)*100)));
        } else if(!std::strcmp(type,"error")) {
            error=std::string(event["error"]|"backend_error").substr(0,160);
            record_locked("error",error);
            if(control.enrolling()){const auto why=error;finish_enroll_locked("error",why.c_str());return;}
        } else if(!std::strcmp(type,"speech")&&!std::strcmp(event["phase"]|"","end")) {
            // No identity gate: known, unsure and unknown speakers all use the same response policy.
            band=std::string(event["speaker"]["band"]|"none").substr(0,16);
            speaker=band=="known"?utf8_prefix(event["speaker"]["name"]|"",144):"";
            text=utf8_prefix(event["text"]|"",768);emotion=std::string(event["emotion"]|"unknown").substr(0,24);last_speech=now;
            record_locked("speech",utf8_prefix(text,160),emotion+"/"+band+(speaker.empty()?"":"/"+utf8_prefix(speaker,48)),
                          static_cast<int>(std::lround((event["voice"]["p"]|-0.01f)*100)));
            if(!control.requested()||control.enrolling())continue;
            const char* id=event["id"]|"";unsigned seq=0;char extra;
            if(std::sscanf(id,"s-%u%c",&seq,&extra)!=1)continue;
            const auto end=event["pos"][1];
            const int64_t age_samples=doc["rx"].as<int64_t>()-end.as<int64_t>();
            const double processing=(event["debug"]["server_ms"]|100000.0)+(event["debug"]["tick_wait_ms"]|100000.0);
            const bool valid=end.is<int64_t>()&&age_samples>=0&&age_samples<=80000&&processing>=0&&processing<=5000&&
                !std::strcmp(event["event"]|"","speech")&&!std::strcmp(event["voice"]["acoustic_band"]|"","voice")&&!text.empty();
            const bool self= !event["self"]["sounding"].is<bool>()||event["self"]["sounding"].as<bool>()||playback_self_sounding();
            if(quiet.pending())continue; // one response at a time; this utterance is not considered
            // 嘴 mode (0.4.77): in TTS or off mode the ear sends respond=false and no clip is chosen.
            const bool respond=response_allowed(event["respond"].is<bool>(),event["respond"]|true);
            if(!respond)++responses_muted;
            const int clip=!respond?0:policy.consider(seq,emotion.c_str(),valid,self,valid?static_cast<int>(processing):-1,now,esp_random(),
                kVoiceCount,[](int clip_id){return voice_emotion(clip_id);});
            if(clip)quiet.hold(clip,now,utf8_prefix(text,120),emotion);
            // Normal eye mode: no response for this utterance → show its emotion once (vision on only;
            // with the eyes disabled the request is refused and the home page stays).
            else if(!self&&!text.empty())show_emotion_eyes(emotion.c_str());
        }
    }
}
bool send_json(HearingSocket& socket,JsonDocument& doc) {std::string value;serializeJson(doc,value);return socket.Send(value);}

void worker(void*) {
    unsigned retry=0;
    for(;;) {
        uint32_t session;std::string endpoint,key;bool enrollment;
        {
            std::lock_guard<std::mutex> lock(mutex);
            session=wanted_locked()?control.generation():0;endpoint=url;key=secret;enrollment=control.enrolling();
            if(session)state="connecting";
        }
        if(!session){retry=0;vTaskDelay(pdMS_TO_TICKS(50));continue;}
        xQueueReset(queue);
        auto socket=std::make_shared<HearingSocket>();
        {std::lock_guard<std::mutex> lock(mutex);if(control.generation()==session)active_socket=socket;else socket->Abort();}
        const std::string authorization="Bearer "+key;
        socket->SetHeader("Authorization",authorization.c_str());
        socket->OnData([session](const char* data,size_t len,bool binary){receive(session,data,len,binary);});
        socket->OnDisconnected([session]{std::lock_guard<std::mutex> lock(mutex);if(control.generation()==session){connected=false;capture_session=0;}});
        bool ok=socket->Connect(endpoint.c_str());
        const char* end_reason=ok?nullptr:"connect_failed";
        void* encoder=nullptr;
        esp_opus_enc_config_t cfg{};
        cfg.sample_rate=24000;cfg.channel=1;cfg.bits_per_sample=16;cfg.bitrate=40000;
        cfg.frame_duration=ESP_OPUS_ENC_FRAME_DURATION_60_MS;cfg.application_mode=ESP_OPUS_ENC_APPLICATION_AUDIO;
        cfg.complexity=0;cfg.enable_vbr=true;
        if(ok&&live(session))ok=esp_opus_enc_open(&cfg,sizeof(cfg),&encoder)==ESP_AUDIO_ERR_OK;
        if(!ok&&!end_reason)end_reason="encode_failed";
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
            socket->Poll();
            bool activated=false,ack=false;
            JsonDocument enrollment_msg;
            {
                std::lock_guard<std::mutex> lock(mutex);
                ack=audio_ack;
                if(ack&&control.enrolling()&&!enrollment_sent) {
                    enrollment_msg["type"]="enroll";enrollment_msg["name"]=enroll_name;
                    enrollment_msg["request_id"]=enroll_id;enrollment_msg["count"]=5;enrollment_msg["replace"]=replace_speaker;
                }
                activated=ack&&(!control.enrolling()||enroll_ack);
                if(activated&&control.generation()==session){
                    connected=true;state=control.enrolling()?"enrolling":"listening";error.clear();capture_session=session;
                    session_started=esp_timer_get_time();session_dropped_base=dropped.load();record_locked("link","connected");
                }
            }
            if(activated)break;
            if(!enrollment_msg.isNull()){ok=send_json(*socket,enrollment_msg);enrollment_sent=true;}
            if(esp_timer_get_time()>handshake_deadline){ok=false;end_reason="handshake_timeout";break;}
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        {std::lock_guard<std::mutex> lock(mutex);if(ok&&control.generation()==session&&audio_ack)retry=0;}
        std::vector<uint8_t> encoded(std::max(0,output_size)+16);
        std::array<int16_t,1440> pcm{};
        unsigned blocks=0;uint32_t previous=0,wire_seq=0;bool sent=false,sounding=false;
        int64_t first_us=0;
        SelfFlagGate self_gate;
        while(ok&&live(session)&&socket->IsConnected()) {
            const auto poll_started=esp_timer_get_time();socket->Poll();poll_us=esp_timer_get_time()-poll_started;
            int clip=0,waited=0;std::string heard,felt;
            {
                std::lock_guard<std::mutex> lock(mutex);
                const int64_t now=esp_timer_get_time();
                if(last_tick&&now-last_tick>kTickTimeoutUs){ok=false;end_reason="heartbeat_timeout";}
                // Speak only into a quiet room: someone talking (backend VAD) or our own playback waits.
                const int held=quiet.clip;
                const auto r=quiet.poll(now,server_speaking||playback_self_sounding(),last_speaking_us);
                if(r==QuietWait::Play){clip=quiet.clip;heard=quiet.heard;felt=quiet.felt;waited=quiet.waited_ms(now);quiet.clear();}
                else if(r==QuietWait::Expired)record_locked("response",quiet.heard,quiet.felt+"/"+std::to_string(held)+"/talking/"+std::to_string(quiet.waited_ms(now)));
            }
            if(clip) {
                const esp_err_t played=playback_hearing(clip,session);
                std::lock_guard<std::mutex> lock(mutex);
                if(played==ESP_OK&&control.generation()==session){policy.accepted(esp_timer_get_time());++responses;}
                record_locked("response",heard,felt+"/"+std::to_string(clip)+"/"+(played==ESP_OK?"":esp_err_to_name(played))+"/"+std::to_string(waited));
            }
            Block block;
            if(!ok||xQueueReceive(queue,&block,pdMS_TO_TICKS(20))!=pdTRUE)continue;
            if(block.session!=session||esp_timer_get_time()-block.us>kMaxBlockAgeUs){++dropped;blocks=0;if(sent)++wire_seq;continue;}
            if(previous&&block.seq!=previous+1){blocks=0;++dropped;if(sent)++wire_seq;}
            if(!blocks){first_us=block.us;sounding=false;}
            std::copy_n(block.pcm,kFrames,pcm.data()+blocks*kFrames);sounding|=block.sounding;previous=block.seq;
            if(++blocks!=3)continue;
            blocks=0;
            esp_audio_enc_in_frame_t in{reinterpret_cast<uint8_t*>(pcm.data()),2880};
            esp_audio_enc_out_frame_t out{};out.buffer=encoded.data()+16;out.len=output_size;
            const auto encode_started=esp_timer_get_time();
            const auto encoded_result=esp_opus_enc_process(encoder,&in,&out);encode_us=esp_timer_get_time()-encode_started;
            if(encoded_result!=ESP_AUDIO_ERR_OK||!out.encoded_bytes||out.encoded_bytes>static_cast<unsigned>(output_size)){ok=false;end_reason="encode_failed";break;}
            if(!live(session))break;
            const auto send_started=esp_timer_get_time();
            if(self_gate.should_send(sounding)) {
                JsonDocument flags;flags["type"]="self";flags["sounding"]=sounding;flags["moving"]=false;flags["t_us"]=first_us;
                ok=send_json(*socket,flags);
            }
            {
                std::string forget;
                {std::lock_guard<std::mutex> lock(mutex);forget.swap(forget_pending);}
                if(ok&&!forget.empty()){JsonDocument msg;msg["type"]="forget";msg["name"]=forget;ok=send_json(*socket,msg);}
            }
            std::memcpy(encoded.data(),"SEA1",4);
            for(unsigned i=0;i<4;++i)encoded[4+i]=(wire_seq>>(i*8))&255;
            for(unsigned i=0;i<8;++i)encoded[8+i]=(static_cast<uint64_t>(first_us)>>(i*8))&255;
            if(ok&&live(session))ok=socket->Send(encoded.data(),out.encoded_bytes+16,true);
            send_us=esp_timer_get_time()-send_started;
            if(!ok&&!end_reason&&live(session))end_reason="send_failed";
            {std::lock_guard<std::mutex> lock(mutex);send_max_us=std::max(send_max_us,send_us.load());}
            if(ok){sent=true;++wire_seq;worker_stack_free=uxTaskGetStackHighWaterMark(nullptr);std::lock_guard<std::mutex> lock(mutex);++packets;}
        }
        capture_session=0;
        const auto transport_error=socket->Error();
        if(!end_reason)end_reason=!live(session)?"stopped":!socket->IsConnected()?"transport_closed":"send_failed";
        ESP_LOGI("Hearing","session ended error=%s stack_free=%u",transport_error.c_str(),unsigned(uxTaskGetStackHighWaterMark(nullptr)));
        if(encoder)esp_opus_enc_close(encoder);
        {std::lock_guard<std::mutex> lock(mutex);if(active_socket==socket)active_socket.reset();}
        socket.reset(); // Synchronous transport: every callback has already returned.
        uint32_t wait_generation=0;
        {
            std::lock_guard<std::mutex> lock(mutex);
            const std::string reason=end_reason;
            if(reason=="heartbeat_timeout")++ends.heartbeat_timeout;else if(reason=="send_failed")++ends.send_failed;
            else if(reason=="transport_closed")++ends.transport_closed;else if(reason=="connect_failed")++ends.connect_failed;
            else if(reason=="handshake_timeout")++ends.handshake_timeout;else if(reason=="encode_failed")++ends.encode_failed;else ++ends.stopped;
            const int64_t ended=esp_timer_get_time();
            last_end_reason=reason;last_end_error=transport_error.substr(0,96);last_end_us=ended;
            last_end_duration_ms=session_started?static_cast<uint32_t>((ended-session_started)/1000):0;
            last_end_dropped=dropped.load()-session_dropped_base;session_started=0;
            record_locked("link",reason,transport_error.substr(0,96));
            if(control.generation()==session) {
                if(enrollment)finish_enroll_locked("error","backend_disconnected");
                else {new_session_locked();state=control.requested()?"retrying":"idle";error="backend_disconnected:"+transport_error;++reconnects;wait_generation=control.generation();}
            }
        }
        if(wait_generation) {
            const int64_t until=esp_timer_get_time()+std::min(30u,1u<<std::min(retry++,5u))*1000000LL;
            while(esp_timer_get_time()<until&&live(wait_generation))vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
}

esp_err_t respond(ControlReply* request,const char* status,const char* message) {
    JsonDocument doc;doc["accepted"]=!std::strcmp(status,"202 Accepted");doc["message"]=message;
    std::string body;serializeJson(doc,body);httpd_resp_set_status(request,status);httpd_resp_set_type(request,"application/json");
    httpd_resp_set_hdr(request,"Cache-Control","no-store");return httpd_resp_send(request,body.data(),body.size());
}
}

bool backend(std::string& endpoint,std::string& token){std::lock_guard<std::mutex> lock(mutex);if(!configured_locked())return false;endpoint=url;token=secret;return true;}
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
    // Match the existing Opus path (24–28 KiB) plus our frame/network buffers.
    // This worker never writes flash; external task stacks are enabled already.
    worker_stack=static_cast<StackType_t*>(heap_caps_malloc(kWorkerStackBytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    // Keep encoding ahead of display/JPEG work while leaving the sampler (7) first.
    // Opus must share core 1 with JPEG (priority 2), not crowd out the
    // core-0 LVGL task alongside Wi-Fi/TCP. Capture (7) still takes priority.
    if(queue&&worker_stack&&xTaskCreateStaticPinnedToCore(worker,"collector_ear",kWorkerStackBytes,nullptr,4,worker_stack,&worker_control,1))ready=true;
    else {
        if(queue)vQueueDelete(queue);
        queue=nullptr;heap_caps_free(storage);heap_caps_free(worker_stack);worker_stack=nullptr;
        error="hearing_memory";
    }
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
ListeningIntent listening_intent() {
    std::lock_guard<std::mutex> lock(mutex);
    return control.snapshot();
}
bool set_listening(bool enabled) {
    std::lock_guard<std::mutex> lock(mutex);
    return explicit_listening_locked(enabled);
}
bool toggle_listening() {
    std::lock_guard<std::mutex> lock(mutex);
    return explicit_listening_locked(!(control.requested()||control.enrolling()),true);
}
OwnerStart start_for_owner(uint32_t expected_revision,uint32_t& owner_revision) {
    std::lock_guard<std::mutex> lock(mutex);
    const auto result=control.start_for_owner(expected_revision,configured_locked(),owner_revision);
    if(result==OwnerStart::Started)apply_listening_locked(true);
    return result;
}
void stop_for_owner(uint32_t owner_revision) {
    std::lock_guard<std::mutex> lock(mutex);
    if(control.stop_for_owner(owner_revision))apply_listening_locked(false);
}
void asr_command(uint8_t id,bool has_value) {
    const int action=asr_action(id,has_value);if(!action)return;
    {std::lock_guard<std::mutex> lock(mutex);if(action>0)++asr_starts;else ++asr_stops;}
    set_listening(action>0);
}
void json(JsonObject out,bool with_events,uint32_t events_after) {
    std::lock_guard<std::mutex> lock(mutex);
    const int64_t now=esp_timer_get_time();
    auto hb=out["heartbeat"].to<JsonObject>();
    hb["interval_ms"]=100;hb["timeout_ms"]=kTickTimeoutUs/1000;
    hb["last_age_ms"]=last_tick?static_cast<int32_t>((now-last_tick)/1000):-1;
    hb["ticks"]=ticks;hb["max_gap_ms"]=tick_gap_max_ms;hb["max_gap_boot_ms"]=tick_gap_max_boot_ms;
    if(server_level_valid){hb["server_level_db"]=server_level_db;hb["server_noise_db"]=server_noise_db;}
    else {hb["server_level_db"]=nullptr;hb["server_noise_db"]=nullptr;}
    hb["server_rx_gap_ms"]=server_rx_gap_ms;
    out["session_age_ms"]=session_started?static_cast<int32_t>((now-session_started)/1000):-1;
    out["session_dropped_blocks"]=session_started?dropped.load()-session_dropped_base:0;
    out["send_max_us"]=send_max_us;
    auto end=out["disconnects"].to<JsonObject>();
    end["heartbeat_timeout"]=ends.heartbeat_timeout;end["send_failed"]=ends.send_failed;end["transport_closed"]=ends.transport_closed;
    end["connect_failed"]=ends.connect_failed;end["handshake_timeout"]=ends.handshake_timeout;end["encode_failed"]=ends.encode_failed;end["stopped"]=ends.stopped;
    auto last=out["last_end"].to<JsonObject>();
    last["reason"]=last_end_reason;last["error"]=last_end_error;last["duration_ms"]=last_end_duration_ms;
    last["age_ms"]=last_end_us?static_cast<int32_t>((now-last_end_us)/1000):-1;last["dropped_blocks"]=last_end_dropped;
    out["event_seq"]=event_seq;
    auto reply=out["response"].to<JsonObject>();
    reply["pending"]=quiet.pending();reply["pending_clip"]=quiet.clip;
    reply["pending_wait_ms"]=quiet.pending()?quiet.waited_ms(now):0;reply["server_speaking"]=server_speaking;
    reply["quiet_ms"]=QuietWait::kQuietUs/1000;reply["max_wait_ms"]=QuietWait::kMaxWaitUs/1000;
    reply["happy_count"]=policy.happy_count;reply["happy_needed"]=policy.happy_needed;
    reply["happy_window_left_s"]=policy.happy_window&&now-policy.happy_window<kHappyWindowUs?static_cast<int32_t>((kHappyWindowUs-(now-policy.happy_window))/1000000):0;
    if(with_events) {
        auto list=out["events"].to<JsonArray>();
        const uint32_t first=event_seq>=kEvents?event_seq-kEvents+1:1;
        for(uint32_t seq=std::max(first,events_after+1);seq<=event_seq;++seq) {
            const auto& e=events[seq%kEvents];if(e.seq!=seq)continue;
            auto item=list.add<JsonObject>();item["seq"]=e.seq;item["age_ms"]=static_cast<int32_t>((now-e.us)/1000);
            item["kind"]=e.kind;item["label"]=e.label;item["detail"]=e.detail;if(e.p>=0)item["p"]=e.p/100.0f;
        }
    }
    out["supported"]=true;out["ready"]=ready;out["url"]=url;out["token_configured"]=!secret.empty();
    out["requested"]=control.requested();out["connected"]=connected;out["state"]=state;out["error"]=error;
    out["worker_stack_free_bytes"]=worker_stack_free.load();
    out["last_encode_us"]=encode_us.load();out["last_send_us"]=send_us.load();out["last_poll_us"]=poll_us.load();
    out["session"]=control.generation();out["packets"]=packets;out["dropped_blocks"]=dropped.load();out["reconnects"]=reconnects;out["responses"]=responses;
    out["asr_starts"]=asr_starts;out["asr_stops"]=asr_stops;out["speaker"]=speaker;out["speaker_band"]=band;
    out["text"]=text;out["emotion"]=emotion;out["emotion_reliable"]=false;out["respond_to"]="everyone";out["responses_muted"]=responses_muted;
    out["last_speech_age_ms"]=last_speech?(esp_timer_get_time()-last_speech)/1000:-1;
    auto list=out["speakers"].to<JsonArray>();for(const auto& name:speakers)list.add(name);
    auto forget=out["forget"].to<JsonObject>();forget["name"]=forget_name;forget["state"]=forget_state;forget["error"]=forget_error;
    auto enrollment=out["enrollment"].to<JsonObject>();enrollment["active"]=control.enrolling();enrollment["name"]=enroll_name;
    enrollment["phase"]=enroll_phase;enrollment["n"]=enroll_n;enrollment["count"]=5;enrollment["rejected"]=enroll_rejected;
}

void events_json(JsonObject out,uint32_t after) {
    std::lock_guard<std::mutex> lock(mutex);const int64_t now=esp_timer_get_time();
    auto list=out["events"].to<JsonArray>();
    const uint32_t first=event_seq>=kEvents?event_seq-kEvents+1:1;
    for(uint32_t seq=std::max(first,after+1);seq<=event_seq;++seq){
        const auto& e=events[seq%kEvents];if(e.seq!=seq)continue;
        auto item=list.add<JsonObject>();item["seq"]=e.seq;item["age_ms"]=static_cast<int32_t>((now-e.us)/1000);
        item["kind"]=e.kind;item["label"]=e.label;item["detail"]=e.detail;if(e.p>=0)item["p"]=e.p/100.0f;
    }
}
esp_err_t handle(ControlReply* request,JsonObject doc) {
    const std::string op=doc["op"]|"";
    if(op=="listen"&&doc.size()==2&&doc["enabled"].is<bool>())
        return set_listening(doc["enabled"])?respond(request,"202 Accepted","listen_updated"):respond(request,"409 Conflict","configure_backend_first");
    std::lock_guard<std::mutex> lock(mutex);
    if(op=="config"&&doc.size()==3&&doc["url"].is<const char*>()&&doc["token"].is<const char*>()) {
        const std::string endpoint=doc["url"].as<std::string>(),key=doc["token"].as<std::string>().empty()?secret:doc["token"].as<std::string>();
        if(!valid_url(endpoint)||!valid_token(key))return respond(request,"400 Bad Request","invalid_url_or_token");
        JsonDocument config;config["url"]=endpoint;config["token"]=key;std::string value;serializeJson(config,value);
        nvs_handle_t nvs;esp_err_t err=nvs_open("collector",NVS_READWRITE,&nvs);
        if(err==ESP_OK){err=nvs_set_str(nvs,"ear_config",value.c_str());if(err==ESP_OK)err=nvs_commit(nvs);nvs_close(nvs);}
        if(err!=ESP_OK)return respond(request,"503 Service Unavailable","config_save_failed");
        control.configure();new_session_locked();url=endpoint;secret=key;state="idle";error.clear();enroll_phase="idle";
        return respond(request,"202 Accepted","configuration_saved");
    }
    if(op=="enroll"&&doc.size()==3&&doc["name"].is<const char*>()&&doc["replace"].is<bool>()) {
        const std::string name=doc["name"].as<std::string>();
        if(name.empty()||name.size()>144||name.find_first_of("/\\\r\n\t")!=std::string::npos)return respond(request,"400 Bad Request","invalid_name");
        if(!ready||url.empty()||secret.empty()||control.enrolling())return respond(request,"409 Conflict","not_ready_or_enrollment_active");
        control.begin_enrollment();enroll_name=name;replace_speaker=doc["replace"];enroll_n=0;enroll_phase="connecting";
        // No 1.2 s self-sound guard here (2026-11-10): a web enrollment makes no sound, and the guard
        // marked the user's first sentence as her own voice, so it never counted.
        new_session_locked();enroll_id=boot+"-"+std::to_string(control.generation());state="connecting";error.clear();quiet_until=0;
        return respond(request,"202 Accepted","enrollment_requested");
    }
    if(op=="forget"&&doc.size()==2&&doc["name"].is<const char*>()) {
        // Deleting goes through the backend that stores the voiceprints, on the live session.
        const std::string name=doc["name"].as<std::string>();
        if(name.empty()||name.size()>144)return respond(request,"400 Bad Request","invalid_name");
        if(std::find(speakers.begin(),speakers.end(),name)==speakers.end())return respond(request,"404 Not Found","unknown_speaker");
        if(!connected||control.enrolling())return respond(request,"409 Conflict","start_listening_first");
        forget_pending=name;forget_name=name;forget_state="pending";forget_error.clear();
        return respond(request,"202 Accepted","forget_requested");
    }
    if(op=="cancel"&&doc.size()==1) {
        const bool was_enrolling=control.enrolling();control.cancel_enrollment();
        if(was_enrolling)finish_enroll_locked("cancelled");
        return respond(request,"202 Accepted","enrollment_cancelled");
    }
    return respond(request,"400 Bad Request","invalid_hearing_command");
}
}
