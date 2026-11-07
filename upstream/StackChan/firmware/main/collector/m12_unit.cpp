/* SPDX-License-Identifier: MIT */
#include "m12_unit.h"
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
constexpr int64_t kResendUs = 8000000;
constexpr int kMaxResends = 6;

std::atomic_bool driver_ok{false};
std::atomic<int> wanted{0};
std::atomic<uint32_t> wanted_seq{0};
std::mutex state_mutex;
M12Snapshot state;
// Diagnostics (task-owned writes, read under the same mutex).
uint32_t rx_bytes = 0, lines = 0, invalid_lines = 0, replies = 0, errors = 0, sends = 0, overflow_lines = 0;
char last_reply[64] = {}, last_running[24] = {};
int64_t last_rx_us = 0;

void send(const char* command)
{
    uart_write_bytes(kUart, command, std::strlen(command));
    std::lock_guard<std::mutex> lock(state_mutex);
    ++sends;
    state.requested_us = esp_timer_get_time();
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
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        state.valid = true;
    }
    driver_ok = true;
    ESP_LOGI(kTag, "UnitV2 M12 on Port C: UART0 %d 8N1, TX GPIO%d, RX GPIO%d (core %d)", kBaud, kTx, kRx, xPortGetCoreID());
    // A bare line flushes whatever the M12 collected while our TX pin was still
    // floating; it answers with one harmless "invalid json format" error.
    uart_write_bytes(kUart, "\r\n", 2);
    M12LineBuffer buffer;
    std::string line;
    std::array<uint8_t, 256> bytes{};
    uint32_t applied_seq = 0;
    int resends = 0;
    int raw_logged = 0;           // raw lines logged since the last request
    int64_t stats_logged_us = 0;  // periodic face statistics while face mode is on
    uint32_t stats_results = 0;
    while (true) {
        const uint32_t seq = wanted_seq.load();
        const int want = wanted.load();
        if (seq != applied_seq) {
            applied_seq = seq;
            resends = 0;
            {
                std::lock_guard<std::mutex> lock(state_mutex);
                state.requested = want;
                state.request_seq = seq;
            }
            send(want > 0 ? kM12FaceDetectorCommand : kM12StandbyCommand);
            raw_logged = 0;
            ESP_LOGI(kTag, "request #%lu: %s", static_cast<unsigned long>(seq), want > 0 ? "Face Detector" : "standby (Audio FFT)");
        } else if (want != 0 && resends < kMaxResends) {
            int64_t requested_us, last_result_us;
            {
                std::lock_guard<std::mutex> lock(state_mutex);
                requested_us = state.requested_us;
                last_result_us = state.last_result_us;
            }
            const int64_t now = esp_timer_get_time();
            // Face mode: no result since the command. Standby: results still arriving.
            const bool unanswered = want > 0 ? last_result_us < requested_us
                                             : last_result_us > requested_us + 4000000;
            if (now - requested_us >= kResendUs && unanswered) {
                ++resends;
                send(want > 0 ? kM12FaceDetectorCommand : kM12StandbyCommand);
                ESP_LOGW(kTag, "no answer; resend %d/%d", resends, kMaxResends);
            }
        }
        if (want > 0) {
            const int64_t now = esp_timer_get_time();
            if (now - stats_logged_us >= 2000000) {
                std::lock_guard<std::mutex> lock(state_mutex);
                if (state.results != stats_results || stats_logged_us == 0)
                    ESP_LOGI(kTag, "faces: %lu results in 2 s, last num=%u kept=%u, rx=%lu lines=%lu invalid=%lu",
                             static_cast<unsigned long>(state.results - stats_results), state.num,
                             static_cast<unsigned>(state.count), static_cast<unsigned long>(rx_bytes),
                             static_cast<unsigned long>(lines), static_cast<unsigned long>(invalid_lines));
                stats_results = state.results;
                stats_logged_us = now;
            }
        }
        const int count = uart_read_bytes(kUart, bytes.data(), bytes.size(), pdMS_TO_TICKS(50));
        if (count <= 0) continue;
        const int64_t now = esp_timer_get_time();
        for (int i = 0; i < count; ++i) {
            if (!buffer.push(static_cast<char>(bytes[i]), line)) continue;
            const M12Line result = parse_m12_line(line);
            // The first two lines after a request show the real output format.
            if (raw_logged < 2 && result.kind != M12LineKind::reply && result.kind != M12LineKind::error) {
                ++raw_logged;
                ESP_LOGI(kTag, "raw %s: %.160s", result.kind == M12LineKind::faces ? "faces" :
                         result.kind == M12LineKind::invalid ? "invalid" : "other", line.c_str());
            }
            std::lock_guard<std::mutex> lock(state_mutex);
            ++lines;
            if (result.running[0]) std::memcpy(last_running, result.running, sizeof(last_running));
            switch (result.kind) {
            case M12LineKind::faces:
                ++state.results;
                ++state.result_seq;
                state.last_result_us = now;
                state.num = result.num;
                state.count = result.count;
                std::memcpy(state.faces, result.faces, sizeof(state.faces));
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

void m12_request_faces(bool on)
{
    wanted = on ? 1 : -1;
    ++wanted_seq;
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
    out["valid"] = state.valid;
    out["requested"] = state.requested > 0 ? "face_detector" : state.requested < 0 ? "standby" : "none";
    out["request_seq"] = state.request_seq;
    out["requested_us"] = state.requested_us;
    out["sends"] = sends;
    out["running"] = last_running;
    out["last_reply"] = last_reply;
    out["results"] = state.results;
    out["last_result_us"] = state.last_result_us;
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
    out["face_command"] = "{\"function\":\"Face Detector\",\"args\":\"\"}";
    out["standby_command"] = "{\"function\":\"Audio FFT\",\"args\":\"\"}";
    auto asr = out["asr_commands"].to<ArduinoJson::JsonObject>();
    asr["0x14 turn on"] = "face view + Face Detector";
    asr["0x15 turn off"] = "home + standby";
}
} // namespace collector
