/* SPDX-License-Identifier: MIT */
#include "asr_unit.h"
#include "playback.h"
#include "hearing.h"
#include "interaction.h"
#include "remote.h"
#include "asr_motion.h"
#include "asr_mode_words.h"
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
};
AsrYawPlan yaw_plan;
BootTracking boot_tracking; // only asr_task changes it; asr_json reads the result string

// Mode words go to mind over /mouth (hello modes step 1): queued for the /mouth task, never sent
// from here (digit < 0 = value null). false = /mouth is offline, reported as "(offline)".
bool send_mode_input(const char* input, int digit)
{
    return mouth::mode_link_send_input("asr", input, digit) != 0;
}
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
            // A spoken motion word (up/down tracking, the yaw words) wins over boot auto-tracking,
            // which then never fires. open/close are mode inputs since step 1 and do not cancel it.
            if (asr_motion_word(frame.id, frame.has_value) != AsrMotionWord::None) boot_tracking.cancel();
            // Consume each newly parsed frame once, independent of webpage/LVGL polling.
            // Local uses left: volume (playback), turn on/off (interaction). hearing::asr_command
            // and voice next are no-ops since step 1 (their words are mode inputs).
            playback_asr_command(frame.id,frame.has_value);
            hearing::asr_command(frame.id,frame.has_value);
            interaction::asr_command(frame.id,frame.has_value);
            int digit = -1;
            const char* mode_input = asr_mode_input(frame.id, frame.has_value, &digit);
            const bool mode_sent = mode_input && send_mode_input(mode_input, digit);
            const char* word = asr_word(frame.id);
            const auto outcome = yaw_plan.command(asr_motion_word(frame.id, frame.has_value), port, esp_timer_get_time());
            event.action = outcome.action; event.result = outcome.result;
            {
                // Published after dispatch so every reader sees the word together with its outcome.
                std::lock_guard<std::mutex> lock(history_mutex);
                history[event.seq % kHistory] = event;
                if (outcome.action) {
                    motion_state = MotionState{event.seq, word, outcome.action, outcome.result, yaw_plan.stage_name(),
                                               yaw_plan.stage() == AsrYawPlan::Stage::Idle ? "" : yaw_plan.last_result()};
                }
            }
            latest = (event.seq << 8) | frame.id;
            ++frames;
            rx_seen = true;
            if (frame.has_value)
                ESP_LOGI(kTag, "#%lu id=0x%02X value=0x%02X %s", static_cast<unsigned long>(event.seq), frame.id,
                         frame.value, word ? word : "(unknown)");
            else if (outcome.action)
                ESP_LOGI(kTag, "#%lu id=0x%02X %s -> %s %s", static_cast<unsigned long>(event.seq), frame.id,
                         word ? word : "(unknown)", outcome.action, outcome.result);
            else if (mode_input)
                ESP_LOGI(kTag, "#%lu id=0x%02X %s -> mode %s%s", static_cast<unsigned long>(event.seq), frame.id,
                         word ? word : "(unknown)", mode_input, mode_sent ? "" : " (offline)");
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
        // Derived from the id, so the history keeps no extra bytes per event.
        int digit = -1;
        if (const char* mode = asr_mode_input(e.frame.id, e.frame.has_value, &digit)) {
            item["mode_input"] = mode;
            if (digit >= 0) item["mode_value"] = digit;
        }
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
    reactions["0x14 turn on"] = "M12 face view";
    reactions["0x15 turn off"] = "home";
    out["reaction_sound"] = false;
    out["boot_tracking"] = boot_tracking.result();
    // Mode words (hello modes step 1, owner 2026-11-14): sent to mind as /mouth mode_input; mind
    // decides what they do in the current mode. Their old local uses are gone.
    auto modes = out["mode_words"].to<ArduinoJson::JsonObject>();
    modes["0x07 forward"] = "forward (enter talk show)";
    modes["0x09 backward"] = "backward (leave talk show)";
    modes["0x16 play"] = "play (conversation)";
    modes["0x17 pause"] = "pause (conversation)";
    modes["0x10 open"] = "open (settings; no longer tracking on)";
    modes["0x11 close"] = "close (settings; no longer tracking off)";
    modes["0x12 start"] = "start (event-driven; no longer hearing start)";
    modes["0x13 stop"] = "stop (event-driven; no longer hearing stop)";
    modes["0x1B next"] = "next (per mode; no longer a random clip)";
    modes["0x1A previous"] = "previous (per mode)";
    modes["0x20-0x29 zero..nine"] = "digit 0-9 (presets)";
    // Spoken yaw words (2026-11-13), robot's own left/right like the web buttons.
    auto yaw = out["yaw_words"].to<ArduinoJson::JsonObject>();
    yaw["0x01 up"] = "tracking_on";
    yaw["0x02 down"] = "tracking_off";
    yaw["0x03 left"] = "one 45 deg notch left";
    yaw["0x04 turn left"] = "two notches left, clipped at -90 deg";
    yaw["0x05 right"] = "one 45 deg notch right";
    yaw["0x06 turn right"] = "two notches right, clipped at +90 deg";
    yaw["0x08 front"] = "centre; when turned around: return turn, then centre";
    yaw["0x0A back"] = "timed turn-around, centring first when off centre";
}
} // namespace collector
