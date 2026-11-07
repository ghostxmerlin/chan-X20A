/* SPDX-License-Identifier: MIT */
#include "m12_unit.h"
#include "web_control.h"
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <array>
#include <atomic>
#include <cstring>
#include <mutex>
#include <string>

namespace collector {
namespace {
constexpr char kTag[] = "M12";
constexpr uart_port_t kUart = UART_NUM_0; // UART1: servo bus, UART2: Unit ASR
constexpr int kBaud = 115200;
constexpr gpio_num_t kTx = GPIO_NUM_17, kRx = GPIO_NUM_18; // CoreS3 Port C: G17 TX, G18 RX
// The M12 needs about 30 s after power-on before it accepts commands, and a
// function switch takes 1.5-2.5 s. Resend while no answer arrives.
// After kFastResends tries it keeps trying every kSlowResendUs: a power-cycled or
// stuck M12 can take a minute to come back, and giving up would leave it unmanaged.
constexpr int64_t kResendUs = 8000000;
constexpr int64_t kSlowResendUs = 30000000;
constexpr int kFastResends = 6;

std::atomic<uint32_t> view_request{0};
std::mutex state_mutex;
M12Snapshot state;
// Diagnostics (task-owned writes, read under the same mutex).
uint32_t rx_bytes = 0, lines = 0, invalid_lines = 0, replies = 0, errors = 0, sends = 0, overflow_lines = 0;
char last_reply[64] = {}, last_running[24] = {};
int64_t last_rx_us = 0;

const char* function_name(M12Function f)
{
    switch (f) {
    case M12Function::detect: return "face_detector";
    case M12Function::recognize: return "face_recognition";
    case M12Function::standby: return "standby";
    default: return "none";
    }
}
const char* command_for(M12Function f)
{
    switch (f) {
    case M12Function::detect: return kM12FaceDetectorCommand;
    default: return kM12StandbyCommand;
    }
}

int64_t send(M12Function f)
{
    const char* command = command_for(f);
    uart_write_bytes(kUart, command, std::strlen(command));
    const int64_t now = esp_timer_get_time();
    std::lock_guard<std::mutex> lock(state_mutex);
    ++sends;
    state.requested_us = now;
    return now;
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

void m12_task(void*)
{
    // Installed from this task (pinned to core 1) so the UART interrupt is allocated
    // on core 1: core 0 must keep free inputs for Wi-Fi's lazily allocated AES interrupt.
    if (!install_uart()) vTaskDelete(nullptr);
    ESP_LOGI(kTag, "UnitV2 M12 on Port C: UART0 %d 8N1, TX GPIO%d, RX GPIO%d (core %d)", kBaud, kTx, kRx, xPortGetCoreID());
    // A bare line flushes whatever the M12 collected while our TX pin was still
    // floating; it answers with one harmless "invalid json format" error.
    uart_write_bytes(kUart, "\r\n", 2);
    M12Policy policy;
    policy.start(esp_timer_get_time());
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        state.valid = true;
    }
    M12LineBuffer buffer;
    std::string line;
    std::array<uint8_t, 256> bytes{};
    M12Function active = M12Function::none;
    M12Phase logged_phase = M12Phase::idle;
    int64_t requested_us = 0, last_detect_us = 0, last_recog_us = 0, last_standby_us = 0;
    uint32_t seen_view = 0;
    int resends = 0, raw_logged = 0;
    int64_t stats_logged_us = 0;
    uint32_t stats_results = 0, results = 0;
    while (true) {
        const int64_t loop_us = esp_timer_get_time();
        if (const uint32_t v = view_request.load(); v != seen_view) {
            seen_view = v;
            policy.view((v & 1) != 0, loop_us);
        }
        policy.tick(loop_us);
        if (policy.phase() != logged_phase) {
            ESP_LOGI(kTag, "phase: %s -> %s", m12_phase_name(logged_phase), m12_phase_name(policy.phase()));
            logged_phase = policy.phase();
        }
        const M12Function want = policy.function();
        if (m12_switch_required(active, want)) {
            requested_us = send(want);
            ESP_LOGI(kTag, "send %s", function_name(want));
            active = want;
            resends = 0;
            raw_logged = 0;
        } else if (active != M12Function::none &&
                   loop_us - requested_us >= (resends < kFastResends ? kResendUs : kSlowResendUs)) {
            bool unanswered = false;
            if (active == M12Function::detect) unanswered = last_detect_us < requested_us;
            // Standby produces no periodic output, so silence alone is not an
            // acknowledgement (especially while the M12 is still booting).
            // Stop retrying after its switch reply unless face output resumes.
            else unanswered = last_standby_us < requested_us ||
                              std::max(last_detect_us, last_recog_us) > last_standby_us + 4000000;
            if (unanswered) {
                ++resends;
                requested_us = send(active);
                ESP_LOGW(kTag, "no answer to %s; resend %d", function_name(active), resends);
            }
        }
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            state.phase = policy.phase();
            state.function = active;
            state.identity = policy.identity();
        }
        if (active == M12Function::detect && loop_us - stats_logged_us >= 10000000) {
            ESP_LOGI(kTag, "faces: %lu results in 10 s, rx=%lu lines=%lu invalid=%lu",
                     static_cast<unsigned long>(results - stats_results), static_cast<unsigned long>(rx_bytes),
                     static_cast<unsigned long>(lines), static_cast<unsigned long>(invalid_lines));
            stats_results = results;
            stats_logged_us = loop_us;
        }
        const int count = uart_read_bytes(kUart, bytes.data(), bytes.size(), pdMS_TO_TICKS(50));
        if (count <= 0) continue;
        const int64_t now = esp_timer_get_time();
        for (int i = 0; i < count; ++i) {
            if (!buffer.push(static_cast<char>(bytes[i]), line)) continue;
            const M12Line result = parse_m12_line(line);
            if ((result.kind == M12LineKind::reply &&
                 (std::strcmp(result.text, "function switched to audio_fft.") == 0 ||
                  std::strcmp(result.text, "function switched to Audio FFT.") == 0)) ||
                (result.kind == M12LineKind::other &&
                 (std::strcmp(result.running, "Audio FFT") == 0 || std::strcmp(result.running, "audio_fft") == 0)))
                last_standby_us = now;
            // The first two lines after a switch show the real output format.
            if (raw_logged < 2 && result.kind != M12LineKind::reply && result.kind != M12LineKind::error) {
                ++raw_logged;
                ESP_LOGI(kTag, "raw %s: %.160s", result.kind == M12LineKind::faces ? "faces" :
                         result.kind == M12LineKind::invalid ? "invalid" : "other", line.c_str());
            }
            if (result.kind == M12LineKind::faces) {
                if (result.recognition) {
                    last_recog_us = now;
                    const M12Face* best = nullptr;
                    for (size_t f = 0; f < result.count; ++f)
                        if (!best || result.faces[f].match_prob > best->match_prob) best = &result.faces[f];
                    policy.result(now, result.count, true, best ? best->name : "", best ? best->match_prob : 0);
                } else {
                    last_detect_us = now;
                    ++results;
                    policy.result(now, result.count, false, nullptr, 0);
                }
            }
            std::lock_guard<std::mutex> lock(state_mutex);
            ++lines;
            if (result.running[0]) std::memcpy(last_running, result.running, sizeof(last_running));
            switch (result.kind) {
            case M12LineKind::faces:
                if (result.recognition) {
                    ++state.recognitions;
                    state.last_recognition_us = now;
                } else {
                    ++state.results;
                    ++state.result_seq;
                    state.last_result_us = now;
                    state.num = result.num;
                    state.count = result.count;
                    std::memcpy(state.faces, result.faces, sizeof(state.faces));
                }
                break;
            case M12LineKind::reply:
            case M12LineKind::error:
                if (result.kind == M12LineKind::reply) ++replies;
                else ++errors;
                std::memcpy(last_reply, result.text, sizeof(last_reply));
                ESP_LOGI(kTag, "%s: %s", result.kind == M12LineKind::reply ? "reply" : "error", result.text);
                break;
            case M12LineKind::invalid: ++invalid_lines; break;
            case M12LineKind::other: break;
            }
        }
        std::lock_guard<std::mutex> lock(state_mutex);
        rx_bytes += count;
        last_rx_us = now;
        overflow_lines = buffer.overflows();
    }
}
} // namespace

void m12_init()
{
    if (xTaskCreatePinnedToCore(m12_task, "collector_m12", 6144, nullptr, 2, nullptr, 1) != pdPASS)
        ESP_LOGE(kTag, "M12 task unavailable");
}

void m12_wake() {}

void m12_set_view(bool on)
{
    auto previous = view_request.load();
    while (!view_request.compare_exchange_weak(previous, next_view_request(previous, on))) {}
}

uint32_t m12_view_request() { return view_request.load(); }

const char* m12_phase_name(M12Phase phase)
{
    switch (phase) {
    case M12Phase::looking: return "looking";
    case M12Phase::watching: return "watching";
    case M12Phase::identifying: return "identifying";
    case M12Phase::dozing: return "dozing";
    default: return "idle";
    }
}

M12Snapshot m12_snapshot()
{
    std::lock_guard<std::mutex> lock(state_mutex);
    M12Snapshot copy = state;
    copy.rx_bytes = rx_bytes;
    return copy;
}

void m12_json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(state_mutex);
    const auto view = view_request.load();
    out["view_enabled"] = (view & 1) != 0;
    out["view_seq"] = view >> 1;
    out["valid"] = state.valid;
    out["phase"] = m12_phase_name(state.phase);
    out["function"] = function_name(state.function);
    out["requested_us"] = state.requested_us;
    out["sends"] = sends;
    out["running"] = last_running;
    out["last_reply"] = last_reply;
    out["results"] = state.results;
    out["last_result_us"] = state.last_result_us;
    out["recognitions"] = state.recognitions;
    out["last_recognition_us"] = state.last_recognition_us;
    out["num"] = state.num;
    auto faces = out["faces"].to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < state.count; ++i) {
        auto face = faces.add<ArduinoJson::JsonObject>();
        face["x"] = state.faces[i].x;
        face["y"] = state.faces[i].y;
        face["w"] = state.faces[i].w;
        face["h"] = state.faces[i].h;
        face["prob"] = state.faces[i].prob;
    }
    if (state.identity.at_us) {
        auto who = out["identity"].to<ArduinoJson::JsonObject>();
        who["known"] = state.identity.known;
        if (state.identity.known) who["name"] = state.identity.name;
        who["match_prob"] = state.identity.match_prob;
        who["at_us"] = state.identity.at_us;
    }
    out["rx_bytes"] = rx_bytes;
    out["last_rx_us"] = last_rx_us;
    out["lines"] = lines;
    out["invalid_lines"] = invalid_lines;
    out["overflow_lines"] = overflow_lines;
    out["replies"] = replies;
    out["errors"] = errors;
}

void m12_info_json(ArduinoJson::JsonObject out)
{
    out["device"] = "M5Stack UnitV2 M12, built-in firmware";
    out["port"] = "C (top blue)";
    out["uart"] = 0;
    out["baud"] = kBaud;
    out["tx_gpio"] = static_cast<int>(kTx);
    out["rx_gpio"] = static_cast<int>(kRx);
    out["frame_width"] = kM12FrameWidth;
    out["frame_height"] = kM12FrameHeight;
    out["max_faces"] = kM12MaxFaces;
    auto policy = out["policy"].to<ArduinoJson::JsonObject>();
    policy["mode"] = "explicit";
    policy["boot"] = "standby";
    policy["automatic_recognition"] = false;
    policy["automatic_wake"] = false;
    policy["detect"] = "ASR turn on opens the face page";
    policy["standby"] = "ASR turn off or leave the face page";
    auto commands = out["commands"].to<ArduinoJson::JsonObject>();
    commands["detect"] = "{\"function\":\"Face Detector\",\"args\":\"\"}";
    commands["standby"] = "{\"function\":\"Audio FFT\",\"args\":\"\"}";
}
} // namespace collector
