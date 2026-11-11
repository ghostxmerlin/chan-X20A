/* SPDX-License-Identifier: MIT */
#include "mouth_link.h"
#include "hearing.h"
#include "hearing_socket.h"
#include "psram_task.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <algorithm>
#include <cstring>
#include <mutex>

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
uint32_t connects = 0, failures = 0, streams = 0, reports_sent = 0, reports_dropped = 0, bad_messages = 0;
int64_t connected_us = 0;

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

void on_message(const char* data, size_t n, bool binary)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (binary) { stream.push(reinterpret_cast<const uint8_t*>(data), n, *ring); return; }
    ArduinoJson::JsonDocument doc;
    if (ArduinoJson::deserializeJson(doc, data, n) || !doc.is<ArduinoJson::JsonObject>()) { ++bad_messages; return; }
    const char* type = doc["type"] | "";
    const uint32_t sid = doc["stream"] | 0u;
    if (!std::strcmp(type, "hello")) {
        hello_ok = !std::strcmp(doc["codec"] | "", "opus") && (doc["frame_ms"] | 0) == kFrameMs && (doc["sample_rate"] | 0) == 24000;
    } else if (!std::strcmp(type, "tts.start")) {
        stream.start(sid, *ring, [](const Report& r) { queue_locked(r); });
        ++streams;
        if (std::strcmp(doc["codec"] | "opus", "opus") || (doc["frame_ms"] | kFrameMs) != kFrameMs) {
            Report r; r.kind = Report::Error; r.stream = sid; r.error = "unsupported_codec";
            queue_locked(r); stream.finished = true;
        }
    } else if (!std::strcmp(type, "tts.end")) {
        stream.end(sid);
    } else if (!std::strcmp(type, "tts.stop")) {
        stream.stop(sid, *ring);
    }
}

bool send_report(hearing::HearingSocket& socket, const Report& r)
{
    ArduinoJson::JsonDocument doc;
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
        while (ok && socket.IsConnected()) {
            for (int i = 0; i < 48 && socket.Poll(); ++i) {}
            Report batch[kOutbox];
            size_t n;
            { std::lock_guard<std::mutex> lock(mutex); n = outbox_n; std::copy(outbox, outbox + n, batch); outbox_n = 0; }
            for (size_t i = 0; ok && i < n; ++i) {
                ok = send_report(socket, batch[i]);
                if (ok) { std::lock_guard<std::mutex> lock(mutex); ++reports_sent; }
            }
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
        if (!ok || !err.empty()) {
            { std::lock_guard<std::mutex> lock(mutex); ++failures; }
            set_state("retry", err.empty() ? (ok ? "closed" : "hello") : err);
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
    // Socket and JSON work only: no flash access, so the stack can live in PSRAM.
    if (create_psram_task(run, "mouth_link", 8192, nullptr, 4) != pdPASS) {
        ESP_LOGE(kTag, "task create failed");
        set_state("task_failed");
    }
}

void json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(mutex);
    out["state"] = state; out["connected"] = connected;
    out["connects"] = connects; out["failures"] = failures; out["streams"] = streams;
    if (connected) out["connected_ms"] = (esp_timer_get_time() - connected_us) / 1000;
    if (!last_error.empty()) out["last_error"] = last_error;
    out["stream"] = stream.id; out["active"] = stream.active(); out["buffered"] = ring ? ring->size() : 0;
    out["received"] = stream.received; out["stale"] = stream.stale; out["overflow"] = stream.overflow; out["too_big"] = stream.too_big;
    out["reports_sent"] = reports_sent; out["reports_dropped"] = reports_dropped; out["bad_messages"] = bad_messages;
    out["ring_bytes"] = int(kSlots * kPacketMax);
}
}
