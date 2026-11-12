/* SPDX-License-Identifier: MIT */
#include "asr_unit.h"
#include "playback.h"
#include "hearing.h"
#include "interaction.h"
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <array>
#include <atomic>
#include <cstdio>
#include <mutex>

namespace collector {
namespace {
constexpr char kTag[] = "AsrUnit";
constexpr uart_port_t kUart = UART_NUM_2; // UART0: M12, UART1: servo bus.
constexpr int kBaud = 115200;
// The unit's TX is on GPIO8 of the top black port (Port B), confirmed on the dev
// robot 2026-11-07. GPIO9 (the unit's RX) is never driven: receive only.
constexpr gpio_num_t kRx = GPIO_NUM_8;
constexpr size_t kHistory = 16;

struct Event {
    uint32_t seq = 0;
    int64_t timestamp_us = 0;
    AsrFrame frame;
};

std::atomic_bool driver_ok{false}, rx_seen{false};
std::atomic<uint32_t> frames{0}, discarded{0}, latest{0};
std::mutex history_mutex;
std::array<Event, kHistory> history;

bool install_uart()
{
    uart_config_t config{};
    config.baud_rate = kBaud;
    config.data_bits = UART_DATA_8_BITS;
    config.parity = UART_PARITY_DISABLE;
    config.stop_bits = UART_STOP_BITS_1;
    config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    config.source_clk = UART_SCLK_DEFAULT;
    esp_err_t result = uart_driver_install(kUart, 512, 0, 0, nullptr, 0);
    if (result == ESP_OK) result = uart_param_config(kUart, &config);
    if (result == ESP_OK) result = uart_set_pin(kUart, UART_PIN_NO_CHANGE, kRx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "UART2 unavailable: %s", esp_err_to_name(result));
        return false;
    }
    gpio_pullup_en(kRx);
    return true;
}

void asr_task(void*)
{
    // Installed from this task (pinned to core 1) so the UART interrupt is allocated
    // on core 1: core 0 must keep free inputs for Wi-Fi's lazily allocated AES interrupt.
    if (!install_uart()) vTaskDelete(nullptr);
    driver_ok = true;
    ESP_LOGI(kTag, "Unit ASR on Port B: UART2 %d 8N1, RX GPIO%d, TX unused (core %d)", kBaud, kRx, xPortGetCoreID());
    AsrParser parser;
    std::array<uint8_t, 64> buffer{};
    uint32_t seq = 0;
    while (true) {
        interaction::tick(esp_timer_get_time());
        const int count = uart_read_bytes(kUart, buffer.data(), buffer.size(), pdMS_TO_TICKS(100));
        for (int i = 0; i < count; ++i) {
            AsrFrame frame;
            if (!parser.push(buffer[i], frame)) continue;
            Event event{++seq, esp_timer_get_time(), frame};
            {
                std::lock_guard<std::mutex> lock(history_mutex);
                history[event.seq % kHistory] = event;
            }
            latest = (event.seq << 8) | frame.id;
            ++frames;
            rx_seen = true;
            // Consume each newly parsed frame once, independent of webpage/LVGL polling.
            playback_asr_command(frame.id,frame.has_value);
            hearing::asr_command(frame.id,frame.has_value);
            interaction::asr_command(frame.id,frame.has_value);
            const char* word = asr_word(frame.id);
            if (frame.has_value)
                ESP_LOGI(kTag, "#%lu id=0x%02X value=0x%02X %s", static_cast<unsigned long>(event.seq), frame.id,
                         frame.value, word ? word : "(unknown)");
            else
                ESP_LOGI(kTag, "#%lu id=0x%02X %s", static_cast<unsigned long>(event.seq), frame.id,
                         word ? word : "(unknown)");
        }
        discarded = parser.discarded();
    }
}
} // namespace

void asr_init()
{
    if (xTaskCreatePinnedToCore(asr_task, "collector_asr", 4096, nullptr, 2, nullptr, 1) != pdPASS)
        ESP_LOGE(kTag, "ASR task unavailable");
}

uint32_t asr_latest() { return latest.load(); }

void asr_json(ArduinoJson::JsonObject out,uint32_t after)
{
    out["valid"] = driver_ok.load();
    out["rx_gpio"] = static_cast<int>(kRx);
    out["rx_seen"] = rx_seen.load();
    out["frames"] = frames.load();
    out["discarded_bytes"] = discarded.load();
    std::array<Event, kHistory> copy;
    {
        std::lock_guard<std::mutex> lock(history_mutex);
        copy = history;
    }
    const uint32_t last_seq = latest.load() >> 8;
    auto events = out["events"].to<ArduinoJson::JsonArray>();
    for (uint32_t seq = last_seq >= kHistory ? last_seq - kHistory + 1 : 1; seq <= last_seq; ++seq) {
        const Event& e = copy[seq % kHistory];
        if (e.seq != seq || seq <= after) continue;
        auto item = events.add<ArduinoJson::JsonObject>();
        item["seq"] = e.seq;
        item["timestamp_us"] = e.timestamp_us;
        item["id"] = e.frame.id;
        if (const char* word = asr_word(e.frame.id)) item["word"] = word;
        if (e.frame.has_value) item["value"] = e.frame.value;
    }
}

void asr_info_json(ArduinoJson::JsonObject out)
{
    out["device"] = "M5Stack Unit ASR (CI-03T), factory English vocabulary";
    out["port"] = "B (top black)";
    out["uart"] = 2;
    out["baud"] = kBaud;
    out["rx_gpio"] = static_cast<int>(kRx);
    out["tx"] = "unused";
    out["frame"] = "AA 55 <id> 55 AA; AA 55 <id> <value> 55 AA";
    out["history"] = kHistory;
    // Independent ASR home reactions; display only, no sound
    // (the unit already speaks its own reply).
    auto reactions = out["screen_reactions"].to<ArduinoJson::JsonObject>();
    reactions["0x07 forward"] = "heart";
    reactions["0x09 backward"] = "reject";
    reactions["0x14 turn on"] = "M12 face view";
    reactions["0x15 turn off"] = "home";
    out["reaction_sound"] = false;
    out["open_action"] = "horizontal_face_tracking_on";
    out["close_action"] = "horizontal_face_tracking_off";
    out["next_action"] = "random_local_voice_after_800ms";
    out["start_action"] = "continuous_hearing_start";
    out["stop_action"] = "continuous_hearing_stop";
}
} // namespace collector
