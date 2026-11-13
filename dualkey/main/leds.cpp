// The DualKey's two WS2812B LEDs (G21, power enable G40 open-drain low = on, as in the official BSP).
//
// Event flashes (both LEDs):  press = short white blink;  group closed = green (good) / red (bad) 0.5 s;
//                             joystick push = short cyan;  both keys together = yellow (ignored).
// Idle status, every 2 s:     not provisioned = purple blink every 1 s;  no Wi-Fi = two orange blinks;
//                             Wi-Fi but no server = one blue blink;  connected = dark.
#include "app.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "led_strip.h"

namespace app {
namespace {
const char* TAG = "leds";
led_strip_handle_t s_strip;
QueueHandle_t s_flashes;

struct Rgb {
    uint8_t r, g, b;
    bool operator!=(const Rgb& o) const { return r != o.r || g != o.g || b != o.b; }
};
constexpr Rgb kOff{0, 0, 0};

Rgb status_colour(uint32_t now) {
    Config c = config_get();
    uint32_t t = now % 2000;
    if (c.wifi_count == 0 || !c.server[0] || !c.token[0]) return (now % 1000) < 100 ? Rgb{40, 0, 40} : kOff;
    if (!g_status.wifi_up.load()) return (t < 100 || (t >= 250 && t < 350)) ? Rgb{80, 30, 0} : kOff;
    if (!g_status.hello_ok.load()) return t < 150 ? Rgb{0, 0, 80} : kOff;
    return kOff;
}

void led_task(void*) {
    Rgb shown{1, 1, 1};
    Rgb overlay = kOff;
    uint32_t overlay_until = 0;
    bool overlay_on = false;
    for (;;) {
        uint32_t now = now_ms();
        Flash f;
        while (xQueueReceive(s_flashes, &f, 0) == pdTRUE) {
            uint32_t dur = 60;
            switch (f) {
                case Flash::Press: overlay = {50, 50, 50}; dur = 60; break;
                case Flash::Good: overlay = {0, 90, 0}; dur = 500; break;
                case Flash::Bad: overlay = {90, 0, 0}; dur = 500; break;
                case Flash::ModePush: overlay = {0, 50, 70}; dur = 150; break;
                case Flash::Chord: overlay = {70, 60, 0}; dur = 300; break;
            }
            overlay_until = now + dur;
            overlay_on = true;
        }
        if (overlay_on && (int32_t)(now - overlay_until) >= 0) overlay_on = false;
        Rgb want = overlay_on ? overlay : status_colour(now);
        if (want != shown) {
            for (int i = 0; i < 2; ++i) led_strip_set_pixel(s_strip, i, want.r, want.g, want.b);
            led_strip_refresh(s_strip);
            shown = want;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
}  // namespace

void leds_init() {
    gpio_config_t pwr = {};
    pwr.pin_bit_mask = 1ULL << kLedPowerGpio;
    pwr.mode = GPIO_MODE_OUTPUT_OD;
    gpio_config(&pwr);
    gpio_set_level((gpio_num_t)kLedPowerGpio, 0);  // low = LED power on
    gpio_hold_en((gpio_num_t)kLedPowerGpio);

    led_strip_config_t strip = {};
    strip.strip_gpio_num = kLedGpio;
    strip.max_leds = 2;
    strip.led_model = LED_MODEL_WS2812;
    strip.color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB;
    led_strip_rmt_config_t rmt = {};
    rmt.clk_src = RMT_CLK_SRC_DEFAULT;
    rmt.resolution_hz = 10 * 1000 * 1000;
    rmt.mem_block_symbols = 64;
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip, &rmt, &s_strip));
    led_strip_clear(s_strip);
    s_flashes = xQueueCreate(8, sizeof(Flash));
    xTaskCreate(led_task, "leds", 3072, nullptr, 3, nullptr);
    ESP_LOGI(TAG, "ready");
}

void leds_flash(Flash kind) {
    if (s_flashes) xQueueSend(s_flashes, &kind, 0);
}

}  // namespace app
