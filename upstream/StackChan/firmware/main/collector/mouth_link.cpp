/* SPDX-License-Identifier: MIT */
#include "mouth_link.h"
#include "burst_indicator.h"
#include "mouth_liveness.h"
#include "hearing.h"
#include "hearing_socket.h"
#include "psram_task.h"
#include "vision_snap.h"
#include "vision_uplink.h"
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
constexpr int64_t kLookHoldUs = 800000;      // 0.4.98: crowd's tap report waits at most this for fresh faces
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

// 0.5.00 burst indicator: the latest burst frame as one word (burst_indicator.h). Only the /mouth task writes it (a
// plain load + store is enough); the LVGL timer reads it. Internal RAM: an atomic (S32C1I does not work on PSRAM).
std::atomic<uint32_t> burst_now{0};
std::atomic<uint32_t> wake_n{0};          // 0.5.06: ASR "hello" count (+ the interrupted flag of the last one)
std::atomic<bool> wake_interrupted{false};
std::atomic<const char*> wake_word{"hello"};   // a static string of asr_protocol.h
uint32_t wake_sent = 0;                   // run() task only
std::atomic<uint32_t> order_n{0};         // 0.5.07: Unit ASR command words (direct orders), the last one's word
std::atomic<const char*> order_word{""};
uint32_t order_sent = 0;                  // run() task only
uint32_t burst_frames = 0;                   // accepted burst frames (under `mutex`, for json())
void burst_publish(const burst::Frame& f, bool lost)
{
    burst_now.store(burst::publish(burst_now.load(std::memory_order_relaxed), f, lost), std::memory_order_release);
}
// The /mouth link dropped: an indicator that is on goes off (Why::Link); nothing is published when it is already off.
void burst_link_down()
{
    if (burst::unpack(burst_now.load(std::memory_order_relaxed)).on) burst_publish(burst::Frame{}, true);
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
    if (kind == Text::Snap) {               // 0.4.99 视觉爆发: hand it to the vision task (short locks only)
        uint32_t id = 0;
        if (!snap::parse(doc.as<ArduinoJson::JsonObjectConst>(), n, id)) { std::lock_guard<std::mutex> lock(mutex); ++bad_messages; return; }
        vision_snap_request(id);
        return;
    }
    if (kind == Text::Burst) {              // 0.5.00 视觉爆发 indicator: one word for the screen, nothing sent back
        burst::Frame f;
        const bool ok = burst::parse(doc.as<ArduinoJson::JsonObjectConst>(), n, f);
        { std::lock_guard<std::mutex> lock(mutex); if (ok) ++burst_frames; else ++bad_messages; }
        if (ok) burst_publish(f, false);
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
        if (modes_session) { modes_session->link_up(esp_timer_get_time()); mode_publish_locked(); }
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

// 0.4.99: the vision task's snap_result lines (and the 2 s deadline's timeouts), at most 4 a pass, sent without
// any lock held. false = the send failed and the link is gone (the link drop resets the snap).
// 0.5.06: the last ASR wake not yet reported (one line; several wakes in a pass are one report with n).
bool send_wake(hearing::HearingSocket& socket)
{
    const uint32_t n = wake_n.load(std::memory_order_acquire);
    if (n == wake_sent) return true;
    char line[96];
    const int len = std::snprintf(line, sizeof line, "{\"type\":\"asr_wake\",\"word\":\"%s\",\"n\":%lu,\"interrupted\":%s}",
                                  wake_word.load(), static_cast<unsigned long>(n), wake_interrupted.load() ? "true" : "false");
    if (!socket.Send(line, static_cast<size_t>(len), false)) return false;
    wake_sent = n;
    return true;
}

bool send_order(hearing::HearingSocket& socket)
{
    const uint32_t n = order_n.load(std::memory_order_acquire);
    if (n == order_sent) return true;
    char line[96];
    const int len = std::snprintf(line, sizeof line, "{\"type\":\"asr_order\",\"word\":\"%s\",\"n\":%lu}",
                                  order_word.load(), static_cast<unsigned long>(n));
    if (!socket.Send(line, static_cast<size_t>(len), false)) return false;
    order_sent = n;
    return true;
}

bool send_snaps(hearing::HearingSocket& socket)
{
    for (int i = 0; i < 4; ++i) {
        char line[snap::kLineMax];
        size_t n = 0;
        if (!vision_snap_take_result(line, sizeof line, n)) return true;
        if (!socket.Send(line, n, false)) return false;
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
            wake_sent = wake_n.load(std::memory_order_acquire);   // a wake from before this link is stale: not sent
            order_sent = order_n.load(std::memory_order_acquire);
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
            if (ok) ok = send_snaps(socket);
            if (ok) ok = send_wake(socket);
            if (ok) ok = send_order(socket);
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
        vision_snap_link_down();
        burst_link_down();
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
        // power-on (0.4.96, owner 2026-11-15): 围观 (crowd), and 9 (全开) to mind behind the "boot" report of the
        // first link; both wait for that link. The robot half of the 9 is the main loop's, at boot.
        modes_session->boot(esp_timer_get_time(), modes::rules::Mode::Crowd, 9);
        modes_session->set_look_hold(kLookHoldUs);   // 0.4.98: crowd's tap waits up to 0.8 s for the M12 look
        mode_publish_locked();
    } else {
        ESP_LOGE(kTag, "no memory for the mode session: modes off");
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

void asr_wake(const char* word)
{
    wake_word.store(word && *word ? word : "hello");
    bool stopped = false;
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (stream.active() && !stream.stopped) { stream.stop(stream.id, *ring); stopped = true; }
    }
    wake_interrupted.store(stopped);
    wake_n.fetch_add(1, std::memory_order_acq_rel);
    ESP_LOGI(kTag, "asr wake: %s%s", wake_word.load(), stopped ? " (stopped the speech)" : "");
}
uint32_t asr_wakes() { return wake_n.load(std::memory_order_acquire); }
void asr_order(const char* word)
{
    order_word.store(word && *word ? word : "?");
    order_n.fetch_add(1, std::memory_order_acq_rel);
}

namespace {
std::atomic<void (*)(bool)> senses_hook{nullptr};
std::atomic<void (*)(uint32_t)> look_hook{nullptr};
}
void set_senses_hook(void (*hook)(bool on)) { senses_hook.store(hook); }
void set_look_hook(void (*hook)(uint32_t token)) { look_hook.store(hook); }

void mode_look_done(uint32_t token)
{
    std::lock_guard<std::mutex> lock(mode_mutex);
    if (modes_session && modes_session->release_look(token)) mode_publish_locked();
}

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
    // 0.4.96: hello's own 0 / 9 also switch its own senses, online or not (the hook only queues for the main loop).
    if (a.to_mind && (value.digit == 0 || value.digit == 9))
        if (auto hook = senses_hook.load()) hook(value.digit == 9);
    // 0.4.98: crowd's tap (look): the M12 asks who / refreshes attributes, then lets the held report go
    // (mode_look_done); without a hook the report goes when the hold ends.
    if (a.look) {
        if (auto hook = look_hook.load()) hook(a.look_token);
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
uint32_t burst_word() { return burst_now.load(std::memory_order_acquire); }

void json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(mutex);
    out["state"] = state; out["connected"] = connected;
    out["connects"] = connects; out["failures"] = failures; out["silent_drops"] = silent_drops; out["streams"] = streams;
    if (connected) out["connected_ms"] = (esp_timer_get_time() - connected_us) / 1000;
    if (!last_error.empty()) out["last_error"] = last_error;
    out["stream"] = stream.id; out["active"] = stream.active(); out["buffered"] = ring ? ring->size() : 0;
    out["received"] = stream.received; out["stale"] = stream.stale; out["overflow"] = stream.overflow; out["too_big"] = stream.too_big;
    out["reports_sent"] = reports_sent; out["reports_dropped"] = reports_dropped; out["bad_messages"] = bad_messages;
    out["ring_bytes"] = int(kSlots * kPacketMax);
    const burst::Word b = burst::unpack(burst_now.load(std::memory_order_acquire));
    auto bo = out["burst"].to<ArduinoJson::JsonObject>();     // 0.5.00: the last burst frame as received
    bo["on"] = b.on; bo["left_s"] = b.left_s; bo["looks"] = b.looks; bo["lost"] = b.lost; bo["frames"] = burst_frames;
}
}
