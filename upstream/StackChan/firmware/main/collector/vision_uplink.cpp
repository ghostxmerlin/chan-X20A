/* SPDX-License-Identifier: MIT */
#include "vision_uplink.h"
#include "body_sense.h"
#include "camera_frames.h"
#include "hearing.h"
#include "hearing_socket.h"
#include "m12_unit.h"
#include "psram_task.h"
#include "vision_snap.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <mutex>
#include <new>

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
// Camera frames (0.4.75): sent when the picture changed and 1 s passed (0.4.98; was 10 s), plus the first after connect.
constexpr size_t kFrameMax = 32768;          // whole binary message; larger frames are skipped
uint32_t frames_sent = 0, frames_too_big = 0, frames_no_memory = 0;
int last_frame_diff = -1;
int64_t last_frame_us = 0;
int64_t frames_hold_until_us = 0;           // after a failed frame send (frame_change.h kFrameFailHoldUs)
uint32_t frames_failed = 0;
uint32_t frames_deferred = 0;               // 0.4.98: passes that skipped a frame for queued M12 events / a look
// 0.4.99 视觉爆发 snap (vision_snap.h): the slot lives in PSRAM behind its own lock (never held with `mutex`);
// snap_wake (a binary semaphore, internal RAM) lets an accepted snap cut the task's 100 ms idle wait short.
std::mutex snap_mutex;
snap::Slot* snap_slot = nullptr;
SemaphoreHandle_t snap_wake = nullptr;

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

// ev: the m12d event of an m12_ev entry (its fields go flat into the message, "ev" = its type).
void fill(ArduinoJson::JsonDocument& msg, const M12VisionEvent& v, uint32_t seq, const M12Event* ev = nullptr)
{
    msg["type"] = "vision"; msg["event"] = m12_vision_kind_name(v.kind); msg["seq"] = seq;
    msg["t_us"] = v.t_us; msg["now_us"] = esp_timer_get_time();
    if (v.f) msg["f"] = v.f;
    if (v.kind == M12VisionKind::m12_ev) {
        if (ev) m12_event_json(*ev, msg.as<ArduinoJson::JsonObject>());
        return;
    }
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

// 0.5.12: the body sense goes once a second whatever the M12 does, so the link stays up whenever a backend is set
// (it used to close 30 s after the M12 went offline with nothing queued).
bool wanted() { return true; }

float r2(float v) { return std::round(v * 100.0f) / 100.0f; }

// Body sense (0.5.12, body_sense.h): the events first, then the window's summary when one is due. false only when
// the socket failed (a taken event is not put back).
bool send_body(hearing::HearingSocket& socket, bool& sent_any)
{
    body::Event e;
    for (int i = 0; i < 4 && body_take_event(e); ++i) {
        ArduinoJson::JsonDocument msg;
        msg["type"] = "body_ev"; msg["ev"] = body::ev_name(e.ev); msg["seq"] = e.seq;
        msg["t_us"] = e.t_us; msg["now_us"] = esp_timer_get_time();
        if (e.held_ms >= 0) msg["held_ms"] = e.held_ms;
        if (e.prox >= 0) msg["prox"] = e.prox;
        if (e.peak_g > 0) msg["peak_g"] = r2(e.peak_g);
        if (e.peak_dps > 0) msg["peak_dps"] = std::round(e.peak_dps);
        if (!send_doc(socket, msg)) return false;
        sent_any = true;
    }
    body::Summary s;
    // the window's raw samples (0.5.14): PSRAM, once; without it the summary still goes, without raw
    static body::RawSample* raw = static_cast<body::RawSample*>(
        heap_caps_calloc(body::kRawMax, sizeof(body::RawSample), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    int raw_n = 0;
    int64_t raw_t0 = 0;
    if (!body_take_summary(esp_timer_get_time(), s, raw, raw ? &raw_n : nullptr, &raw_t0)) return true;
    ArduinoJson::JsonDocument msg;
    msg["type"] = "body"; msg["seq"] = s.seq; msg["t_us"] = s.t_us; msg["now_us"] = esp_timer_get_time();
    msg["n"] = s.samples;
    if (s.imu) {
        msg["pitch"] = r2(s.pitch_deg); msg["roll"] = r2(s.roll_deg); msg["acc_g"] = r2(s.acc_g);
        msg["dev_g"] = r2(s.dev_max_g); msg["gyro"] = std::round(s.gyro_max_dps); msg["moved"] = r2(s.moved);
    }
    msg["still"] = s.still; msg["held"] = s.held; msg["self"] = s.self_moving;
    if (s.prox >= 0) { msg["prox"] = s.prox; msg["prox_base"] = s.prox_base; msg["prox_max"] = s.prox_max; msg["near"] = s.near; }
    if (s.lux >= 0) msg["lux"] = r2(s.lux);
    if (s.mag) {
        auto m = msg["mag"].to<ArduinoJson::JsonArray>();
        for (float v : s.mag_ut) m.add(r2(v));
    }
    if (!s.still && raw && raw_n > 0) {          // moving (or the head turning): the 50 Hz samples go too
        msg["raw_t0_us"] = raw_t0;
        if (s.raw_over) msg["raw_over"] = s.raw_over;
        auto arr = msg["raw"].to<ArduinoJson::JsonArray>();
        for (int i = 0; i < raw_n; ++i) {
            const auto& r = raw[i];
            auto row = arr.add<ArduinoJson::JsonArray>();
            row.add(r.dt_ms);
            for (int k = 0; k < 3; ++k) row.add(r.a_mg[k]);
            for (int k = 0; k < 3; ++k) row.add(r.g_dps10[k]);
            row.add(r.self);
        }
    }
    if (!send_doc(socket, msg)) return false;
    sent_any = true;
    return true;
}

// "SVF1" + u16le header length + JSON header + JPEG, built in PSRAM. false only when the socket
// failed; a frame that is too big or finds no memory is skipped (counted) and the link stays.
// A snap's frame (0.4.99) carries "snap":<id> in the header; snap_id < 0 = an ordinary frame.
bool send_frame(hearing::HearingSocket& socket, const CameraFrame& f, int diff, bool& sent_out, int64_t snap_id = -1)
{
    sent_out = false;
    ArduinoJson::JsonDocument head;
    head["type"] = "frame"; head["seq"] = f.seq; head["t_us"] = f.timestamp_us; head["now_us"] = esp_timer_get_time();
    head["w"] = f.width; head["h"] = f.height; head["diff"] = diff; head["bytes"] = f.length;
    if (snap_id >= 0) head["snap"] = static_cast<uint32_t>(snap_id);
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
    // m12d event payloads of one batch (~0.25 KB each): PSRAM, not this task's stack. Without it m12_ev still goes,
    // with its type unknown to the server ("ev" missing).
    auto* batch_ev = static_cast<M12Event*>(heap_caps_calloc(kBatch, sizeof(M12Event), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (batch_ev) for (size_t i = 0; i < kBatch; ++i) new (&batch_ev[i]) M12Event();
    else ESP_LOGE(kTag, "no memory for the event batch");
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
            const size_t n = m12_peek_vision_events(events, seq, kBatch, batch_ev);
            for (size_t i = 0; ok && i < n; ++i) {
                ArduinoJson::JsonDocument msg;
                fill(msg, events[i], seq[i], batch_ev && events[i].kind == M12VisionKind::m12_ev ? &batch_ev[i] : nullptr);
                ok = send_doc(socket, msg);
                if (ok) {                     // only what reached the socket leaves the queue
                    m12_commit_vision_events(seq[i]);
                    std::lock_guard<std::mutex> lock(mutex); ++sent; last_sent_us = esp_timer_get_time();
                }
            }
            bool body_sent = false;
            if (ok) ok = send_body(socket, body_sent);
            bool frame_sent = false;
            // 0.4.98 (review 2026-11-15): M12 events first. A frame (up to 3 s to send) never goes while events
            // are still queued or a crowd-tap look is running; it is skipped, not queued: the next pass takes the
            // newest one. At most one frame per pass.
            const bool look = m12_look_active();
            const bool gate = frame_gate_open(m12_vision_pending(), look);
            // 0.4.99 snap (视觉爆发): an accepted snap takes this pass's frame, behind the same gate (so after the
            // events drained above), outside the change rule and the 1 s gap: the newest frame captured at or after
            // the request. A frame too big / without memory: the next fresh one tries until the 2 s deadline.
            snap::Ticket ticket;
            bool snapping;
            { std::lock_guard<std::mutex> lock(snap_mutex); snapping = snap_slot && snap_slot->waiting(esp_timer_get_time(), ticket); }
            if (ok && !gate && (snapping || camera_streaming())) { std::lock_guard<std::mutex> lock(mutex); ++frames_deferred; }
            const auto note_sent = [&](const CameraFrame& f, int diff) {
                have_last = true; last_thumb = f.thumb; last_frame_sent_us = esp_timer_get_time();
                std::lock_guard<std::mutex> lock(mutex);
                ++frames_sent; last_frame_diff = diff; last_frame_us = last_frame_sent_us;
            };
            const auto note_failed = [&] {
                std::lock_guard<std::mutex> lock(mutex);
                frames_hold_until_us = esp_timer_get_time() + kFrameFailHoldUs; ++frames_failed;
            };
            if (ok && snapping && !camera_streaming()) {
                std::lock_guard<std::mutex> lock(snap_mutex);    // camera switched off since the accept (0.5.01: a frame
                snap_slot->failed(ticket, snap::Why::CameraOff); // hold no longer fails a snap: snap_uplink_up)
            } else if (ok && snapping && gate) {
                const auto frame = camera_latest_frame();
                if (frame && frame->bytes && frame->length && snap::fresh(frame->timestamp_us, ticket.since_us)) {
                    last_seen_seq = frame->seq;
                    const int diff = frame_changed_cells(frame->thumb, last_thumb);
                    ok = send_frame(socket, *frame, diff, frame_sent, ticket.id);
                    {
                        std::lock_guard<std::mutex> lock(snap_mutex);
                        if (frame_sent) snap_slot->sent(ticket, esp_timer_get_time(), frame->seq, static_cast<uint32_t>(frame->length));
                        else snap_slot->failed(ticket, snap::Why::NoUplink);   // also too big / no memory: fail now, not at 2 s
                    }
                    if (!ok) note_failed();
                    if (frame_sent) note_sent(*frame, diff);
                }
            } else if (ok && gate && camera_streaming() && !frame_held(esp_timer_get_time(), frames_hold_until_us)) {
                const auto frame = camera_latest_frame();
                if (frame && frame->seq != last_seen_seq && frame->bytes && frame->length) {
                    last_seen_seq = frame->seq;
                    const int diff = frame_changed_cells(frame->thumb, last_thumb);   // reported as "diff" (cells)
                    if (frame_should_send(have_last, diff, esp_timer_get_time() - last_frame_sent_us)) {
                        ok = send_frame(socket, *frame, diff, frame_sent);
                        if (!ok) note_failed();
                        if (frame_sent) note_sent(*frame, diff);
                    }
                }
            }
            const int64_t now = esp_timer_get_time();
            if (n || frame_sent || body_sent) last_tx = now;
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
            if (!n) {   // a look waits for its events, a snap for a fresh frame: poll faster; a new snap wakes it
                const TickType_t wait = pdMS_TO_TICKS(look || snapping ? 20 : 100);
                if (snap_wake) xSemaphoreTake(snap_wake, wait); else vTaskDelay(wait);
            }
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
    // 0.4.99 snap: the slot in PSRAM (never freed); without it /mouth snaps are ignored (no answer: the ear times out).
    if (void* mem = heap_caps_calloc(1, sizeof(snap::Slot), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)) {
        std::lock_guard<std::mutex> lock(snap_mutex);
        snap_slot = new (mem) snap::Slot();
    } else {
        ESP_LOGE(kTag, "no memory for the snap slot: snaps off");
    }
    snap_wake = xSemaphoreCreateBinary();   // a FreeRTOS sync object: internal RAM
    // Socket and JSON work only: no flash access, so the stack can live in PSRAM.
    if (create_psram_task(run, "vision_up", 8192, nullptr, 3) != pdPASS) {
        ESP_LOGE(kTag, "task create failed");
        set_state("task_failed");
    }
}

void vision_snap_request(uint32_t id)
{
    const int64_t now = esp_timer_get_time();
    bool up;
    { std::lock_guard<std::mutex> lock(mutex); up = snap_uplink_up(connected, frame_held(now, frames_hold_until_us)); }
    const bool camera = camera_streaming();
    snap::Why why;
    {
        std::lock_guard<std::mutex> lock(snap_mutex);
        if (!snap_slot) return;
        why = snap_slot->request(id, now, camera, up);
    }
    if (why == snap::Why::None) { if (snap_wake) xSemaphoreGive(snap_wake); }
    else ESP_LOGI(kTag, "snap %lu refused: %s", (unsigned long)id, snap::why_name(why));   // after the locks
}

bool vision_snap_take_result(char* line, size_t cap, size_t& n)
{
    snap::Result r;
    {
        std::lock_guard<std::mutex> lock(snap_mutex);
        if (!snap_slot || !snap_slot->take(esp_timer_get_time(), r)) return false;
    }
    n = snap::result_line(r, line, cap);
    return n > 0;
}

void vision_snap_link_down()
{
    std::lock_guard<std::mutex> lock(snap_mutex);
    if (snap_slot) snap_slot->reset();
}

void vision_uplink_json(ArduinoJson::JsonObject out)
{
    snap::Counters sc;
    bool have_snap = false, snap_busy = false;
    {
        std::lock_guard<std::mutex> lock(snap_mutex);
        if (snap_slot) { sc = snap_slot->counters(); snap_busy = snap_slot->busy(); have_snap = true; }
    }
    if (have_snap) {
        auto s = out["snap"].to<ArduinoJson::JsonObject>();
        s["busy"] = snap_busy; s["accepted"] = sc.accepted; s["ok"] = sc.ok; s["refused"] = sc.refused;
        s["timeouts"] = sc.timeouts; s["failed"] = sc.failed; s["late"] = sc.late; s["dropped"] = sc.dropped;
    }
    std::lock_guard<std::mutex> lock(mutex);
    out["state"] = state; out["connected"] = connected;
    out["connects"] = connects; out["failures"] = failures; out["sent"] = sent;
    if (connected) out["connected_ms"] = (esp_timer_get_time() - connected_us) / 1000;
    if (last_sent_us) out["last_sent_ms_ago"] = (esp_timer_get_time() - last_sent_us) / 1000;
    if (!last_error.empty()) out["last_error"] = last_error;
    out["frames_sent"] = frames_sent; out["frames_too_big"] = frames_too_big; out["frames_no_memory"] = frames_no_memory;
    out["frames_failed"] = frames_failed; out["frames_deferred"] = frames_deferred;
    if (frame_held(esp_timer_get_time(), frames_hold_until_us)) out["frames_held_ms"] = (frames_hold_until_us - esp_timer_get_time()) / 1000;
    if (last_frame_diff >= 0) out["last_frame_diff"] = last_frame_diff;
    if (last_frame_us) out["last_frame_age_ms"] = (esp_timer_get_time() - last_frame_us) / 1000;
}
}
