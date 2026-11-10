/* SPDX-License-Identifier: MIT */
#include "psram_task.h"
#include "m12_unit.h"
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <atomic>
#include <cmath>
#include <cstring>
#include <mutex>
#include <new>
#include <string>

namespace collector {
namespace {
constexpr char kTag[] = "M12";
constexpr uart_port_t kUart = UART_NUM_0; // UART1: servo bus, UART2: Unit ASR
constexpr int kBaud = 115200;
constexpr gpio_num_t kTx = GPIO_NUM_17, kRx = GPIO_NUM_18; // CoreS3 Port C: G17 TX, G18 RX
constexpr int64_t kCfgRetryUs = 1000000;      // after a cfg timeout or error
constexpr float kTemperatureLogStep = 5.0f;   // log the M12 temperature when it moves this much
constexpr size_t kRecords = 6;                // recent user commands kept for telemetry

// ArduinoJson working memory goes to PSRAM (internal RAM is kept for Wi-Fi DMA buffers).
struct PsramAllocator final : ArduinoJson::Allocator {
    void* allocate(size_t n) override { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
    void deallocate(void* p) override { heap_caps_free(p); }
    void* reallocate(void* p, size_t n) override { return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
};
PsramAllocator psram_allocator;

struct Record {                // one user command, for the web panel
    bool used = false;
    int32_t id = 0;
    M12Op op = M12Op::ping;
    char state[8] = {};        // pending / ok / error
    char err[16] = {};
    char name[kM12NameBytes + 1] = {};
    int samples = -1;
    int32_t face = -1;
    float sim = -1;
    int64_t at_us = 0;
};
struct Waiting {               // callback for a pending request
    int32_t id = 0;
    M12Callback fn = nullptr;
    void* context = nullptr;
    bool record = false;       // user command: keep a Record
    bool internal = false;     // driver's own cfg / who / faces refresh
};
// Everything shared lives in PSRAM; `state_mutex` guards it, `tx_mutex` guards requests.
struct Shared {
    M12Snapshot state;
    Record records[kRecords];
    size_t record_head = 0;
    M12LibraryEntry library[kM12LibraryMax];
    uint8_t library_count = 0;
    bool library_valid = false;
    int64_t library_us = 0;
    M12Config cfg;
    char version[16] = {};
    uint32_t lines = 0, invalid_lines = 0, overflow_lines = 0, replies = 0, errors = 0, timeouts = 0, unmatched = 0;
    uint32_t who_sent = 0, offline_events = 0, restarts = 0, cfg_sent = 0;
    bool det_known = false, det = false, cfg_inflight = false;
    uint32_t confirmed_revision = UINT32_MAX;
    int32_t who_inflight = -1;
    uint32_t who_busy = 0;
    // vision change events for the server (state_mutex): oldest first, newest kept when full
    static constexpr size_t kVision = 64;
    M12ChangeTracker tracker;
    M12VisionEvent vision[kVision];
    size_t vision_head = 0, vision_count = 0;
    uint32_t vision_total = 0, vision_dropped = 0;
    // tx side (tx_mutex)
    M12Requests pending;
    Waiting waiting[M12Requests::kSlots];
};
Shared* shared = nullptr;
std::mutex state_mutex, tx_mutex, request_mutex;
std::atomic<uint32_t> mode_request{0}, view_request{0};
std::atomic<bool> uart_ready{false};

const char* function_name(M12Function f)
{
    return f == M12Function::detect ? "detect" : f == M12Function::standby ? "standby" : "unknown";
}

bool install_uart()
{
    uart_config_t config{};
    config.baud_rate = kBaud;
    config.data_bits = UART_DATA_8_BITS;
    config.parity = UART_PARITY_DISABLE;
    config.stop_bits = UART_STOP_BITS_1;
    config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    config.source_clk = UART_SCLK_DEFAULT;
    esp_err_t result = uart_driver_install(kUart, 4096, 0, 0, nullptr, 0);
    if (result == ESP_OK) result = uart_param_config(kUart, &config);
    if (result == ESP_OK) result = uart_set_pin(kUart, kTx, kRx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "UART0 unavailable: %s", esp_err_to_name(result));
        return false;
    }
    gpio_pullup_en(kRx);
    return true;
}

void push_vision_locked(const M12VisionEvent& e)
{
    auto& s = *shared;
    if (s.vision_count == Shared::kVision) { s.vision_head = (s.vision_head + 1) % Shared::kVision; --s.vision_count; ++s.vision_dropped; }
    s.vision[(s.vision_head + s.vision_count) % Shared::kVision] = e;
    ++s.vision_count; ++s.vision_total;
}
void status_event_locked(M12VisionKind kind, int64_t now)
{
    M12VisionEvent e; e.kind = kind; e.t_us = now;
    push_vision_locked(e);
}
const auto vision_sink = [](const M12VisionEvent& e) { push_vision_locked(e); };

void put_record_locked(const Record& r)
{
    for (auto& x : shared->records) if (x.used && x.id == r.id) { x = r; return; }
    shared->records[shared->record_head] = r;
    shared->record_head = (shared->record_head + 1) % kRecords;
}

// Writes one request. Caller holds no lock. Returns id or negative error (see header).
int32_t send_request(M12Op op, const M12RequestArgs& args, uint32_t tag, M12Callback fn, void* context, bool record, bool internal)
{
    if (!uart_ready.load()) return -1;
    if ((op == M12Op::learn || op == M12Op::forget) && !m12_valid_name(args.name)) return -4;
    char line[kM12MaxRequest + 2];
    int32_t id;
    {
        std::lock_guard<std::mutex> lock(tx_mutex);
        const int64_t now = esp_timer_get_time();
        id = shared->pending.add(op, now, tag, args.face);
        if (id < 0) return -3;
        const size_t n = m12_build_request(line, sizeof(line), id, op, args);
        if (!n) { shared->pending.cancel(id); return -4; }
        for (auto& w : shared->waiting) if (w.id == 0) { w = {id, fn, context, record, internal}; break; }
        uart_write_bytes(kUart, line, n);
    }
    if (record) {
        Record r;
        r.used = true; r.id = id; r.op = op; r.at_us = esp_timer_get_time(); r.face = args.face;
        std::snprintf(r.state, sizeof(r.state), "pending");
        if (args.name) std::snprintf(r.name, sizeof(r.name), "%s", args.name);
        std::lock_guard<std::mutex> lock(state_mutex);
        put_record_locked(r);
    }
    return id;
}

void refresh_faces_cb(const M12Reply&, const char*, void*) {}

// Completion of any request; runs only in the M12 task.
void finish_request(const M12Pending& p, const M12Reply* reply, const char* err, M12WhoPolicy& who, int64_t now)
{
    Waiting w;
    {
        std::lock_guard<std::mutex> lock(tx_mutex);
        for (auto& x : shared->waiting) if (x.id == p.id) { w = x; x = {}; break; }
    }
    M12Reply empty;                      // on the M12 task's PSRAM stack (only caller)
    const M12Reply& r = reply ? *reply : empty;
    bool refresh_library = false;
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        auto& s = shared->state;
        switch (p.op) {
        case M12Op::cfg:
            shared->cfg_inflight = false;
            if (!err && r.cfg.valid) {
                const bool was_on = shared->det_known && shared->det;
                if (r.cfg.det != was_on || !shared->det_known) {
                    if (!r.cfg.det) shared->tracker.clear(now, "vision_off", vision_sink);
                    if (r.cfg.det != was_on) status_event_locked(r.cfg.det ? M12VisionKind::vision_on : M12VisionKind::vision_off, now);
                }
                shared->cfg = r.cfg;
                shared->det_known = true;
                shared->det = r.cfg.det;
                shared->confirmed_revision = p.tag;
                s.function = r.cfg.det ? M12Function::detect : M12Function::standby;
                s.mode_request_revision = p.tag;
                if (!r.cfg.det) { s.count = 0; s.persons = 0; s.num = 0; }
            } else {
                shared->det_known = false;   // retry after kCfgRetryUs
            }
            break;
        case M12Op::who:
            shared->who_inflight = -1;
            if (!err && !r.has_name) {          // asked and nobody matched: report it right away
                M12VisionEvent e; e.kind = M12VisionKind::face_unknown; e.t_us = now; e.f = r.f;
                e.face = r.face >= 0 ? r.face : p.face; e.sim = r.sim;
                push_vision_locked(e);
            }
            break;
        case M12Op::faces:
            if (!err && r.has_library) {
                shared->library_count = r.library_count;
                std::memcpy(shared->library, r.library, sizeof(shared->library));
                shared->library_valid = true;
                shared->library_us = now;
            }
            break;
        case M12Op::status:
            if (!err && r.hb.valid) s.hb = r.hb;
            break;
        case M12Op::ver:
            if (!err) std::snprintf(shared->version, sizeof(shared->version), "%s", r.ver);
            break;
        default: break;
        }
        if ((p.op == M12Op::learn || p.op == M12Op::forget) && !err) refresh_library = true;
        if (err) ++shared->errors;
        if (w.record) {
            Record rec;
            for (auto& x : shared->records) if (x.used && x.id == p.id) rec = x;
            rec.used = true; rec.id = p.id; rec.op = p.op; rec.at_us = now;
            std::snprintf(rec.state, sizeof(rec.state), "%s", err ? "error" : "ok");
            std::snprintf(rec.err, sizeof(rec.err), "%s", err ? err : "");
            if (r.has_name || r.name[0]) std::snprintf(rec.name, sizeof(rec.name), "%s", r.name);
            else if (p.op == M12Op::who && !err) rec.name[0] = 0;   // who: nobody matched
            if (r.samples >= 0) rec.samples = r.samples;
            if (r.face >= 0) rec.face = r.face;
            if (r.sim >= 0) rec.sim = r.sim;
            put_record_locked(rec);
        }
    }
    if (p.op == M12Op::who && w.internal) {
        who.answered(p.face, err, now);
        std::lock_guard<std::mutex> lock(state_mutex);
        shared->who_busy = who.busy();
    }
    if (err) ESP_LOGW(kTag, "%s #%ld: %s", m12_op_name(p.op), static_cast<long>(p.id), err);
    else if (p.op == M12Op::learn) ESP_LOGI(kTag, "learn #%ld ok, samples %d", static_cast<long>(p.id), r.samples);
    else if (p.op == M12Op::who) ESP_LOGI(kTag, "who #%ld face %ld: %s (sim %.2f)", static_cast<long>(p.id), static_cast<long>(r.face), r.name[0] ? "known" : "no match", r.sim);
    if (w.fn) w.fn(r, err, w.context);
    if (refresh_library) send_request(M12Op::faces, {}, 0, refresh_faces_cb, nullptr, false, true);
}

void m12_task(void*)
{
    // Installed from this task (pinned to core 1) so the UART interrupt is allocated
    // on core 1: core 0 must keep free inputs for Wi-Fi's lazily allocated AES interrupt.
    if (!install_uart()) vTaskDeleteWithCaps(nullptr);
    ESP_LOGI(kTag, "M12 (m12d) on Port C: UART0 %d 8N1, TX GPIO%d, RX GPIO%d (core %d)", kBaud, kTx, kRx, xPortGetCoreID());
    char* storage = static_cast<char*>(heap_caps_malloc(kM12MaxLine, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    void* message_memory = heap_caps_malloc(sizeof(M12Message), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    void* who_memory = heap_caps_malloc(sizeof(M12WhoPolicy), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!storage || !message_memory || !who_memory) {
        ESP_LOGE(kTag, "PSRAM buffers unavailable");
        vTaskDeleteWithCaps(nullptr);
    }
    M12LineBuffer buffer(storage, kM12MaxLine);
    M12Message& msg = *new (message_memory) M12Message();
    M12WhoPolicy& who = *new (who_memory) M12WhoPolicy();
    ArduinoJson::JsonDocument doc(&psram_allocator);
    uart_ready = true;
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        shared->state.valid = true;
    }
    uint8_t bytes[256];
    uint32_t last_up = 0;
    float logged_t = -100;
    int logged_tier = -1;
    int64_t cfg_next_us = 0, summary_us = 0;
    while (true) {
        const int64_t now = esp_timer_get_time();
        // --- heartbeat watch ---
        bool online;
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            auto& s = shared->state;
            if (s.online && now - s.last_hb_us > kM12HeartbeatTimeoutUs) {
                s.online = false;
                s.count = 0; s.persons = 0; s.num = 0;
                s.function = M12Function::none;
                shared->det_known = false;
                ++shared->offline_events;
                shared->tracker.clear(now, "m12_offline", vision_sink);
                status_event_locked(M12VisionKind::m12_offline, now);
                ESP_LOGW(kTag, "no heartbeat for %lld ms: M12 offline", static_cast<long long>((now - s.last_hb_us) / 1000));
            }
            online = s.online;
            shared->tracker.tick(now, vision_sink);
        }
        // --- expired requests ---
        for (M12Pending p; ; ) {
            bool expired;
            { std::lock_guard<std::mutex> lock(tx_mutex); expired = shared->pending.expire(now, p); }
            if (!expired) break;
            { std::lock_guard<std::mutex> lock(state_mutex); ++shared->timeouts; }
            finish_request(p, nullptr, "client_timeout", who, now);
            if (p.op == M12Op::cfg) cfg_next_us = now + kCfgRetryUs;
        }
        // --- keep det in line with the requested mode ---
        const uint32_t want_rev = mode_request.load();
        const bool want_det = m12_requested_function(want_rev) == M12Function::detect;
        bool send_cfg = false;
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            if (online && !shared->cfg_inflight && now >= cfg_next_us &&
                (!shared->det_known || shared->det != want_det || shared->confirmed_revision != want_rev)) {
                shared->cfg_inflight = true;
                shared->state.requested_us = now;
                ++shared->cfg_sent;
                send_cfg = true;
            }
        }
        if (send_cfg) {
            M12RequestArgs a;
            a.det = want_det ? 1 : 0;
            a.lm = 0;                       // landmarks unused here; ~40% shorter detection lines
            const int32_t id = send_request(M12Op::cfg, a, want_rev, nullptr, nullptr, false, true);
            if (id < 0) {
                std::lock_guard<std::mutex> lock(state_mutex);
                shared->cfg_inflight = false;
                cfg_next_us = now + kCfgRetryUs;
            } else {
                ESP_LOGI(kTag, "cfg det:%s (request %lu)", want_det ? "true" : "false", static_cast<unsigned long>(want_rev >> 2));
                if (!want_det) who.reset();
            }
        }
        // --- periodic summary ---
        if (now - summary_us >= 60000000) {
            summary_us = now;
            std::lock_guard<std::mutex> lock(state_mutex);
            const auto& s = shared->state;
            ESP_LOGI(kTag, "online=%d det=%s t=%.0f fps=%.1f tier=%d lines=%lu invalid=%lu timeouts=%lu", s.online,
                     function_name(s.function), s.hb.t, s.hb.fps, s.hb.tier, static_cast<unsigned long>(shared->lines),
                     static_cast<unsigned long>(shared->invalid_lines), static_cast<unsigned long>(shared->timeouts));
        }
        // --- receive ---
        const int count = uart_read_bytes(kUart, bytes, sizeof(bytes), pdMS_TO_TICKS(50));
        if (count <= 0) continue;
        const int64_t rx_us = esp_timer_get_time();
        for (int i = 0; i < count; ++i) {
            if (!buffer.push(static_cast<char>(bytes[i]))) continue;
            const bool ok = parse_m12_line(buffer.line(), buffer.size(), msg, doc);
            if (!ok) {
                std::lock_guard<std::mutex> lock(state_mutex);
                ++shared->lines; ++shared->invalid_lines;
                continue;
            }
            if (msg.kind == M12Kind::reply) {
                M12Pending p;
                bool matched;
                { std::lock_guard<std::mutex> lock(tx_mutex); matched = msg.reply.id >= 0 && shared->pending.take(msg.reply.id, p); }
                {
                    std::lock_guard<std::mutex> lock(state_mutex);
                    ++shared->lines; ++shared->replies;
                    if (!matched) ++shared->unmatched;
                }
                if (!matched) {
                    ESP_LOGW(kTag, "reply id %ld without a waiting request (%s)", static_cast<long>(msg.reply.id), msg.reply.ok ? "ok" : msg.reply.err);
                    continue;
                }
                finish_request(p, &msg.reply, msg.reply.ok ? nullptr : msg.reply.err, who, rx_us);
                if (p.op == M12Op::cfg && !msg.reply.ok) cfg_next_us = rx_us + kCfgRetryUs;
                continue;
            }
            if (msg.kind == M12Kind::heartbeat) {
                bool came_online = false, restarted = false;
                {
                    std::lock_guard<std::mutex> lock(state_mutex);
                    auto& s = shared->state;
                    ++shared->lines;
                    came_online = !s.online;
                    restarted = msg.hb.up + 5 < last_up;
                    s.online = true;
                    s.last_hb_us = rx_us;
                    s.hb = msg.hb;
                    // m12d settings reset on power-up (det back to true): send cfg again.
                    if (came_online || restarted) { shared->det_known = false; if (restarted) ++shared->restarts; }
                    if (came_online) status_event_locked(M12VisionKind::m12_online, rx_us);
                }
                last_up = msg.hb.up;
                if (came_online) ESP_LOGI(kTag, "heartbeat: M12 online (up %lu s, %.0f C)", static_cast<unsigned long>(msg.hb.up), msg.hb.t);
                if (restarted) { ESP_LOGW(kTag, "M12 restarted (up %lu s)", static_cast<unsigned long>(msg.hb.up)); who.reset(); }
                if (std::fabs(msg.hb.t - logged_t) >= kTemperatureLogStep || msg.hb.tier != logged_tier) {
                    ESP_LOGI(kTag, "temperature %.0f C, tier %d%s, fps %.1f", msg.hb.t, msg.hb.tier,
                             msg.hb.tier == 3 ? " (detection paused by M12, >= 90 C)" : "", msg.hb.fps);
                    logged_t = msg.hb.t; logged_tier = msg.hb.tier;
                }
                continue;
            }
            if (msg.kind == M12Kind::detection) {
                bool ask = false;
                int32_t face = -1;
                {
                    std::lock_guard<std::mutex> lock(state_mutex);
                    auto& s = shared->state;
                    ++shared->lines;
                    ++s.results; ++s.result_seq;
                    s.last_result_us = rx_us;
                    ++s.frame_seq;
                    s.frame_us = rx_us;
                    s.frame_f = msg.det.f;
                    s.num = msg.det.num;
                    s.count = msg.det.count;
                    std::memcpy(s.faces, msg.det.faces, sizeof(s.faces));
                    s.persons = msg.det.persons;
                    std::memcpy(s.person, msg.det.person, sizeof(s.person));
                    shared->tracker.detection(msg.det, rx_us, vision_sink);
                    // ask who only while detection is what we asked for
                    if (s.online && shared->det_known && shared->det && want_det) {
                        face = who.next(msg.det, rx_us);
                        ask = face >= 0;
                    }
                }
                if (ask) {
                    M12RequestArgs a;
                    a.face = face;
                    const int32_t id = send_request(M12Op::who, a, 0, nullptr, nullptr, false, true);
                    if (id < 0) who.answered(face, "send", rx_us);
                    else {
                        who.sent(face, rx_us);
                        std::lock_guard<std::mutex> lock(state_mutex);
                        ++shared->who_sent;
                        shared->who_inflight = face;
                    }
                }
                continue;
            }
            std::lock_guard<std::mutex> lock(state_mutex);
            ++shared->lines;
        }
        std::lock_guard<std::mutex> lock(state_mutex);
        shared->state.rx_bytes += count;
        shared->overflow_lines = buffer.overflows();
    }
}
} // namespace

void m12_init()
{
    if (!shared) {
        void* memory = heap_caps_malloc(sizeof(Shared), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!memory) { ESP_LOGE(kTag, "PSRAM state unavailable"); return; }
        shared = new (memory) Shared();
    }
    if (create_psram_task(m12_task, "collector_m12", 8192, nullptr, 2, 1) != pdPASS)
        ESP_LOGE(kTag, "M12 task unavailable");
}

void m12_wake() {}

void m12_set_view(bool on) { m12_set_mode(on ? M12Function::detect : M12Function::standby); }
void m12_set_mode(M12Function mode)
{
    if (mode == M12Function::none) return;
    std::lock_guard<std::mutex> lock(request_mutex);
    auto previous_mode = mode_request.load();
    while (!mode_request.compare_exchange_weak(previous_mode, next_m12_request(previous_mode, mode))) {}
    auto previous = view_request.load();
    while (!view_request.compare_exchange_weak(previous, next_m12_request(previous, mode))) {}
}

uint32_t m12_set_background_mode(M12Function mode)
{
    std::lock_guard<std::mutex> lock(request_mutex);
    auto previous = mode_request.load();
    uint32_t requested;
    do { requested = next_m12_request(previous, mode); } while (!mode_request.compare_exchange_weak(previous, requested));
    auto preview = view_request.load();
    while (!view_request.compare_exchange_weak(preview, next_m12_request(preview, M12Function::standby))) {}
    return requested;
}
uint32_t m12_mode_request() { return mode_request.load(); }
uint32_t m12_view_request() { return view_request.load(); }

M12Snapshot m12_snapshot()
{
    if (!shared) return {};
    std::lock_guard<std::mutex> lock(state_mutex);
    return shared->state;
}

int32_t m12_request(M12Op op, const M12RequestArgs& args, M12Callback callback, void* context)
{
    if (!shared || !uart_ready.load()) return -1;
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        if (!shared->state.online) return -2;
    }
    return send_request(op, args, 0, callback, context, true, false);
}

size_t m12_take_vision_events(M12VisionEvent* out, size_t max)
{
    if (!shared) return 0;
    std::lock_guard<std::mutex> lock(state_mutex);
    auto& s = *shared;
    size_t n = 0;
    while (n < max && s.vision_count) {
        out[n++] = s.vision[s.vision_head];
        s.vision_head = (s.vision_head + 1) % Shared::kVision; --s.vision_count;
    }
    return n;
}

const char* m12_request_error(int32_t code)
{
    switch (code) {
    case -1: return "serial_unavailable";
    case -2: return "offline";
    case -3: return "busy_local";
    case -4: return "badarg";
    default: return "error";
    }
}

void m12_json(ArduinoJson::JsonObject out)
{
    if (!shared) { out["valid"] = false; return; }
    std::lock_guard<std::mutex> lock(state_mutex);
    const auto& s = shared->state;
    const int64_t now = esp_timer_get_time();
    const auto view = view_request.load(), mode = mode_request.load();
    out["protocol"] = "m12d";
    out["valid"] = s.valid;
    out["online"] = s.online;
    out["hb_age_ms"] = s.last_hb_us ? (now - s.last_hb_us) / 1000 : -1;
    if (s.hb.valid) {
        auto hb = out["heartbeat"].to<ArduinoJson::JsonObject>();
        hb["t"] = s.hb.t; hb["fps"] = s.hb.fps; hb["tier"] = s.hb.tier; hb["up"] = s.hb.up; hb["mem"] = s.hb.mem;
        hb["frames"] = s.hb.frames; hb["reopens"] = s.hb.reopens; hb["drop"] = s.hb.drop; hb["mhz"] = s.hb.mhz;
    }
    if (shared->version[0]) out["version"] = std::string(shared->version);
    out["view_enabled"] = m12_requested_view(view);
    out["view_seq"] = view >> 2;
    out["function"] = function_name(s.function);
    out["requested_function"] = function_name(m12_requested_function(mode));
    out["requested_us"] = s.requested_us;
    out["mode_request_revision"] = s.mode_request_revision;
    out["mode_request_pending"] = mode != shared->confirmed_revision;
    if (shared->cfg.valid) {
        auto c = out["cfg"].to<ArduinoJson::JsonObject>();
        c["det"] = shared->cfg.det; c["lm"] = shared->cfg.lm; c["attr"] = shared->cfg.attr; c["person"] = shared->cfg.person;
        c["id_th"] = shared->cfg.id_th; c["max_faces"] = shared->cfg.max_faces; c["fps_cap"] = shared->cfg.fps_cap;
    }
    out["results"] = s.results;
    out["last_result_us"] = s.last_result_us;
    out["frame_us"] = s.frame_us;
    out["frame_f"] = s.frame_f;
    out["num"] = s.num;
    auto faces = out["faces"].to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < s.count; ++i) {
        const auto& f = s.faces[i];
        auto face = faces.add<ArduinoJson::JsonObject>();
        face["id"] = f.id; face["x"] = f.x; face["y"] = f.y; face["w"] = f.w; face["h"] = f.h; face["prob"] = f.prob;
        // Copy every non-literal string: the document is serialized after this function returns.
        if (f.gender) face["gender"] = std::string(1, f.gender);
        if (f.age >= 0) face["age"] = f.age;
        if (f.age_group != M12AgeGroup::none) face["age_group"] = m12_age_group_name(f.age_group);
        if (f.name[0]) face["name"] = std::string(f.name);
    }
    auto persons = out["persons"].to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < s.persons; ++i) {
        const auto& p = s.person[i];
        auto person = persons.add<ArduinoJson::JsonObject>();
        person["id"] = p.id; person["x"] = p.x; person["y"] = p.y; person["w"] = p.w; person["h"] = p.h;
        person["prob"] = p.prob; person["face"] = p.face;
    }
    auto commands = out["commands"].to<ArduinoJson::JsonArray>();
    for (size_t k = 0; k < kRecords; ++k) {
        const auto& r = shared->records[(shared->record_head + kRecords - 1 - k) % kRecords];   // newest first
        if (!r.used) continue;
        auto c = commands.add<ArduinoJson::JsonObject>();
        c["id"] = r.id; c["op"] = m12_op_name(r.op); c["state"] = std::string(r.state); c["age_ms"] = (now - r.at_us) / 1000;
        if (r.err[0]) c["err"] = std::string(r.err);
        if (r.name[0]) c["name"] = std::string(r.name);
        if (r.samples >= 0) c["samples"] = r.samples;
        if (r.face >= 0) c["face"] = r.face;
        if (r.sim >= 0) c["sim"] = r.sim;
    }
    auto library = out["library"].to<ArduinoJson::JsonObject>();
    library["valid"] = shared->library_valid;
    library["age_ms"] = shared->library_us ? (now - shared->library_us) / 1000 : -1;
    auto names = library["faces"].to<ArduinoJson::JsonArray>();
    for (uint8_t i = 0; i < shared->library_count; ++i) {
        auto e = names.add<ArduinoJson::JsonObject>();
        e["name"] = std::string(shared->library[i].name); e["samples"] = shared->library[i].samples;
    }
    out["rx_bytes"] = s.rx_bytes;
    auto vision = out["vision_events"].to<ArduinoJson::JsonObject>();
    vision["queued"] = shared->vision_count; vision["total"] = shared->vision_total; vision["dropped"] = shared->vision_dropped;
    vision["faces_in_view"] = shared->tracker.faces_in_view(); vision["persons_in_view"] = shared->tracker.persons_in_view();
    auto counters = out["counters"].to<ArduinoJson::JsonObject>();
    counters["lines"] = shared->lines; counters["invalid"] = shared->invalid_lines; counters["overflow"] = shared->overflow_lines;
    counters["replies"] = shared->replies; counters["errors"] = shared->errors; counters["timeouts"] = shared->timeouts;
    counters["unmatched"] = shared->unmatched; counters["cfg_sent"] = shared->cfg_sent; counters["who_sent"] = shared->who_sent;
    counters["who_busy"] = shared->who_busy; counters["offline_events"] = shared->offline_events; counters["restarts"] = shared->restarts;
}

void m12_info_json(ArduinoJson::JsonObject out)
{
    out["device"] = "M5Stack UnitV2 M12 running m12d (user firmware; factory protocol retired 2026-11-10)";
    out["port"] = "C (top blue), Grove power only (never USB-C at the same time)";
    out["uart"] = 0;
    out["baud"] = kBaud;
    out["tx_gpio"] = static_cast<int>(kTx);
    out["rx_gpio"] = static_cast<int>(kRx);
    out["frame_width"] = kM12FrameWidth;
    out["frame_height"] = kM12FrameHeight;
    out["max_faces"] = kM12MaxFaces;
    out["protocol"] = "one JSON object per line; push {f..}/{hb..}; request {id,do}; reply {id,ok[,err]}";
    out["heartbeat_timeout_ms"] = kM12HeartbeatTimeoutUs / 1000;
    out["reply_timeout_ms"]["learn_who"] = kM12SlowReplyUs / 1000;
    out["reply_timeout_ms"]["other"] = kM12FastReplyUs / 1000;
    auto ops = out["ops"].to<ArduinoJson::JsonArray>();
    for (M12Op op : {M12Op::ping, M12Op::ver, M12Op::status, M12Op::cfg, M12Op::learn, M12Op::who, M12Op::forget, M12Op::faces})
        ops.add(m12_op_name(op));
    auto policy = out["policy"].to<ArduinoJson::JsonObject>();
    policy["boot"] = "standby (cfg det:false once the first heartbeat arrives; re-sent after an M12 restart)";
    policy["detect"] = "ASR turn on (face view), tracking or recognition mode";
    policy["identify"] = "one who per new face id without a name; names then ride on detection lines";
    policy["temperature"] = "hb.t shown and logged only; M12 pauses detection itself at 90 C (tier 3)";
}
} // namespace collector
