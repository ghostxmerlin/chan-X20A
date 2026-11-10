/* SPDX-License-Identifier: MIT */
#include "vision_uplink.h"
#include "hearing.h"
#include "hearing_socket.h"
#include "m12_unit.h"
#include "psram_task.h"
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

bool wanted() { return m12_snapshot().online || m12_vision_pending() > 0; }

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
            const int64_t now = esp_timer_get_time();
            if (n) last_tx = now;
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
}
}
