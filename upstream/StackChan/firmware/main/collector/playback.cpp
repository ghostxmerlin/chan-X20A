/* SPDX-License-Identifier: MIT */
#include "playback.h"
#include "playback_session.h"
#include "voice_library.h"
#include "voice_policy.h"
#include "hearing_policy.h"
#include "eyes/eyes.h"
#include "eyes/voice_cue.h"
#include <board.h>
#include <hal/board/cores3_audio_codec.h>
#include <hal/board/hal_bridge.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_random.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <lwip/sockets.h>
#include <algorithm>
#include <cstdio>
#include <mutex>
#include <new>

namespace collector {
namespace {
constexpr char kTag[] = "CollectorPlayback";
constexpr int64_t kUploadTimeoutUs = 10000000;
PlaybackGate gate;
std::mutex status_mutex;
const char* state = "idle";
const char* source = "none";
uint32_t reaction_dropped = 0;
uint32_t reaction_queued = 0;
bool remote_job = false;
PendingReaction pending_reaction;
const char* error_stage = "none";
int error_code = 0, cleanup_error = 0;
uint32_t job_sequence = 0;
size_t expected_bytes = 0, received_bytes = 0, submitted_bytes = 0;
std::atomic<CoreS3AudioCodec*> output_codec{nullptr};
std::atomic_bool voice_audio_ready{false};
VoiceCooldown voice_cooldown;
hearing::PlaybackOwner hearing_owner;
std::atomic<int64_t> self_tail_until{0};
uint32_t hearing_job=0;
int current_voice_id=0, last_voice_id=0;
uint32_t asr_voice_received=0, asr_voice_accepted=0, asr_voice_dropped=0;
int64_t voice_accepted_us=0, voice_output_started_us=0;

void set_state(const char* value)
{
    std::lock_guard<std::mutex> lock(status_mutex);
    state = value;
}

esp_err_t response(httpd_req_t* request, const char* status, const char* body)
{
    httpd_resp_set_status(request, status);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_sendstr(request, body);
}

esp_err_t reject(httpd_req_t* request, const char* status, const char* body)
{
    httpd_resp_set_hdr(request, "Connection", "close");
    response(request, status, body);
    // ESP_FAIL closes the synchronous request without purging an unbounded
    // rejected body on the main HTTP server task.
    return ESP_FAIL;
}

esp_err_t complete_request(httpd_req_t* request, bool close)
{
    const int socket = httpd_req_to_sockfd(request);
    // Shutdown first prevents any residual PCM becoming another request when
    // async completion releases the socket. The HTTP server owns its close.
    if (close) shutdown(socket, SHUT_RDWR);
    // The server sees EOF and closes after completion; a second deferred close
    // by this numeric fd could race with the server reusing that descriptor.
    return httpd_req_async_handler_complete(request);
}

struct Job {
    httpd_req_t* request = nullptr;
    int16_t* pcm = nullptr;
    size_t bytes = 0;
    uint32_t id = 0;
    RemoteReaction reaction = RemoteReaction::None;
    const VoiceClip* voice = nullptr;
    int64_t not_before_us = 0;
    bool from_asr = false;
    uint32_t hearing_session = 0;
};
QueueHandle_t playback_queue=nullptr;
StaticQueue_t playback_queue_control;
StaticTask_t playback_worker_control;
Job* playback_slot[1];
constexpr size_t kPlaybackStackBytes=16384;
StackType_t* playback_stack=nullptr;
std::atomic<uint32_t> playback_stack_free{0};
bool enqueue_playback(Job* job) {return playback_queue&&xQueueSend(playback_queue,&job,0)==pdTRUE;}

struct Output {
    CoreS3AudioCodec* codec;
    int64_t write_deadline_us = 0;
    const Job* job=nullptr;
    eyes::VoiceCue cue{};
    int64_t now() { return esp_timer_get_time(); }
    void state(const char* value) { set_state(value); }
    void submitted(size_t bytes) {
        std::lock_guard<std::mutex> lock(status_mutex);
        submitted_bytes=bytes;
        if(cue.submitted(bytes) && job && job->voice && hearing_owner.allowed(job->hearing_session))
            eyes::voice_started(job->id,job->voice->id,(job->voice->frames*1000u+23999u)/24000u);
    }
    void pause() { vTaskDelay(1); }
    void drain() { vTaskDelay(std::max<TickType_t>(1, pdMS_TO_TICKS(100))); }
    esp_err_t begin()
    {
        if(job && !hearing_owner.allowed(job->hearing_session))return ESP_ERR_INVALID_STATE;
        auto err = hal_bridge::board_set_speaker_reset(true);
        if (err != ESP_OK) return err;
        err = codec->PrepareCollectorOutput();
        if (err != ESP_OK) return err;
        // The complete zero-filled TX ring must drain while RESET_N is low.
        drain();
        err = hal_bridge::board_set_speaker_reset(false);
        if (err != ESP_OK) return err;
        vTaskDelay(std::max<TickType_t>(1, pdMS_TO_TICKS(50)));
        err = codec->BeginCollectorOutput(kPlaybackVolume);
        if (err==ESP_OK) {
            std::lock_guard<std::mutex> lock(status_mutex);
            if (current_voice_id) voice_output_started_us=esp_timer_get_time();
        }
        // Eight seconds maximum media plus two seconds of scheduling/drain
        // allowance; slow successful writes cannot hold the single slot forever.
        write_deadline_us = esp_timer_get_time() + 10000000;
        return err;
    }
    esp_err_t write(const int16_t* data, size_t count)
    {
        if(job && !hearing_owner.allowed(job->hearing_session))return ESP_ERR_INVALID_STATE;
        if (esp_timer_get_time() >= write_deadline_us) return ESP_ERR_TIMEOUT;
        const auto error = codec->WriteCollectorOutput(data, count);
        return error == ESP_OK && esp_timer_get_time() >= write_deadline_us ? ESP_ERR_TIMEOUT : error;
    }
    esp_err_t finish()
    {
        const auto codec_error = codec->EndCollectorOutput();
        esp_err_t reset_error = ESP_FAIL;
        for (int attempt = 0; attempt < 3; ++attempt) {
            reset_error = hal_bridge::board_set_speaker_reset(true);
            if (reset_error == ESP_OK) break;
            pause();
        }
        return reset_error != ESP_OK ? reset_error : codec_error;
    }
};

void process_playback(Job* job)
{
    for (;;) {
    PlaybackUpload upload(job->bytes);
    const int64_t deadline = esp_timer_get_time() + kUploadTimeoutUs;
    int failure = 0;
    const char* failure_stage = "none";
    if (job->request) {
        while (upload.received() < upload.bytes()) {
            if (esp_timer_get_time() >= deadline) { failure = ESP_ERR_TIMEOUT; break; }
            const size_t count = std::min<size_t>(4096, upload.bytes() - upload.received());
            const int got = httpd_req_recv(job->request, reinterpret_cast<char*>(job->pcm) + upload.received(), count);
            if (got == HTTPD_SOCK_ERR_TIMEOUT) { vTaskDelay(1); continue; }
            if (got <= 0 || !upload.append(got)) { failure = ESP_FAIL; break; }
            { std::lock_guard<std::mutex> lock(status_mutex); received_bytes = upload.received(); }
            vTaskDelay(1);
        }
        if (!failure && (!upload.ready() || esp_timer_get_time() >= deadline)) failure = ESP_ERR_TIMEOUT;
        if (failure) {
            failure_stage = "upload";
            response(job->request, "400 Bad Request", "{\"accepted\":false,\"error\":\"incomplete_or_timed_out_upload\"}");
        } else {
            char body[128];
            snprintf(body, sizeof(body), "{\"accepted\":true,\"job_id\":%lu,\"bytes\":%u}",
                     static_cast<unsigned long>(job->id), static_cast<unsigned>(job->bytes));
            if (response(job->request, "202 Accepted", body) != ESP_OK) {
                failure = ESP_FAIL; failure_stage = "response";
            }
        }
        const auto completion_error = complete_request(job->request, failure != 0);
        if (!failure && completion_error != ESP_OK) { failure = completion_error; failure_stage = "response"; }
        job->request = nullptr;
    } else if (job->voice) {
        if (const auto* error=voice_decode(*job->voice, job->pcm)) {
            failure=ESP_FAIL; failure_stage=error;
        } else {
            upload.append(job->bytes);
            std::lock_guard<std::mutex> lock(status_mutex);
            received_bytes=job->bytes;
        }
    } else {
        synthesize_reaction(job->reaction, job->pcm, job->bytes / sizeof(int16_t));
        upload.append(job->bytes);
        std::lock_guard<std::mutex> lock(status_mutex);
        received_bytes = job->bytes;
    }
    PlaybackResult result;
    if (!failure) {
        auto* codec = static_cast<CoreS3AudioCodec*>(Board::GetInstance().GetAudioCodec());
        output_codec = codec;
        Output output{codec};
        output.job=job;output.cue.from_asr=job->from_asr || job->hearing_session;
        wait_voice_start(job->not_before_us,output);
        result = run_pcm_playback(job->pcm, upload, output);
    } else { result.error = failure; result.stage = failure_stage; }
    if(job->from_asr || job->hearing_session)eyes::cancel_voice(job->id);
    ESP_LOGI(kTag, "job %lu %s: submitted %u/%u bytes, error %d (%s), cleanup %d",
             static_cast<unsigned long>(job->id), result.error ? "failed" : "completed",
             static_cast<unsigned>(result.submitted), static_cast<unsigned>(job->bytes),
             result.error, result.stage, result.cleanup_error);
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        error_code = result.error; cleanup_error = result.cleanup_error; error_stage = result.stage;
        state = result.error ? "failed" : "completed";
        if (result.error && pending_reaction.clear()) ++reaction_dropped;
        const auto next = pending_reaction.take(esp_timer_get_time());
        if (remote_job && next != RemoteReaction::None) {
            job->reaction = next; job->id = ++job_sequence;
            source = next == RemoteReaction::Heart ? "remote_heart" : "remote_reject";
            state = "starting"; error_code = cleanup_error = 0; error_stage = "none";
            received_bytes = submitted_bytes = 0;
            continue;
        }
        remote_job = false;
        voice_cooldown.finished(esp_timer_get_time());
        self_tail_until=esp_timer_get_time()+1200000;
        hearing_job=0;
        gate.release();
    }
    break;
    }
    heap_caps_free(job->pcm);
    delete job;
}
void playback_worker(void*) {
    for (;;) {
        Job* job=nullptr;
        if(xQueueReceive(playback_queue,&job,portMAX_DELAY)==pdTRUE) {
            process_playback(job);
            playback_stack_free=uxTaskGetStackHighWaterMark(nullptr);
        }
    }
}
}
void playback_init() {
    if(playback_queue)return;
    // Collector output is transient I2S/I2C work and never writes NVS. Reserve
    // one external stack before network clients fragment internal heap memory.
    playback_stack=static_cast<StackType_t*>(heap_caps_malloc(kPlaybackStackBytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!playback_stack)return;
    playback_queue=xQueueCreateStatic(1,sizeof(Job*),reinterpret_cast<uint8_t*>(playback_slot),&playback_queue_control);
    if(playback_queue&&xTaskCreateStatic(playback_worker,"collector_playback",kPlaybackStackBytes,nullptr,3,playback_stack,&playback_worker_control))return;
    if(playback_queue)vQueueDelete(playback_queue);
    playback_queue=nullptr;heap_caps_free(playback_stack);playback_stack=nullptr;
}

esp_err_t playback_handle(httpd_req_t* request)
{
    if (!playback_length_valid(request->content_len))
        return reject(request, "400 Bad Request", "{\"error\":\"expected_even_2_to_384000_bytes_s16le_24000_mono\"}");
    bool reserved;
    { std::lock_guard<std::mutex> lock(status_mutex); reserved = gate.acquire(); if (reserved) remote_job = false; }
    if (!reserved) return reject(request, "409 Conflict", "{\"error\":\"playback_busy\"}");
    auto* job = new (std::nothrow) Job;
    if (job) job->pcm = static_cast<int16_t*>(heap_caps_malloc(request->content_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!job || !job->pcm) {
        delete job; gate.release();
        return reject(request, "503 Service Unavailable", "{\"error\":\"playback_memory\"}");
    }
    job->bytes = request->content_len;
    if (httpd_req_async_handler_begin(request, &job->request) != ESP_OK) {
        heap_caps_free(job->pcm); delete job; gate.release();
        return reject(request, "503 Service Unavailable", "{\"error\":\"playback_request\"}");
    }
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        job->id = ++job_sequence;
        current_voice_id=0;
        state = "uploading"; source = "upload"; error_code = cleanup_error = 0; error_stage = "none";
        expected_bytes = job->bytes; received_bytes = submitted_bytes = 0;
    }
    if (!enqueue_playback(job)) {
        response(job->request, "503 Service Unavailable", "{\"error\":\"playback_task\"}");
        complete_request(job->request, true);
        heap_caps_free(job->pcm); delete job;
        { std::lock_guard<std::mutex> lock(status_mutex); state = "failed"; error_code = ESP_ERR_NO_MEM; error_stage = "task"; }
        gate.release();
    }
    return ESP_OK;
}

bool playback_reaction(RemoteReaction reaction)
{
    if (reaction != RemoteReaction::Heart && reaction != RemoteReaction::Reject) return false;
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        if (!gate.acquire()) {
            if (remote_job) {
                if (pending_reaction.put(reaction, esp_timer_get_time())) ++reaction_dropped;
                ++reaction_queued;
                return true;
            }
            ++reaction_dropped;
            return false;
        }
        remote_job = true;
    }
    auto* job = new (std::nothrow) Job;
    if (job) {
        job->bytes = kReactionFrames * sizeof(int16_t);
        job->reaction = reaction;
        job->pcm = static_cast<int16_t*>(heap_caps_malloc(job->bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    if (!job || !job->pcm) {
        delete job;
        std::lock_guard<std::mutex> lock(status_mutex); ++reaction_dropped;
        if (pending_reaction.clear()) ++reaction_dropped;
        remote_job = false; gate.release();
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        job->id = ++job_sequence;
        current_voice_id=0;
        source = reaction == RemoteReaction::Heart ? "remote_heart" : "remote_reject";
        state = "starting"; error_code = cleanup_error = 0; error_stage = "none";
        expected_bytes = job->bytes; received_bytes = submitted_bytes = 0;
    }
    if (enqueue_playback(job)) return true;
    heap_caps_free(job->pcm); delete job;
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        state = "failed"; error_code = ESP_ERR_NO_MEM; error_stage = "task"; ++reaction_dropped;
        if (pending_reaction.clear()) ++reaction_dropped;
        remote_job = false; gate.release();
    }
    return false;
}

void playback_set_audio_ready() { voice_audio_ready=playback_queue!=nullptr; }

static esp_err_t playback_voice_impl(int requested_id, bool from_asr, uint32_t* job_id, int* selected_id, uint32_t hearing_session)
{
    if (requested_id<0 || requested_id>kVoiceCount) return ESP_ERR_INVALID_ARG;
    const int64_t accepted_us=esp_timer_get_time();
    int selected;
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        if (from_asr) ++asr_voice_received;
        if (!hearing_owner.allowed(hearing_session) || !voice_audio_ready || (from_asr && !voice_cooldown.allowed(esp_timer_get_time())) || !gate.acquire()) {
            if (from_asr) ++asr_voice_dropped;
            return ESP_ERR_INVALID_STATE;
        }
        remote_job=false;
        selected=choose_voice(requested_id,esp_random(),last_voice_id);
    }
    auto* job=new (std::nothrow) Job;
    if (job) {
        job->voice=voice_clip(selected);
        job->from_asr=from_asr;
        job->hearing_session=hearing_session;
        job->not_before_us=voice_not_before(from_asr,accepted_us);
        job->bytes=job->voice->frames*sizeof(int16_t);
        job->pcm=static_cast<int16_t*>(heap_caps_malloc(job->bytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    }
    if (!job || !job->pcm) {
        delete job;
        std::lock_guard<std::mutex> lock(status_mutex);
        if (from_asr) ++asr_voice_dropped;
        gate.release(); return ESP_ERR_NO_MEM;
    }
    uint32_t id;
    {
        std::lock_guard<std::mutex> lock(status_mutex);
        id=job->id=++job_sequence; current_voice_id=selected; last_voice_id=selected;
        voice_accepted_us=accepted_us; voice_output_started_us=0;
        source=hearing_session?"hearing_emotion":from_asr?"asr_next":"web_voice";
        hearing_job=hearing_session?id:0;
        state="starting"; error_code=cleanup_error=0; error_stage="none";
        expected_bytes=job->bytes; received_bytes=submitted_bytes=0;
    }
    if (!enqueue_playback(job)) {
        heap_caps_free(job->pcm); delete job;
        std::lock_guard<std::mutex> lock(status_mutex);
        state="failed"; error_code=ESP_ERR_NO_MEM; error_stage="task";
        if (from_asr) ++asr_voice_dropped;
        gate.release(); return ESP_ERR_NO_MEM;
    }
    if (from_asr) { std::lock_guard<std::mutex> lock(status_mutex); ++asr_voice_accepted; }
    if (job_id) *job_id=id;
    if (selected_id) *selected_id=selected;
    return ESP_OK;
}

esp_err_t playback_voice(int requested_id,bool from_asr,uint32_t* job_id,int* selected_id)
{
    return playback_voice_impl(requested_id,from_asr,job_id,selected_id,0);
}
esp_err_t playback_hearing(int clip,uint32_t session)
{
    if(!session)return ESP_ERR_INVALID_ARG;
    return playback_voice_impl(clip,false,nullptr,nullptr,session);
}
void playback_set_hearing_session(uint32_t session)
{
    std::lock_guard<std::mutex> lock(status_mutex);
    hearing_owner.activate(session);
    if(hearing_job)eyes::cancel_voice(hearing_job);
}
bool playback_self_sounding()
{
    return gate.busy() || esp_timer_get_time()<self_tail_until.load();
}
bool playback_busy() { return gate.busy(); }

void playback_asr_command(uint8_t id, bool has_value)
{
    if (voice_asr_next(id,has_value)) playback_voice(0,true);
}

bool playback_output_enabled()
{
    auto* codec = output_codec.load();
    return codec && codec->CollectorOutputEnabled();
}

void playback_json(ArduinoJson::JsonObject target)
{
    std::lock_guard<std::mutex> lock(status_mutex);
    target["state"] = state; target["busy"] = gate.busy(); target["job_id"] = job_sequence;
    target["worker_ready"] = playback_queue!=nullptr;target["worker_stack_free_bytes"] = playback_stack_free.load();
    target["expected_bytes"] = expected_bytes; target["received_bytes"] = received_bytes;
    target["submitted_bytes"] = submitted_bytes; target["error"] = error_code;
    target["error_stage"] = error_stage; target["cleanup_error"] = cleanup_error;
    target["sample_rate"] = 24000; target["channels"] = 1; target["bits"] = 16;
    target["format"] = "s16le"; target["volume"] = kPlaybackVolume;
    target["source"] = source; target["reaction_dropped"] = reaction_dropped;
    target["reaction_queued"] = reaction_queued;
    target["voice_clip_id"]=current_voice_id;
    if (const auto* clip=voice_clip(current_voice_id)) {
        target["voice_text"]=clip->text; target["voice_title"]=clip->title;
    }
    target["asr_next_received"]=asr_voice_received;
    target["asr_next_accepted"]=asr_voice_accepted;
    target["asr_next_dropped"]=asr_voice_dropped;
    target["asr_cooldown_ms"]=1200;
    target["asr_start_delay_ms"]=kAsrVoiceStartDelayMs;
    target["voice_accepted_us"]=current_voice_id ? voice_accepted_us : 0;
    target["voice_output_started_us"]=current_voice_id ? voice_output_started_us : 0;
    target["output_enabled"] = playback_output_enabled();
    const int reset = hal_bridge::board_speaker_reset_state();
    if (reset < 0) target["hardware_reset_held"] = nullptr;
    else target["hardware_reset_held"] = reset != 0;
}
}
