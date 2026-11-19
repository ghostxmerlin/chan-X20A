/* SPDX-License-Identifier: MIT */
// Robot command line (collector-0.5.10): the /mouth task parses each cmd (robot_cmd.h) and answers a bad or too
// frequent one at once; the rest go to one worker task (PSRAM stack: no flash access here, the volume is RAM only)
// that calls the existing subsystems and queues the cmd_result line for the /mouth task to send.
#include "robot_cmd.h"
#include "asr_unit.h"
#include "eye_mode.h"
#include "eyes/eyes.h"
#include "head_gesture.h"
#include "hearing_policy.h"
#include "m12_unit.h"
#include "mouth_link.h"
#include "playback.h"
#include "psram_task.h"
#include "remote.h"
#include "voice_library.h"
#include "voice_policy.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <mutex>

namespace collector::cmd {
namespace {
constexpr const char* kTag = "robot_cmd";
constexpr size_t kQueue = 4, kOutCap = 6;
constexpr uint32_t kYawWaitMs = 1500, kLearnWaitMs = 4500;

struct PsramAllocator final : ArduinoJson::Allocator {
    void* allocate(size_t n) override { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
    void deallocate(void* p) override { heap_caps_free(p); }
    void* reallocate(void* p, size_t n) override { return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
} psram_allocator;

struct Job { Cmd c; uint32_t gen = 0; };
struct Line { char text[kResultMax]; size_t n; uint32_t gen; };

// The queue's control block stays in internal RAM (its spinlock needs S32C1I); its storage is in PSRAM.
StaticQueue_t queue_control;
QueueHandle_t queue = nullptr;
std::atomic<uint32_t> gen{1};                // +1 for every /mouth drop: older jobs and answers are dropped
std::mutex out_mutex;                        // outbox + counters
Line* outbox = nullptr;                      // kOutCap lines in PSRAM
size_t out_n = 0;
uint32_t received = 0, answered = 0, refused = 0, dropped = 0, bad = 0;
const char* last_verb = "";
const char* last_error = "";
RateLimit rate;                              // the /mouth task only

std::mutex word_mutex;                       // the word for the screen loop
char word_text[kWordBytes + 1] = {};
uint32_t word_hold = 0;
std::atomic<uint32_t> word_seq{0};
uint32_t word_taken = 0;                     // screen loop only

std::atomic<void (*)(ArduinoJson::JsonObject)> status_hook{nullptr};

// M12 learn: the M12 task's callback answers the one waiting learn.
std::mutex learn_mutex;
StaticSemaphore_t learn_sem_buffer;
SemaphoreHandle_t learn_done = nullptr;
uint32_t learn_token = 0, learn_answered = 0;
bool learn_ok = false;
int learn_samples = -1;
char learn_err[16] = {};

void push_line(const Line& l)
{
    std::lock_guard<std::mutex> lock(out_mutex);
    if (!outbox) return;
    if (out_n == kOutCap) { std::memmove(outbox, outbox + 1, sizeof(Line) * (kOutCap - 1)); --out_n; ++dropped; }
    outbox[out_n++] = l;
}

// One cmd_result line. data_fn fills data (may be null). error = nullptr: ok.
template<class Fill> void answer(uint32_t id, uint32_t g, const char* error, Fill&& fill)
{
    ArduinoJson::JsonDocument doc(&psram_allocator);
    doc["type"] = "cmd_result"; doc["id"] = id; doc["ok"] = error == nullptr;
    if (error) doc["error"] = error; else doc["error"] = nullptr;
    auto data = doc["data"].to<ArduinoJson::JsonObject>();
    fill(data);
    Line* l = static_cast<Line*>(heap_caps_malloc(sizeof(Line), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!l) return;
    l->gen = g;
    if (ArduinoJson::measureJson(doc) >= sizeof l->text) {    // too long: the answer without its data
        doc.remove("data");
        doc["data"].to<ArduinoJson::JsonObject>();
    }
    l->n = ArduinoJson::serializeJson(doc, l->text, sizeof l->text);
    push_line(*l);
    heap_caps_free(l);
    std::lock_guard<std::mutex> lock(out_mutex);
    ++answered;
    if (error) { last_error = error; ++refused; }
}
void answer(uint32_t id, uint32_t g, const char* error) { answer(id, g, error, [](ArduinoJson::JsonObject) {}); }

float tenth(float v) { return std::round(v * 10.f) / 10.f; }

// ---- the verbs (worker task) ----
void do_eyes(const Job& j)
{
    eyes::State st = eyes::parse_state(j.c.text);
    if (!eyes::valid(st)) st = eye_state_for_emotion(j.c.text);
    if (!eyes::valid(st)) return answer(j.c.id, j.gen, "bad_args");
    eyes::Command c;
    c.op = eyes::Operation::Play; c.state = st; c.mode = eyes::Mode::Once;
    eye_request_id(c.request_id, [] { return esp_random(); });
    const auto r = eyes::submit(c, eyes::Source::Manual);
    if (!r.accepted) {
        const auto e = r.error;
        const char* why = e == eyes::Error::Busy ? "busy" :
            (e == eyes::Error::Disabled || e == eyes::Error::NotVisible || e == eyes::Error::PageHidden || e == eyes::Error::Reaction) ? "eyes_off" : "failed";
        return answer(j.c.id, j.gen, why, [&](ArduinoJson::JsonObject d) { d["eyes"] = eyes::name(e); });
    }
    answer(j.c.id, j.gen, nullptr, [&](ArduinoJson::JsonObject d) { d["state"] = eyes::kStateIds[size_t(st)]; });
}

void do_word(const Job& j)
{
    {
        std::lock_guard<std::mutex> lock(word_mutex);
        std::snprintf(word_text, sizeof word_text, "%s", j.c.text);
        word_hold = j.c.hold_ms;
    }
    word_seq.fetch_add(1, std::memory_order_release);
    answer(j.c.id, j.gen, nullptr, [&](ArduinoJson::JsonObject d) {
        d["text"] = j.c.text; d["hold_ms"] = j.c.hold_ms; d["stays"] = *j.c.text && j.c.hold_ms > kWordDefaultMs;
    });
}

void do_clip(const Job& j)
{
    if (mouth::speaking()) return answer(j.c.id, j.gen, "speaking");
    int id = j.c.value;
    if (!id && *j.c.text) {
        id = hearing::emotion_clip(j.c.text, esp_random(), kVoiceCount, voice_emotion);
        if (!id) return answer(j.c.id, j.gen, "no_clip");
    }
    uint32_t job = 0;
    int selected = 0;
    const esp_err_t r = playback_voice(id, false, &job, &selected);
    if (r != ESP_OK) return answer(j.c.id, j.gen, r == ESP_ERR_INVALID_ARG ? "bad_args" : r == ESP_ERR_INVALID_STATE ? "busy" : "failed");
    answer(j.c.id, j.gen, nullptr, [&](ArduinoJson::JsonObject d) {
        d["clip_id"] = selected; d["emotion"] = voice_emotion(selected);
        if (const VoiceClip* v = voice_clip(selected)) d["title"] = v->title;
    });
}

// An ASR plan outcome -> the answer: accepted is ok; the plan's refusals are the error codes.
void yaw_answer(const Job& j, const AsrMotionOutcome& o)
{
    const char* r = o.result ? o.result : "failed";
    const bool ok = !std::strcmp(r, "accepted");
    const char* why = ok ? nullptr : !std::strcmp(r, "rejected") ? "busy" :
        !std::strcmp(r, "unavailable") ? (m12_snapshot().online ? "motor_off" : "m12_offline") :
        !std::strcmp(r, "timeout") ? "failed" : r;
    answer(j.c.id, j.gen, why, [&](ArduinoJson::JsonObject d) {
        if (o.action) d["action"] = o.action;
        d["facing"] = yaw_facing_name(remote_yaw_status().facing);
        d["tracking"] = remote_tracking_requested();
    });
}

void do_learn(const Job& j)
{
    if (!m12_snapshot().online) return answer(j.c.id, j.gen, "m12_offline");
    uint32_t token;
    {
        std::lock_guard<std::mutex> lock(learn_mutex);
        token = ++learn_token ? learn_token : ++learn_token;
    }
    xSemaphoreTake(learn_done, 0);
    M12RequestArgs args;
    args.name = j.c.text; args.face = -1;                       // the largest face in view
    const int32_t rid = m12_request(M12Op::learn, args, [](const M12Reply& reply, const char* err, void* ctx) {
        std::lock_guard<std::mutex> lock(learn_mutex);
        if (static_cast<uint32_t>(reinterpret_cast<uintptr_t>(ctx)) != learn_token) return;
        learn_answered = learn_token; learn_ok = !err; learn_samples = reply.samples;
        std::snprintf(learn_err, sizeof learn_err, "%s", err ? err : "");
        xSemaphoreGive(learn_done);
    }, reinterpret_cast<void*>(static_cast<uintptr_t>(token)));
    if (rid < 0) return answer(j.c.id, j.gen, rid == -2 ? "m12_offline" : rid == -3 ? "busy" : rid == -4 ? "bad_args" : "failed");
    const TickType_t until = xTaskGetTickCount() + pdMS_TO_TICKS(kLearnWaitMs);
    for (;;) {
        const TickType_t now = xTaskGetTickCount();
        const bool given = xSemaphoreTake(learn_done, int32_t(until - now) > 0 ? until - now : 0) == pdTRUE;
        std::lock_guard<std::mutex> lock(learn_mutex);
        if (learn_answered == token) break;
        if (!given) { ++learn_token; return answer(j.c.id, j.gen, "failed", [](ArduinoJson::JsonObject d) { d["m12"] = "no_reply"; }); }
    }
    bool ok; int samples; char err[16];
    { std::lock_guard<std::mutex> lock(learn_mutex); ok = learn_ok; samples = learn_samples; std::memcpy(err, learn_err, sizeof err); }
    ESP_LOGI(kTag, "face_learn #%ld: %s", static_cast<long>(rid), ok ? "ok" : err);
    if (!ok) {
        char code[24];
        std::snprintf(code, sizeof code, "m12_%s", err);
        return answer(j.c.id, j.gen, !std::strcmp(err, "client_timeout") ? "failed" : code);
    }
    answer(j.c.id, j.gen, nullptr, [&](ArduinoJson::JsonObject d) { d["name"] = j.c.text; d["samples"] = samples; });
}

void do_status(const Job& j)
{
    answer(j.c.id, j.gen, nullptr, [](ArduinoJson::JsonObject d) {
        d["fw"] = FIRMWARE_VERSION;
        d["up_s"] = static_cast<int32_t>(esp_timer_get_time() / 1000000);
        mouth::ModeView view;
        mouth::mode_view(view);
        d["mode"] = view.look.label; d["online"] = view.online;
        d["volume"] = playback_volume();
        const auto r = remote_snapshot();
        if (r.yaw_valid) d["yaw"] = tenth(r.yaw_angle_deg); else d["yaw"] = nullptr;
        if (r.pitch_valid) d["pitch"] = tenth(r.pitch_angle_deg); else d["pitch"] = nullptr;
        const AsrYawStatus s = remote_yaw_status();
        d["facing"] = yaw_facing_name(s.facing);
        d["tracking"] = s.tracking; d["motor"] = s.control;
        d["moving"] = s.turn_busy || s.manual_busy || !s.settled;
        d["m12"] = m12_snapshot().online;
        d["speaking"] = mouth::speaking();
        d["playing"] = playback_busy();
        d["heap"] = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        d["psram"] = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
        if (auto hook = status_hook.load()) hook(d);      // battery, charging, chip_c, lux, camera, rssi
    });
}

void run_job(const Job& j)
{
    switch (j.c.verb) {
    case Verb::Eyes: return do_eyes(j);
    case Verb::Word: return do_word(j);
    case Verb::Clip: return do_clip(j);
    case Verb::Yaw: return yaw_answer(j, asr_yaw_request(AsrMotionWord::None, j.c.value, kYawWaitMs));
    case Verb::Front: return yaw_answer(j, asr_yaw_request(AsrMotionWord::Front, 0, kYawWaitMs));
    case Verb::TurnAround: {
        const bool back = remote_yaw_status().facing == YawFacing::Back;     // turned: turn back (and centre)
        return yaw_answer(j, asr_yaw_request(back ? AsrMotionWord::Front : AsrMotionWord::Back, 0, kYawWaitMs));
    }
    case Verb::Tracking: return yaw_answer(j, asr_yaw_request(j.c.on ? AsrMotionWord::TrackingOn : AsrMotionWord::TrackingOff, 0, kYawWaitMs));
    case Verb::Nod: case Verb::Shake: {
        const char* why = nullptr;
        uint32_t seq = 0;
        const bool nod = j.c.verb == Verb::Nod;
        if (remote_gesture(nod, j.c.value, &why, &seq) != ESP_OK) return answer(j.c.id, j.gen, why ? why : "failed");
        return answer(j.c.id, j.gen, nullptr, [&](ArduinoJson::JsonObject d) {
            d["gesture"] = nod ? "nod" : "shake"; d["times"] = j.c.value; d["seq"] = seq; d["amp_deg"] = (nod ? kNodAmpTenths : kShakeAmpTenths) / 10;
        });
    }
    case Verb::Volume: {
        const int v = volume_after(j.c, playback_volume());
        if (playback_set_volume(v, "server", false) != ESP_OK) return answer(j.c.id, j.gen, "bad_args");
        return answer(j.c.id, j.gen, nullptr, [&](ArduinoJson::JsonObject d) { d["volume"] = v; d["saved"] = false; });
    }
    case Verb::FaceLearn: return do_learn(j);
    case Verb::Status: return do_status(j);
    default: return answer(j.c.id, j.gen, "unknown_verb");
    }
}

void worker(void*)
{
    Job j;
    for (;;) {
        if (xQueueReceive(queue, &j, portMAX_DELAY) != pdTRUE) continue;
        if (j.gen != gen.load(std::memory_order_acquire)) { std::lock_guard<std::mutex> lock(out_mutex); ++dropped; continue; }
        ESP_LOGI(kTag, "#%lu %s", static_cast<unsigned long>(j.c.id), verb_id(j.c.verb));
        run_job(j);
    }
}
}  // namespace

void init()
{
    if (queue) return;
    outbox = static_cast<Line*>(heap_caps_calloc(kOutCap, sizeof(Line), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    auto* storage = static_cast<uint8_t*>(heap_caps_calloc(kQueue, sizeof(Job), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    learn_done = xSemaphoreCreateBinaryStatic(&learn_sem_buffer);
    if (!outbox || !storage || !learn_done) { ESP_LOGE(kTag, "no memory: command line off"); return; }
    queue = xQueueCreateStatic(kQueue, sizeof(Job), storage, &queue_control);
    // No flash access in this task (no NVS: the volume is RAM only), so the stack lives in PSRAM.
    if (create_psram_task(worker, "robot_cmd", 6144, nullptr, 3) != pdPASS) { ESP_LOGE(kTag, "task create failed"); queue = nullptr; }
}

bool ready() { return queue != nullptr; }

void submit(ArduinoJson::JsonObjectConst o, size_t n)
{
    Cmd c;
    const Parse p = parse(o, n, c);
    { std::lock_guard<std::mutex> lock(out_mutex); ++received; last_verb = p == Parse::Ok ? verb_id(c.verb) : "?"; }
    const uint32_t g = gen.load(std::memory_order_acquire);
    if (p == Parse::NoId) { std::lock_guard<std::mutex> lock(out_mutex); ++bad; return; }
    if (!queue) return answer(c.id, g, "failed");
    if (p != Parse::Ok) return answer(c.id, g, parse_error(p));
    if (!rate.allow(c.verb, esp_timer_get_time())) return answer(c.id, g, "rate_limited");
    Job j; j.c = c; j.gen = g;
    if (xQueueSend(queue, &j, 0) != pdTRUE) answer(c.id, g, "queue_full");
}

bool take_result(char* out, size_t cap, size_t& n)
{
    std::lock_guard<std::mutex> lock(out_mutex);
    const uint32_t g = gen.load(std::memory_order_acquire);
    while (out_n) {
        const Line& l = outbox[0];
        const bool current = l.gen == g && l.n < cap;
        if (current) { std::memcpy(out, l.text, l.n); n = l.n; }
        std::memmove(outbox, outbox + 1, sizeof(Line) * (out_n - 1));
        --out_n;
        if (current) return true;
        ++dropped;
    }
    return false;
}

void link_down()
{
    gen.fetch_add(1, std::memory_order_acq_rel);
    std::lock_guard<std::mutex> lock(out_mutex);
    dropped += out_n; out_n = 0;
}

bool take_word(char (&text)[kWordBytes + 1], uint32_t& hold_ms)
{
    const uint32_t s = word_seq.load(std::memory_order_acquire);
    if (s == word_taken) return false;
    word_taken = s;
    std::lock_guard<std::mutex> lock(word_mutex);
    std::memcpy(text, word_text, sizeof text);
    hold_ms = word_hold;
    return true;
}

void set_status_hook(void (*hook)(ArduinoJson::JsonObject out)) { status_hook.store(hook); }

void json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(out_mutex);
    out["ready"] = queue != nullptr;
    out["received"] = received; out["answered"] = answered; out["refused"] = refused;
    out["dropped"] = dropped; out["bad"] = bad; out["waiting"] = out_n;
    out["last_verb"] = last_verb; out["last_error"] = last_error;
    out["rate"] = "2 per 10 s per verb (eyes, word, clip, yaw, front, turn_around, tracking, nod, shake)";
}
}  // namespace collector::cmd
