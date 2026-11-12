/* SPDX-License-Identifier: MIT */
#include "vision_uplink.h"
#include "camera_frames.h"
#include "hearing.h"
#include "hearing_socket.h"
#include "m12_unit.h"
#include "psram_task.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <algorithm>
#include <cstring>
#include <mutex>

namespace collector {
namespace {
constexpr const char* kTag = "vision_up";
constexpr int64_t kPingUs = 15000000;      // keeps the server's 60 s idle timeout away
constexpr int64_t kIdleCloseUs = 30000000; // M12 offline and nothing queued for 30 s → close
constexpr size_t kBatch = 8;

std::mutex mutex;
std::string device, boot;
std::string state = "idle", last_error;
bool connected = false;
uint32_t connects = 0, failures = 0, sent = 0;
int64_t connected_us = 0, last_sent_us = 0;
// Camera frames (0.4.75): sent when the picture changed and 10 s passed, plus the first after connect.
constexpr size_t kFrameMax = 32768;          // whole binary message; larger frames are skipped
uint32_t frames_sent = 0, frames_too_big = 0, frames_no_memory = 0;
int last_frame_diff = -1;
int64_t last_frame_us = 0;
int64_t frames_hold_until_us = 0;           // after a failed frame send (frame_change.h kFrameFailHoldUs)
uint32_t frames_failed = 0;

void set_state(const char* s, const std::string& err = {})
{
    std::lock_guard<std::mutex> lock(mutex);
    state = s;
    connected = !std::strcmp(s, "connected");
    if (!err.empty()) last_error = err;
}

bool send_doc(hearing::HearingSocket& socket, ArduinoJson::JsonDocument& doc)
{
    std::string text;
    ArduinoJson::serializeJson(doc, text);
    return socket.Send(text);
}

void fill(ArduinoJson::JsonDocument& msg, const M12VisionEvent& v, uint32_t seq)
{
    msg["type"] = "vision"; msg["event"] = m12_vision_kind_name(v.kind); msg["seq"] = seq;
    msg["t_us"] = v.t_us; msg["now_us"] = esp_timer_get_time();
    if (v.f) msg["f"] = v.f;
    if (v.face >= 0) msg["face"] = v.face;
    if (v.person >= 0) msg["person"] = v.person;
    if (v.w > 0) {
        auto box = msg["box"].to<ArduinoJson::JsonArray>();
        box.add(v.x); box.add(v.y); box.add(v.w); box.add(v.h);
        msg["prob"] = v.prob;
    }
    if (v.sim >= 0) msg["sim"] = v.sim;
    if (v.gender) msg["gender"] = std::string(1, v.gender);
    if (v.age >= 0) msg["age"] = v.age;
    if (v.age_group != M12AgeGroup::none) msg["age_group"] = m12_age_group_name(v.age_group);
    if (v.name[0]) msg["name"] = std::string(v.name);
    if (v.reason[0]) msg["reason"] = std::string(v.reason);
}

bool wanted() { return m12_snapshot().online || m12_vision_pending() > 0 || camera_streaming(); }

// "SVF1" + u16le header length + JSON header + JPEG, built in PSRAM. false only when the socket
// failed; a frame that is too big or finds no memory is skipped (counted) and the link stays.
bool send_frame(hearing::HearingSocket& socket, const CameraFrame& f, int diff, bool& sent_out)
{
    sent_out = false;
    ArduinoJson::JsonDocument head;
    head["type"] = "frame"; head["seq"] = f.seq; head["t_us"] = f.timestamp_us; head["now_us"] = esp_timer_get_time();
    head["w"] = f.width; head["h"] = f.height; head["diff"] = diff; head["bytes"] = f.length;
    std::string text;
    ArduinoJson::serializeJson(head, text);
    const size_t total = 6 + text.size() + f.length;
    if (text.size() > 512 || total > kFrameMax) {
        std::lock_guard<std::mutex> lock(mutex); ++frames_too_big;
        return true;
    }
    auto* buf = static_cast<uint8_t*>(heap_caps_malloc(total, MALLOC_CAP_SPIRAM));
    if (!buf) {
        std::lock_guard<std::mutex> lock(mutex); ++frames_no_memory;
        return true;
    }
    std::memcpy(buf, "SVF1", 4);
    buf[4] = static_cast<uint8_t>(text.size() & 0xff); buf[5] = static_cast<uint8_t>(text.size() >> 8);
    std::memcpy(buf + 6, text.data(), text.size());
    std::memcpy(buf + 6 + text.size(), f.bytes, f.length);
    const bool ok = socket.Send(buf, total, true);
    heap_caps_free(buf);
    sent_out = ok;
    return ok;
}

void run(void*)
{
    int backoff_ms = 2000;
    for (;;) {
        std::string url, token;
        if (!hearing::backend(url, token) || !wanted()) {
            set_state(url.empty() ? "no_backend" : "idle");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        url = vision_url(url);
        set_state("connecting");
        hearing::HearingSocket socket;
        const std::string authorization = "Bearer " + token;
        socket.SetHeader("Authorization", authorization.c_str());
        socket.OnData([](const char*, size_t, bool) {});   // the server only answers errors
        if (!socket.Connect(url.c_str())) {
            { std::lock_guard<std::mutex> lock(mutex); ++failures; }
            set_state("retry", socket.Error());
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = std::min(backoff_ms * 2, 30000);
            continue;
        }
        bool ok;
        {
            ArduinoJson::JsonDocument hello;
            hello["type"] = "hello"; hello["source"] = "stackchan-" + device;
            hello["device"]["device_id"] = device; hello["device"]["boot_id"] = boot;
            hello["device"]["firmware"] = FIRMWARE_VERSION; hello["device"]["protocol"] = "soul-vision/1";
            ok = send_doc(socket, hello);
        }
        if (ok) {
            backoff_ms = 2000;
            { std::lock_guard<std::mutex> lock(mutex); ++connects; connected_us = esp_timer_get_time(); }
            set_state("connected");
            ESP_LOGI(kTag, "connected");
        }
        int64_t last_tx = esp_timer_get_time(), idle_since = 0;
        FrameThumb last_thumb;                 // reset per connection: the first frame always goes
        bool have_last = false;
        uint32_t last_seen_seq = 0;
        int64_t last_frame_sent_us = 0;
        while (ok && socket.IsConnected()) {
            socket.Poll();
            M12VisionEvent events[kBatch];
            uint32_t seq[kBatch];
            const size_t n = m12_peek_vision_events(events, seq, kBatch);
            for (size_t i = 0; ok && i < n; ++i) {
                ArduinoJson::JsonDocument msg;
                fill(msg, events[i], seq[i]);
                ok = send_doc(socket, msg);
                if (ok) {                     // only what reached the socket leaves the queue
                    m12_commit_vision_events(seq[i]);
                    std::lock_guard<std::mutex> lock(mutex); ++sent; last_sent_us = esp_timer_get_time();
                }
            }
            bool frame_sent = false;
            if (ok && camera_streaming() && !frame_held(esp_timer_get_time(), frames_hold_until_us)) {
                const auto frame = camera_latest_frame();
                if (frame && frame->seq != last_seen_seq && frame->bytes && frame->length) {
                    last_seen_seq = frame->seq;
                    const int diff = frame_changed_cells(frame->thumb, last_thumb);   // reported as "diff" (cells)
                    if (frame_should_send(have_last, diff, esp_timer_get_time() - last_frame_sent_us)) {
                        ok = send_frame(socket, *frame, diff, frame_sent);
                        if (!ok) {
                            frames_hold_until_us = esp_timer_get_time() + kFrameFailHoldUs;
                            std::lock_guard<std::mutex> lock(mutex); ++frames_failed;
                        }
                        if (frame_sent) {
                            have_last = true; last_thumb = frame->thumb; last_frame_sent_us = esp_timer_get_time();
                            std::lock_guard<std::mutex> lock(mutex);
                            ++frames_sent; last_frame_diff = diff; last_frame_us = last_frame_sent_us;
                        }
                    }
                }
            }
            const int64_t now = esp_timer_get_time();
            if (n || frame_sent) last_tx = now;
            if (ok && now - last_tx >= kPingUs) {
                ArduinoJson::JsonDocument ping; ping["type"] = "ping";
                ok = send_doc(socket, ping); last_tx = now;
            }
            if (!wanted()) {
                if (!idle_since) idle_since = now;
                if (now - idle_since >= kIdleCloseUs) break;
            } else {
                idle_since = 0;
            }
            if (!n) vTaskDelay(pdMS_TO_TICKS(100));
        }
        const std::string err = ok && socket.IsConnected() ? std::string() : socket.Error();
        socket.Abort();
        if (!err.empty() || !ok) {
            { std::lock_guard<std::mutex> lock(mutex); ++failures; }
            set_state("retry", err.empty() ? "send" : err);
            ESP_LOGW(kTag, "link lost: %s", err.c_str());
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = std::min(backoff_ms * 2, 30000);
        } else {
            set_state("idle");
        }
    }
}
}

void vision_uplink_init(const std::string& device_id, const std::string& boot_id)
{
    device = device_id; boot = boot_id;
    // Socket and JSON work only: no flash access, so the stack can live in PSRAM.
    if (create_psram_task(run, "vision_up", 8192, nullptr, 3) != pdPASS) {
        ESP_LOGE(kTag, "task create failed");
        set_state("task_failed");
    }
}

void vision_uplink_json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(mutex);
    out["state"] = state; out["connected"] = connected;
    out["connects"] = connects; out["failures"] = failures; out["sent"] = sent;
    if (connected) out["connected_ms"] = (esp_timer_get_time() - connected_us) / 1000;
    if (last_sent_us) out["last_sent_ms_ago"] = (esp_timer_get_time() - last_sent_us) / 1000;
    if (!last_error.empty()) out["last_error"] = last_error;
    out["frames_sent"] = frames_sent; out["frames_too_big"] = frames_too_big; out["frames_no_memory"] = frames_no_memory;
    out["frames_failed"] = frames_failed;
    if (frame_held(esp_timer_get_time(), frames_hold_until_us)) out["frames_held_ms"] = (frames_hold_until_us - esp_timer_get_time()) / 1000;
    if (last_frame_diff >= 0) out["last_frame_diff"] = last_frame_diff;
    if (last_frame_us) out["last_frame_age_ms"] = (esp_timer_get_time() - last_frame_us) / 1000;
}
}
