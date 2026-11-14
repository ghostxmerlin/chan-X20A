/* SPDX-License-Identifier: MIT */
#include "mouth_link.h"
#include "mouth_assets.h"
#include "mouth_liveness.h"
#include "eyes/http.h"
#include "ui_font.h"
#include "voice_library.h"
#include "hal/board/shared_storage.h"
#include "hearing.h"
#include "hearing_socket.h"
#include "psram_task.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <mutex>
#include <new>

namespace collector::mouth {
namespace {
constexpr const char* kTag = "mouth";
constexpr size_t kSlots = 168;               // 10 s of 60 ms frames: the server leads by at most 8 s
constexpr size_t kOutbox = 16;
constexpr int64_t kHelloUs = 5000000;
using PacketRing = Ring<kSlots, kPacketMax>;

std::mutex mutex;
PacketRing* ring = nullptr;                  // storage in PSRAM (kSlots * kPacketMax bytes)
StreamState stream;
Report outbox[kOutbox];
size_t outbox_n = 0;
std::string device, boot, state = "idle", last_error;
bool connected = false, hello_ok = false;
uint32_t connects = 0, failures = 0, silent_drops = 0, streams = 0, reports_sent = 0, reports_dropped = 0, bad_messages = 0;
int64_t connected_us = 0;

// JSON documents on /mouth keep their pool in PSRAM (a text message is up to 1600 B).
struct PsramAllocator final : ArduinoJson::Allocator {
    void* allocate(size_t n) override { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
    void deallocate(void* p) override { heap_caps_free(p); }
    void* reallocate(void* p, size_t n) override { return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
} psram_allocator;

// hello modes step 1b: hello owns the mode (mouth_modes.h). The mode state has its own lock, never the audio
// one (`mutex`), and the two are never held together. The session lives in PSRAM; `mode_ver` mirrors its
// version for lock-free polling (an atomic in internal RAM: S32C1I does not work on PSRAM).
std::mutex mode_mutex;
ModeSession* modes_session = nullptr;
std::atomic<uint32_t> mode_ver{0};
void mode_publish_locked() { mode_ver.store(modes_session->version(), std::memory_order_release); }

// 0.4.96 SD assets over /mouth (mouth_assets.h): one Transfer in PSRAM behind its own lock, never held together with
// the audio or the mode lock. The /mouth task feeds it and sends its lines; the SD work runs on the asset install
// worker (eyes/http.cpp), never on this task.
std::mutex asset_mutex;
assets::Transfer* transfer = nullptr;
assets::JobView job_view;                    // the job on the worker (one at a time: written before the submit)
char eyes_path[48];                          // an eyes part's path, stable while its transfer runs (begin refuses busy)

void* psram_alloc(size_t n) { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
void psram_free(void* p) { heap_caps_free(p); }

// The firmware catalogs, as POST /v1/assets: font ids ("license", "cjk16.0"), "voice.<n>", "eyes.<style>.<state>".
bool resolve_part(const char* part, assets::Spec& out)
{
    shared_storage::FileSpec f{};
    if (ui_font::part(part, f)) { out = {f.path, f.bytes, f.sha256, true}; return true; }
    if (!std::strncmp(part, "voice.", 6)) {
        const char* d = part + 6;
        if (d[0] < '0' || d[0] > '9' || (d[1] && (d[1] < '0' || d[1] > '9' || d[2]))) return false;
        const VoiceClip* clip = voice_clip(d[1] ? (d[0] - '0') * 10 + (d[1] - '0') : d[0] - '0');
        if (!clip) return false;
        out = {clip->path, clip->bytes, clip->sha256, false};
        return true;
    }
    if (!std::strncmp(part, "eyes.", 5)) {
        const char* s = part + 5;
        const char* dot = std::strchr(s, '.');
        char style[8];
        if (!dot || size_t(dot - s) >= sizeof style) return false;
        std::memcpy(style, s, size_t(dot - s)); style[dot - s] = 0;
        const eyes::Asset* a = eyes::asset(eyes::parse_style(style), eyes::parse_state(dot + 1));
        if (!a) return false;
        shared_storage::asset_path(*a, eyes_path, sizeof eyes_path);
        out = {eyes_path, a->bytes, a->sha256, false};
        return true;
    }
    return false;
}

// On the asset install worker (PSRAM stack, prio 1): the SD check or write, then the outcome back to the Transfer.
void run_asset_job(void*)
{
    const assets::JobView v = job_view;
    const shared_storage::FileSpec f{v.spec.path, v.spec.bytes, v.spec.sha256};
    bool flag = false;
    const char* error = v.job == assets::Job::Check ? shared_storage::match_file(f, flag)
                                                    : shared_storage::install_file(f, v.bytes, v.length, flag);
    bool font = false;
    {
        std::lock_guard<std::mutex> lock(asset_mutex);
        font = transfer->job_done(error, flag, esp_timer_get_time());
    }
    if (font) ui_font::reload();             // retries the variants not loaded yet; only complete ones load
    if (error) ESP_LOGW(kTag, "asset %s: %s", v.job == assets::Job::Check ? "check" : "install", error);
}

// Each pass of the /mouth loop: the timeout, a job for the worker, then up to 4 lines (acks, results). false = the
// link is gone.
bool assets_service(hearing::HearingSocket& socket, bool speaking)
{
    if (!transfer) return true;
    assets::JobView v;
    assets::Job job;
    {
        std::lock_guard<std::mutex> lock(asset_mutex);
        transfer->tick(esp_timer_get_time(), speaking);
        job = transfer->take_job(v);
        if (job != assets::Job::None) job_view = v;
    }
    if (job != assets::Job::None && !eyes::run_storage_job(run_asset_job, nullptr)) {
        std::lock_guard<std::mutex> lock(asset_mutex);
        transfer->job_refused();
    }
    for (int i = 0; i < 4; ++i) {
        assets::Line line;
        {
            std::lock_guard<std::mutex> lock(asset_mutex);
            if (!transfer->next(line)) return true;
        }
        if (!socket.Send(line.c_str(), line.size(), false)) return false;
    }
    return true;
}

void on_asset_text(Text kind, ArduinoJson::JsonObjectConst o)
{
    const bool storage = kind == Text::AssetBegin && shared_storage::available();
    std::lock_guard<std::mutex> lock(asset_mutex);
    if (!transfer) return;
    const int64_t now = esp_timer_get_time();
    if (kind == Text::AssetBegin) transfer->begin(o, now, storage);
    else if (kind == Text::AssetEnd) transfer->end(o, now);
    else transfer->abort(o);
}

void set_state(const char* s, const std::string& err = {})
{
    std::lock_guard<std::mutex> lock(mutex);
    state = s; connected = !std::strcmp(s, "connected");
    if (!err.empty()) last_error = err;
}

void queue_locked(const Report& r)
{
    if (outbox_n == kOutbox) {              // full: drop the oldest progress, else the oldest report
        size_t drop = 0;
        for (size_t i = 0; i < outbox_n; ++i) if (outbox[i].kind == Report::Progress) { drop = i; break; }
        std::move(outbox + drop + 1, outbox + outbox_n, outbox + drop);
        --outbox_n; ++reports_dropped;
    }
    outbox[outbox_n++] = r;
}

void on_mode_result(ArduinoJson::JsonObjectConst o)
{
    bool ok = true;
    {
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (!modes_session) return;
        ok = modes_session->on_result(o, esp_timer_get_time());
        mode_publish_locked();
    }
    if (!ok) ESP_LOGW(kTag, "mode_result dropped");     // after the lock (review M3)
}

void on_message(const char* data, size_t n, bool binary)
{
    if (binary && assets::is_chunk(reinterpret_cast<const uint8_t*>(data), n)) {   // "AST1", never "SPK1"
        std::lock_guard<std::mutex> lock(asset_mutex);
        if (transfer) transfer->chunk(reinterpret_cast<const uint8_t*>(data), n, esp_timer_get_time());
        return;
    }
    if (binary) {
        std::lock_guard<std::mutex> lock(mutex);
        stream.push(reinterpret_cast<const uint8_t*>(data), n, *ring);
        return;
    }
    // Length-checked and fully parsed before any lock is taken: the player waits on `mutex`, so a 1 KB
    // snapshot never holds up the audio path. A failed parse leaves nothing to read.
    ArduinoJson::JsonDocument doc(&psram_allocator);
    const Text kind = read_text(data, n, doc);
    if (kind == Text::Other) return;
    if (kind == Text::TooLong || kind == Text::Bad) { std::lock_guard<std::mutex> lock(mutex); ++bad_messages; return; }
    if (kind == Text::ModeResult) { on_mode_result(doc.as<ArduinoJson::JsonObjectConst>()); return; }
    if (kind == Text::AssetBegin || kind == Text::AssetEnd || kind == Text::AssetAbort) {
        on_asset_text(kind, doc.as<ArduinoJson::JsonObjectConst>());
        return;
    }
    if (kind == Text::ChatStage) {          // 0.4.93 chat dots: state only; the screen task draws them
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (modes_session && modes_session->on_chat_stage(doc.as<ArduinoJson::JsonObjectConst>(), n, esp_timer_get_time())) mode_publish_locked();
        return;
    }
    bool handshake = false, speech = false;
    {
        std::lock_guard<std::mutex> lock(mutex);
        const uint32_t sid = doc["stream"] | 0u;
        if (kind == Text::Hello) {
            hello_ok = !std::strcmp(doc["codec"] | "", "opus") && (doc["frame_ms"] | 0) == kFrameMs && (doc["sample_rate"] | 0) == 24000;
            handshake = hello_ok;
        } else if (kind == Text::TtsStart) {
            stream.start(sid, *ring, [](const Report& r) { queue_locked(r); });
            ++streams; speech = true;
            if (std::strcmp(doc["codec"] | "opus", "opus") || (doc["frame_ms"] | kFrameMs) != kFrameMs) {
                Report r; r.kind = Report::Error; r.stream = sid; r.error = "unsupported_codec";
                queue_locked(r); stream.finished = true;
            }
        } else if (kind == Text::TtsEnd) {
            stream.end(sid);
        } else if (kind == Text::TtsStop) {
            stream.stop(sid, *ring);
        }
    }
    if (speech) {           // 0.4.93 chat dots: when the speech began (mode lock taken after the audio lock is released)
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (modes_session) { modes_session->on_speech_start(esp_timer_get_time()); mode_publish_locked(); }
    }
    if (handshake) {        // the ear's hello: the current mode goes up first, then every change
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (modes_session) { modes_session->link_up(); mode_publish_locked(); }
    }
}

bool send_report(hearing::HearingSocket& socket, const Report& r)
{
    ArduinoJson::JsonDocument doc(&psram_allocator);
    static constexpr const char* kTypes[] = {"tts.playing", "tts.progress", "tts.done", "tts.stopped", "tts.error"};
    doc["type"] = kTypes[r.kind]; doc["stream"] = r.stream;
    if (r.kind == Report::Error) doc["error"] = r.error;
    else {
        if (r.kind != Report::Playing) doc["played_ms"] = r.played_ms;
        if (r.kind == Report::Playing || r.kind == Report::Progress) doc["buffered_ms"] = r.buffered_ms;
        if (r.kind == Report::Progress || r.kind == Report::Done) doc["underruns"] = r.underruns;
        if (r.kind == Report::Done) doc["underrun_ms"] = r.underrun_ms;
    }
    std::string text;
    ArduinoJson::serializeJson(doc, text);
    return socket.Send(text);
}

void drop_stream_locked()
{
    if (stream.active()) { stream.stopped = true; stream.finished = true; ring->clear(); }
    outbox_n = 0; hello_ok = false;
}

// Disconnect: unsent reports and digits go; the next link sends the current state again.
void drop_modes()
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (modes_session) { modes_session->link_down(); mode_publish_locked(); }
}

// mode_reports first, then digit mode_inputs, at most 4 lines a pass. Each line is built under the mode lock
// and sent without it (the ASR task, the screen and the page only queue). false = the send failed and the link
// is gone; the line stays pending until then.
bool send_modes(hearing::HearingSocket& socket)
{
    for (int i = 0; i < 4; ++i) {
        OutLine line;
        uint32_t seq = 0;
        ModeSession::Out kind;
        {
            std::lock_guard<std::mutex> lock(mode_mutex);
            if (!modes_session) return true;
            kind = modes_session->next(esp_timer_get_time(), line, seq);
        }
        if (kind == ModeSession::Out::None) return true;
        if (!socket.Send(line.c_str(), line.size(), false)) return false;
        std::lock_guard<std::mutex> lock(mode_mutex);
        modes_session->sent(kind, seq);
        mode_publish_locked();
    }
    return true;
}

bool speaking_now();                         // below: a /mouth stream is playing (takes the audio lock briefly)

void run(void*)
{
    int backoff_ms = 2000;
    for (;;) {
        std::string url, token;
        if (!hearing::backend(url, token)) { set_state("no_backend"); vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
        const std::string configured = url;
        url = mouth_url(url);
        set_state("connecting");
        hearing::HearingSocket socket;
        const std::string authorization = "Bearer " + token;
        socket.SetHeader("Authorization", authorization.c_str());
        socket.AcceptBinary(kMaxMessage);
        socket.SetReadBudgetMs(3000);    // own task: a frame split by an RTT spike may take its time
        socket.OnData(on_message);
        bool ok = socket.Connect(url.c_str());
        if (ok) {
            ArduinoJson::JsonDocument hello;
            hello["type"] = "hello"; hello["device_id"] = device; hello["boot_id"] = boot;
            auto codecs = hello["codecs"].to<ArduinoJson::JsonArray>(); codecs.add("opus");
            hello["sample_rate"] = 24000; hello["buffer_ms"] = int(kSlots) * kFrameMs; hello["fw"] = FIRMWARE_VERSION;
            if (transfer) hello["assets"] = 1;      // 0.4.96: takes asset.begin (mouth_assets.h)
            std::string text; ArduinoJson::serializeJson(hello, text);
            ok = socket.Send(text);
        }
        const int64_t hello_by = esp_timer_get_time() + kHelloUs;
        while (ok && socket.IsConnected()) {
            socket.Poll();
            { std::lock_guard<std::mutex> lock(mutex); if (hello_ok) break; }
            if (esp_timer_get_time() > hello_by) { ok = false; break; }
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        ok = ok && socket.IsConnected();
        if (ok) {
            backoff_ms = 2000;
            { std::lock_guard<std::mutex> lock(mutex); ++connects; connected_us = esp_timer_get_time(); }
            set_state("connected");
            ESP_LOGI(kTag, "connected");
        }
        int64_t checked_us = esp_timer_get_time();
        Liveness live; live.start(checked_us);
        bool silent = false;
        while (ok && socket.IsConnected()) {
            bool got = false;
            for (int i = 0; i < 48 && socket.Poll(); ++i) got = true;
            if (got) live.seen(esp_timer_get_time());
            if (live.dead(esp_timer_get_time())) {    // no frame, not even the ear's ping, for 45 s
                silent = true; ok = false;
                { std::lock_guard<std::mutex> lock(mutex); ++silent_drops; }
                break;
            }
            Report batch[kOutbox];
            size_t n;
            { std::lock_guard<std::mutex> lock(mutex); n = outbox_n; std::copy(outbox, outbox + n, batch); outbox_n = 0; }
            for (size_t i = 0; ok && i < n; ++i) {
                ok = send_report(socket, batch[i]);
                if (ok) { std::lock_guard<std::mutex> lock(mutex); ++reports_sent; }
            }
            if (ok) ok = send_modes(socket);
            if (ok) ok = assets_service(socket, speaking_now());
            const int64_t now = esp_timer_get_time();
            if (now - checked_us > 2000000) {    // backend changed or removed: reconnect
                checked_us = now;
                std::string u, t;
                if (!hearing::backend(u, t) || u != configured || t != token) break;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        const std::string err = socket.Error();
        socket.Abort();
        { std::lock_guard<std::mutex> lock(mutex); drop_stream_locked(); }
        drop_modes();
        { std::lock_guard<std::mutex> lock(asset_mutex); if (transfer) transfer->link_down(); }
        if (!ok || !err.empty()) {
            { std::lock_guard<std::mutex> lock(mutex); ++failures; }
            set_state("retry", silent ? "silent" : err.empty() ? (ok ? "closed" : "hello") : err);
            ESP_LOGW(kTag, "link lost: %s", err.c_str());
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = std::min(backoff_ms * 2, 30000);
        } else {
            set_state("idle");
        }
    }
}
}

bool player_view(PlayerView& v)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (!ring || !stream.active()) return false;
    v.stream = stream.id; v.ended = stream.ended; v.stopped = stream.stopped;
    v.buffered = ring->size(); v.played_frames = stream.played_frames;
    return true;
}

bool player_pop(uint32_t sid, uint8_t* out, size_t& n)
{
    std::lock_guard<std::mutex> lock(mutex);
    return ring && stream.active() && stream.id == sid && !stream.stopped && ring->pop(out, n);
}

void player_played(uint32_t sid)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (stream.active() && stream.id == sid) ++stream.played_frames;
}

void player_report(const Report& r)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (r.kind == Report::Done || r.kind == Report::Stopped || r.kind == Report::Error) {
        if (stream.id == r.stream) stream.finished = true;
    }
    if (connected) queue_locked(r);
}

void init(const std::string& device_id, const std::string& boot_id)
{
    device = device_id; boot = boot_id;
    auto* storage = static_cast<uint8_t*>(heap_caps_malloc(kSlots * kPacketMax, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!storage) { set_state("no_memory"); return; }
    ring = new PacketRing(storage);
    if (void* mem = heap_caps_calloc(1, sizeof(ModeSession), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)) {
        std::lock_guard<std::mutex> lock(mode_mutex);
        modes_session = new (mem) ModeSession();      // never freed: lives as long as the link task
        modes_session->boot(esp_timer_get_time());    // power-on: talk; its "boot" report waits for the first link
        mode_publish_locked();
    } else {
        ESP_LOGE(kTag, "no memory for the mode session: modes off");
    }
    if (void* mem = heap_caps_calloc(1, sizeof(assets::Transfer), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)) {
        std::lock_guard<std::mutex> lock(asset_mutex);
        transfer = new (mem) assets::Transfer(assets::Hooks{resolve_part, psram_alloc, psram_free});   // never freed
    } else {
        ESP_LOGE(kTag, "no memory for the asset transfer: SD assets over /mouth off");
    }
    // Socket and JSON work only: no flash access, so the stack can live in PSRAM.
    if (create_psram_task(run, "mouth_link", 8192, nullptr, 4) != pdPASS) {
        ESP_LOGE(kTag, "task create failed");
        set_state("task_failed");
    }
}

namespace {
// hello is speaking: a /mouth stream is playing (from tts.start until it is done or stopped).
bool speaking_now()
{
    std::lock_guard<std::mutex> lock(mutex);
    return ring && stream.active() && !stream.stopped && !stream.finished;
}
}

bool speaking() { return speaking_now(); }

Applied mode_apply(const char* source, modes::rules::Input input, const modes::rules::Value& value)
{
    const bool speaking = speaking_now();       // the audio lock first, released before the mode lock
    Applied a;
    {
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (!modes_session) { std::snprintf(a.out.toast, sizeof a.out.toast, "NO MODES"); a.out.code = modes::rules::Code::UnknownInput; return a; }
        a = modes_session->apply(esp_timer_get_time(), source, input, value, speaking);
        mode_publish_locked();
    }
    // Logged after the lock: a backed-up console must not hold the /mouth task's hello / mode_result (review M3).
    if (a.changed) ESP_LOGI(kTag, "mode %s by %s (%s)", modes::rules::label(a.out.state), source, modes::rules::input_id(input));
    return a;
}

void mode_tick()
{
    const bool speaking = speaking_now();
    const char* back = nullptr;
    {
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (modes_session && modes_session->tick(esp_timer_get_time(), speaking)) {
            mode_publish_locked();
            back = modes::rules::label(modes_session->state());   // a constant table string: safe after the lock
        }
    }
    if (back) ESP_LOGI(kTag, "mode %s by timeout", back);
}

bool mode_view(ModeView& out)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (!modes_session) { out = ModeView{}; return false; }
    modes_session->view(out);
    return true;
}

void mode_json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (modes_session) modes_session->json(out, esp_timer_get_time());
}

uint32_t mode_version() { return mode_ver.load(std::memory_order_acquire); }

void json(ArduinoJson::JsonObject out)
{
    {
        std::lock_guard<std::mutex> lock(asset_mutex);      // released before the audio lock (never both)
        if (transfer) transfer->json(out["assets"].to<ArduinoJson::JsonObject>());
    }
    std::lock_guard<std::mutex> lock(mutex);
    out["state"] = state; out["connected"] = connected;
    out["connects"] = connects; out["failures"] = failures; out["silent_drops"] = silent_drops; out["streams"] = streams;
    if (connected) out["connected_ms"] = (esp_timer_get_time() - connected_us) / 1000;
    if (!last_error.empty()) out["last_error"] = last_error;
    out["stream"] = stream.id; out["active"] = stream.active(); out["buffered"] = ring ? ring->size() : 0;
    out["received"] = stream.received; out["stale"] = stream.stale; out["overflow"] = stream.overflow; out["too_big"] = stream.too_big;
    out["reports_sent"] = reports_sent; out["reports_dropped"] = reports_dropped; out["bad_messages"] = bad_messages;
    out["ring_bytes"] = int(kSlots * kPacketMax);
}
}
