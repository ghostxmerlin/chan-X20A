// Chain DualKey remote for "hello": keys rate, joystick picks modes, events go to the ear server over Wi-Fi.
#include <cstdio>
#include <cstring>

#include "app.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "bootloader_random.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "nvs_flash.h"

namespace app {

SemaphoreHandle_t g_queue_lock;
dualkey::SignalQueue g_queue;
std::atomic<uint32_t> g_acked{0};
Status g_status;
std::atomic<uint32_t> g_joy_led_rgb{0};

namespace {
char s_device[24];
char s_boot[17];
portMUX_TYPE s_err_mux = portMUX_INITIALIZER_UNLOCKED;
}  // namespace

uint32_t now_ms() { return (uint32_t)(esp_timer_get_time() / 1000); }
const char* device_name() { return s_device; }
const char* boot_id() { return s_boot; }

void set_last_error(const char* text) {
    taskENTER_CRITICAL(&s_err_mux);
    snprintf(g_status.last_error, sizeof(g_status.last_error), "%s", text ? text : "");
    taskEXIT_CRITICAL(&s_err_mux);
}

void queue_init() { g_queue_lock = xSemaphoreCreateMutex(); }

uint32_t queue_push(const char* body, uint32_t t0_ms) {
    uint32_t seq = 0;
    with_queue([&](dualkey::SignalQueue& q) { seq = q.push(body, t0_ms); });
    ESP_LOGI("signal", "queued seq=%u %s", (unsigned)seq, body);
    return seq;
}

QueueStats queue_stats() {
    QueueStats s{};
    with_queue([&](dualkey::SignalQueue& q) {
        s.pending = q.size();
        s.dropped = q.dropped();
        s.last_seq = q.last_seq();
    });
    s.acked = g_acked.load();
    return s;
}

}  // namespace app

extern "C" void app_main(void) {
    using namespace app;
    // NVS holds the factory firmware's data as well: never erase it automatically.
    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) ESP_LOGE("main", "nvs_flash_init failed: %s (settings will not persist)", esp_err_to_name(err));

    uint8_t mac[6] = {};
    esp_efuse_mac_get_default(mac);
    snprintf(s_device, sizeof(s_device), "dualkey-%02x%02x%02x", mac[3], mac[4], mac[5]);
    // Boot id: Wi-Fi is not up yet, so turn on the SAR-ADC entropy source for a true random value.
    bootloader_random_enable();
    uint64_t r = ((uint64_t)esp_random() << 32) | esp_random();
    bootloader_random_disable();
    snprintf(s_boot, sizeof(s_boot), "%016llx", (unsigned long long)r);
    ESP_LOGI("main", "%s fw=%s boot=%s", s_device, DUALKEY_FW, s_boot);

    queue_init();
    config_init();
    leds_init();
    inputs_start();
    console_start();
    net_start();
}
