/* SPDX-License-Identifier: MIT */
#include "mouth_link.h"
#include "mouth_liveness.h"
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

// JSON documents on /mouth keep their pool in PSRAM (a snapshot is up to 1 KB).
struct PsramAllocator final : ArduinoJson::Allocator {
    void* allocate(size_t n) override { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
    void deallocate(void* p) override { heap_caps_free(p); }
    void* reallocate(void* p, size_t n) override { return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
} psram_allocator;

// hello modes step 1: the mode state has its own lock, never the audio one (`mutex`), and the two are never
// held together. The session lives in PSRAM; `mode_ver` mirrors its version for lock-free polling (an atomic
// in internal RAM: S32C1I does not work on PSRAM).
std::mutex mode_mutex;
ModeSession* modes_session = nullptr;
std::atomic<uint32_t> mode_ver{0};
void mode_publish_locked() { mode_ver.store(modes_session->version(), std::memory_order_release); }

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

void on_mode_message(Text kind, ArduinoJson::JsonObjectConst o)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (!modes_session) return;
    if (kind == Text::Mode) {
        if (modes_session->on_mode(o) == modes::Parse::Bad) ESP_LOGW(kTag, "bad mode snapshot");
    } else {
        modes_session->on_result(o);
    }
    mode_publish_locked();
}

void on_message(const char* data, size_t n, bool binary)
{
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
    if (kind == Text::Mode || kind == Text::ModeResult) { on_mode_message(kind, doc.as<ArduinoJson::JsonObjectConst>()); return; }
    bool handshake = false;
    {
        std::lock_guard<std::mutex> lock(mutex);
        const uint32_t sid = doc["stream"] | 0u;
        if (kind == Text::Hello) {
            hello_ok = !std::strcmp(doc["codec"] | "", "opus") && (doc["frame_ms"] | 0) == kFrameMs && (doc["sample_rate"] | 0) == 24000;
            handshake = hello_ok;
        } else if (kind == Text::TtsStart) {
            stream.start(sid, *ring, [](const Report& r) { queue_locked(r); });
            ++streams;
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
    if (handshake) {        // the ear's hello: inputs are taken, the next snapshot is applied whatever its seq
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

// Disconnect: queued inputs go (the ear answers none of them); an unsent mode_applied waits for the next link.
void drop_modes()
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (modes_session) { modes_session->link_down(); mode_publish_locked(); }
}

// Pending mode_applied first, then queued mode_inputs, at most 4 lines a pass. Each line is built under the
// mode lock and sent without it (the ASR task and the screen only queue). false = the send failed and the
// link is gone; the line stays pending.
bool send_modes(hearing::HearingSocket& socket)
{
    for (int i = 0; i < 4; ++i) {
        modes::Line line;
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
    } else {
        ESP_LOGE(kTag, "no memory for the mode session: modes off");
    }
    // Socket and JSON work only: no flash access, so the stack can live in PSRAM.
    if (create_psram_task(run, "mouth_link", 8192, nullptr, 4) != pdPASS) {
        ESP_LOGE(kTag, "task create failed");
        set_state("task_failed");
    }
}

uint32_t mode_link_send_input(const char* source, const char* input, int value)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    return modes_session ? modes_session->push_input(esp_timer_get_time(), source, input, value) : 0;
}

uint32_t mode_link_send_select(const char* mode, const char* dir, const char* source)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    return modes_session ? modes_session->push_select(esp_timer_get_time(), source, mode, dir) : 0;
}

bool mode_view(ModeView& out)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (!modes_session) { out = ModeView{}; return false; }
    modes_session->view(out);
    return true;
}

uint32_t mode_version() { return mode_ver.load(std::memory_order_acquire); }

void json(ArduinoJson::JsonObject out)
{
    {
        std::lock_guard<std::mutex> lock(mode_mutex);
        if (modes_session) modes_session->json(out["mode"].to<ArduinoJson::JsonObject>());
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
