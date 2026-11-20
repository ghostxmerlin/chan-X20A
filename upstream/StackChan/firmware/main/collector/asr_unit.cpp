/* SPDX-License-Identifier: MIT */
#include "asr_unit.h"
#include "interaction.h"
#include "remote.h"
#include "asr_motion.h"
#include "boot_tracking.h"
#include "interaction_policy.h"
#include "m12_unit.h"
#include "mouth_link.h"
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <array>
#include <atomic>
#include <cstdio>
#include <mutex>

namespace collector {
namespace {
constexpr char kTag[] = "AsrUnit";
constexpr uart_port_t kUart = UART_NUM_2; // UART0: M12, UART1: servo bus.
constexpr int kBaud = 115200;
// The owner's StickS3 voice module (~/exia/stick/docs/voice-protocol.md) on CoreS3 Port C since 2026-11-20 (the owner
// moved the M12 off Port C; it was the Unit ASR on Port B G8). White wire G18 = our RX (the Stick's TX), yellow G17 =
// our TX (the Stick's RX, pulled up there; for its control frames E0-E3, nothing is sent yet).
constexpr gpio_num_t kRx = GPIO_NUM_18;
constexpr gpio_num_t kTx = GPIO_NUM_17;
constexpr size_t kHistory = 16;

struct Event {
    uint32_t seq = 0;
    int64_t timestamp_us = 0;
    AsrFrame frame;
    const char* action = nullptr; // spoken yaw word: what it asked for and what happened
    const char* result = nullptr;
};

// Spoken yaw words go through the same remote_* entry points as the web buttons; the motor
// task stays the only servo UART owner. Only asr_task touches the plan.
struct RemoteYawPort {
    AsrYawStatus status() { return remote_yaw_status(); }
    bool tracking(bool on) { return interaction::set_mode("tracking", on) == ESP_OK; }
    bool notches(int n, uint32_t& seq) { return remote_yaw_notches(n, kWebYawDefaultSpeed, &seq) == ESP_OK; }
    bool centre(uint32_t& seq) { return remote_yaw_slot(0, kWebYawDefaultSpeed, &seq) == ESP_OK; }
    bool turn(uint32_t& seq) { return remote_turn_yaw(TimedYawTurn::kDefaultMs, &seq) == ESP_OK; }
    bool slot(int s, uint32_t& seq) { return remote_yaw_slot(s, kWebYawDefaultSpeed, &seq) == ESP_OK; }
};
// 0.5.10 robot command line (asr_yaw_request): one ask at a time, taken by asr_task between UART reads.
std::mutex ask_caller;                     // one caller at a time
std::mutex ask_mutex;                      // the fields below
SemaphoreHandle_t ask_done = nullptr;      // given by asr_task after each answer
StaticSemaphore_t ask_done_buffer;
uint32_t ask_token = 0, ask_taken = 0, ask_answered = 0;
AsrMotionWord ask_word = AsrMotionWord::None;
int ask_slot = 0;
AsrMotionOutcome ask_outcome;
AsrYawPlan yaw_plan;
BootTracking boot_tracking; // only asr_task changes it; asr_json reads the result string

struct MotionState {
    uint32_t seq = 0;           // ASR event that started the latest action
    const char* word = "";
    const char* action = "";
    const char* result = "";
    const char* stage = "idle";
    const char* plan_result = "";
};
MotionState motion_state; // under history_mutex

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
    if (result == ESP_OK) result = uart_set_pin(kUart, kTx, kRx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
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
    ESP_LOGI(kTag, "voice module (StickS3) on Port C: UART2 %d 8N1, RX GPIO%d, TX GPIO%d (core %d)", kBaud, kRx, kTx, xPortGetCoreID());
    AsrParser parser;
    std::array<uint8_t, 64> buffer{};
    uint32_t seq = 0;
    RemoteYawPort port;
    while (true) {
        interaction::tick(esp_timer_get_time());
        if (!boot_tracking.done()) {
            // Boot auto-tracking (user, 2026-11-13): with an M12, arm the servos and track once.
            switch (boot_tracking.tick(esp_timer_get_time(), m12_snapshot().online, port.status().control)) {
            case BootTracking::Action::Arm: {
                const bool ok = remote_arm(true);
                boot_tracking.armed(ok);
                ESP_LOGI(kTag, "boot tracking: M12 online, servos %s", ok ? "armed" : "refused");
                break;
            }
            case BootTracking::Action::Track:
                // Vision first, so "down" later turns tracking off but leaves detection on.
                boot_tracking.tracked(interaction::set_mode("vision", true) == ESP_OK && port.tracking(true));
                break;
            default: break;
            }
            if (boot_tracking.done()) ESP_LOGI(kTag, "boot tracking: %s", boot_tracking.result());
        }
        {
            // 0.5.10: a robot command's yaw word / slot, through the same plan as a spoken word.
            AsrMotionWord word = AsrMotionWord::None;
            int slot = 0;
            uint32_t token = 0;
            {
                std::lock_guard<std::mutex> lock(ask_mutex);
                if (ask_token != ask_taken) { ask_taken = token = ask_token; word = ask_word; slot = ask_slot; }
            }
            if (token) {
                if (word != AsrMotionWord::None) boot_tracking.cancel();
                const auto outcome = word == AsrMotionWord::None ? yaw_plan.slot(slot, port)
                                                                 : yaw_plan.command(word, port, esp_timer_get_time());
                {
                    std::lock_guard<std::mutex> lock(history_mutex);
                    if (outcome.action)
                        motion_state = MotionState{0, "cmd", outcome.action, outcome.result, yaw_plan.stage_name(),
                                                   yaw_plan.stage() == AsrYawPlan::Stage::Idle ? "" : yaw_plan.last_result()};
                }
                ESP_LOGI(kTag, "cmd %s -> %s", outcome.action ? outcome.action : "?", outcome.result ? outcome.result : "?");
                { std::lock_guard<std::mutex> lock(ask_mutex); ask_outcome = outcome; ask_answered = token; }
                xSemaphoreGive(ask_done);
            }
        }
        if (const char* done = yaw_plan.tick(port, esp_timer_get_time())) {
            ESP_LOGI(kTag, "#%lu %s plan -> %s", static_cast<unsigned long>(motion_state.seq), motion_state.action, done);
            std::lock_guard<std::mutex> lock(history_mutex);
            motion_state.stage = yaw_plan.stage_name(); motion_state.plan_result = done;
        }
        const int count = uart_read_bytes(kUart, buffer.data(), buffer.size(), pdMS_TO_TICKS(100));
        for (int i = 0; i < count; ++i) {
            AsrFrame frame;
            if (!parser.push(buffer[i], frame)) continue;
            Event event{++seq, esp_timer_get_time(), frame};
            // The StickS3 (owner 2026-11-20): PROVIDENCE = being called (the server opens a chat, speech stops here as
            // with the Unit ASR's hello); its other words are only reported (telemetry asr.events, the log). No
            // factory word does anything any more: no motion, no mode input, no digit, no volume.
            const AsrAction action = asr_action(frame);
            if (action == AsrAction::Call) mouth::asr_wake(asr_word(frame.id));
            const char* word = asr_word(frame.id);
            {
                std::lock_guard<std::mutex> lock(history_mutex);
                history[event.seq % kHistory] = event;
            }
            latest = (event.seq << 8) | frame.id;
            ++frames;
            rx_seen = true;
            if (frame.has_value)
                ESP_LOGI(kTag, "#%lu id=0x%02X value=0x%02X (ignored)", static_cast<unsigned long>(event.seq), frame.id,
                         frame.value);
            else
                ESP_LOGI(kTag, "#%lu id=0x%02X %s%s", static_cast<unsigned long>(event.seq), frame.id,
                         word ? word : "(unknown)", action == AsrAction::Call ? " -> called" : "");
        }
        discarded = parser.discarded();
    }
}
} // namespace

void asr_init()
{
    ask_done = xSemaphoreCreateBinaryStatic(&ask_done_buffer);
    if (xTaskCreatePinnedToCore(asr_task, "collector_asr", 4096, nullptr, 2, nullptr, 1) != pdPASS)
        ESP_LOGE(kTag, "ASR task unavailable");
}

uint32_t asr_latest() { return latest.load(); }

AsrMotionOutcome asr_yaw_request(AsrMotionWord word, int slot, uint32_t wait_ms)
{
    if (!ask_done || !driver_ok.load()) return {nullptr, "unavailable"};
    std::lock_guard<std::mutex> caller(ask_caller);
    uint32_t token;
    {
        std::lock_guard<std::mutex> lock(ask_mutex);
        token = ++ask_token ? ask_token : ++ask_token;      // 0 means "nothing asked"
        ask_word = word; ask_slot = slot;
    }
    xSemaphoreTake(ask_done, 0);                             // an older answer's give
    const TickType_t until = xTaskGetTickCount() + pdMS_TO_TICKS(wait_ms);
    for (;;) {
        const TickType_t now = xTaskGetTickCount();
        const TickType_t left = int32_t(until - now) > 0 ? until - now : 0;
        const bool given = xSemaphoreTake(ask_done, left) == pdTRUE;
        std::lock_guard<std::mutex> lock(ask_mutex);
        if (ask_answered == token) return ask_outcome;
        if (!given) {
            if (ask_taken != token) ask_taken = token;        // withdrawn: asr_task never runs it
            return {nullptr, "timeout"};
        }
    }
}

void asr_json(ArduinoJson::JsonObject out,uint32_t after)
{
    out["valid"] = driver_ok.load();
    out["rx_gpio"] = static_cast<int>(kRx);
    out["tx_gpio"] = static_cast<int>(kTx);
    out["rx_seen"] = rx_seen.load();
    out["frames"] = frames.load();
    out["discarded_bytes"] = discarded.load();
    std::array<Event, kHistory> copy;
    MotionState motion;
    {
        std::lock_guard<std::mutex> lock(history_mutex);
        copy = history;
        motion = motion_state;
    }
    auto yaw = out["motion"].to<ArduinoJson::JsonObject>();
    yaw["seq"] = motion.seq; yaw["word"] = motion.word; yaw["action"] = motion.action;
    yaw["result"] = motion.result; yaw["plan"] = motion.stage; yaw["plan_result"] = motion.plan_result;
    // Facing lives in remote.motor.yaw_turn.facing; this path runs at 20 Hz and never takes the motor lock.
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
        if (e.action) { item["action"] = e.action; item["result"] = e.result; }
        if (asr_action(e.frame) == AsrAction::Call) item["called"] = true;
    }
}

void asr_info_json(ArduinoJson::JsonObject out)
{
    out["device"] = "StickS3 voice module (Unit ASR frame), the owner's own words";
    out["port"] = "C (the M12 moved to Port A on 2026-11-20)";
    out["uart"] = 2;
    out["baud"] = kBaud;
    out["rx_gpio"] = static_cast<int>(kRx);
    out["tx_gpio"] = static_cast<int>(kTx);
    out["tx"] = "wired, nothing sent (control frames E0-E3 not used yet)";
    out["frame"] = "AA 55 <id> 55 AA; AA 55 <id> <value> 55 AA";
    out["history"] = kHistory;
    out["boot_tracking"] = boot_tracking.result();
    auto words = out["words"].to<ArduinoJson::JsonObject>();
    words["0x53 providence"] = "called: the server opens a chat (and speech stops)";
    words["0x50 0x51 0x52 0x55 0x5A 0x5E 0x5F"] = "heard: reported only";
    words["other ids"] = "ignored (the factory vocabulary is off)";
}
} // namespace collector
