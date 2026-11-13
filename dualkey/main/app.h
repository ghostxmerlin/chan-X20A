// Shared state between the DualKey tasks (keys, chain joystick, LEDs, network, USB console).
#pragma once

#include <atomic>
#include <cstdint>

#include "dualkey_logic.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define DUALKEY_FW "dualkey-0.1.2"

namespace app {

// ---- board pins (M5Stack Chain DualKey C147 pin map; see README)
constexpr int kKey1Gpio = 0;    // KEY_1, the key farther from the lanyard hole: GOOD (+1)
constexpr int kKey2Gpio = 17;   // KEY_2, the key nearer the lanyard hole:   BAD  (-1)
constexpr int kLedGpio = 21;    // 2x WS2812B data
constexpr int kLedPowerGpio = 40;  // WS2812 power enable, open-drain, low = on (official bsp_ws2812_enable)
// G7/G8 are the 3-way switch sense inputs. Official warning: never configure them as high outputs, or the
// device cannot power off. This firmware never touches them.

uint32_t now_ms();

// ---- configuration (NVS namespace "soulkey"; never printed)
struct WifiEntry {
    char ssid[33];
    char pass[65];
};
struct Config {
    WifiEntry wifi[2];
    int wifi_count;
    char server[128];  // ws://host:port (path /signal is appended)
    char token[128];
    int joy_rotate;    // 0/90/180/270
};
void config_init();
Config config_get();                    // copy
bool config_apply_json(const char* line, char* err, size_t err_len);  // provisioning
uint32_t config_generation();           // bumps on every successful change
uint32_t wifi_generation();             // bumps only when the Wi-Fi list changes

// ---- identity
const char* device_name();  // dualkey-<last 6 hex of MAC>
const char* boot_id();      // random hex per boot

// ---- signal queue (guarded)
void queue_init();
uint32_t queue_push(const char* body, uint32_t t0_ms);
struct QueueStats {
    size_t pending;
    uint32_t dropped, last_seq, acked;
};
QueueStats queue_stats();
// Runs fn with the queue locked.
template <typename F>
void with_queue(F&& fn);

extern SemaphoreHandle_t g_queue_lock;
extern dualkey::SignalQueue g_queue;
extern std::atomic<uint32_t> g_acked;

template <typename F>
void with_queue(F&& fn) {
    xSemaphoreTake(g_queue_lock, portMAX_DELAY);
    fn(g_queue);
    xSemaphoreGive(g_queue_lock);
}

// ---- live status
struct Status {
    std::atomic<bool> wifi_up{false};
    std::atomic<bool> ws_up{false};
    std::atomic<bool> hello_ok{false};
    std::atomic<int> rssi{0};
    std::atomic<uint32_t> ip{0};
    std::atomic<int> wifi_index{-1};
    std::atomic<uint32_t> ws_connects{0};
    std::atomic<uint32_t> ws_failures{0};
    std::atomic<bool> joystick_found{false};
    std::atomic<int> joystick_bus{-1};
    std::atomic<int> joystick_id{0};
    std::atomic<int> joy_x{0}, joy_y{0};
    std::atomic<uint32_t> signals_sent{0};
    char last_error[48] = {};
    char mode[32] = {};  // last {"type":"mode","current":...}
};
extern Status g_status;
void set_last_error(const char* text);

// ---- LEDs
enum class Flash : uint8_t { Press, Good, Bad, ModePush, Chord };
void leds_init();
void leds_flash(Flash kind);

// ---- joystick LED colour requested by the network side (0xRRGGBB, 0 = off)
extern std::atomic<uint32_t> g_joy_led_rgb;

// ---- tasks
void inputs_start();   // keys + chain joystick
void net_start();      // Wi-Fi + WebSocket
void console_start();  // USB provisioning console

}  // namespace app
