// Keys (rating) and the Chain Joystick (modes, clicks).
//
// Keys: G0 = KEY_1 = good (+1), G17 = KEY_2 = bad (-1); active low, sampled every 5 ms, 3-sample debounce.
// Both keys down together is not a rating: the open group is dropped (reserved for a later reset gesture).
//
// Joystick: M5Stack's ESP-IDF M5Chain port (components/m5chain), used the way the official
// M5DualKey-UserDemo chain_bus.cpp and the M5Chain Joystick_Example do: enumerate both Chain ports
// (UART2 G6/G5 and UART1 G48/G47), find CHAIN_JOYSTICK_TYPE_CODE, set the button to report mode with a
// 200 ms double-click window and 3 s long press, then poll getJoystickMappedInt8Value every 20 ms and drain
// getJoystickButtonPressStatus for single / double / long.
#include <cstring>
#include <string>

#include "ChainJoystick/ChainJoystick.hpp"
#include "app.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/task.h"

// Set to 1 to log joystick read-back, button edges and decoded events (off by default).
#ifndef DUALKEY_DIAG
#define DUALKEY_DIAG 0
#endif

namespace app {
namespace {
const char* TAG = "inputs";

void emit_group(const dualkey::RatingGroup& g) {
    char body[dualkey::kBodyMax];
    if (dualkey::format_rating(g, body, sizeof(body)) > 0) queue_push(body, g.first_ms);
    leds_flash(g.polarity > 0 ? Flash::Good : Flash::Bad);
}

void key_task(void*) {
    gpio_config_t io = {};
    io.pin_bit_mask = (1ULL << kKey1Gpio) | (1ULL << kKey2Gpio);
    io.mode = GPIO_MODE_INPUT;
    io.pull_up_en = GPIO_PULLUP_ENABLE;
    gpio_config(&io);

    dualkey::Debouncer keys[2] = {dualkey::Debouncer(3), dualkey::Debouncer(3)};
    const int gpios[2] = {kKey1Gpio, kKey2Gpio};
    const int polarity[2] = {+1, -1};
    dualkey::RatingGrouper grouper;
    dualkey::RatingGroup closed;
    bool chord = false;
    TickType_t wake = xTaskGetTickCount();
    for (;;) {
        uint32_t now = now_ms();
        for (int k = 0; k < 2; ++k) {
            int edge = keys[k].update(gpio_get_level((gpio_num_t)gpios[k]) == 0);
            if (edge != +1) continue;
            if (keys[1 - k].pressed()) {
                if (!chord) {
                    ESP_LOGI(TAG, "both keys: open group dropped");
                    leds_flash(Flash::Chord);
                }
                grouper.cancel();
                chord = true;
                continue;
            }
            if (chord) continue;
            leds_flash(Flash::Press);
            ESP_LOGI(TAG, "key %s press", polarity[k] > 0 ? "good" : "bad");
            if (grouper.press(polarity[k], now, &closed)) emit_group(closed);
        }
        if (chord && !keys[0].pressed() && !keys[1].pressed()) chord = false;
        if (grouper.poll(now, &closed)) emit_group(closed);
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(5));
    }
}

void emit_click(dualkey::ClickType t, uint32_t t0) {
    char body[dualkey::kBodyMax];
    dualkey::format_click(t, body, sizeof(body));
    queue_push(body, t0);
    leds_flash(Flash::Press);
}

class JoyBus : public ChainJoystick {};
JoyBus s_bus[2];
const char* kBusName[2] = {"port-uart2", "port-uart1"};

uint32_t dir_colour(dualkey::Dir d) {
    switch (d) {
        case dualkey::Dir::Up: return 0x603000;
        case dualkey::Dir::Down: return 0x000060;
        case dualkey::Dir::Left: return 0x006000;
        case dualkey::Dir::Right: return 0x400060;
        default: return 0;
    }
}

void set_joy_led(int bus, int id, uint32_t rgb) {
    uint8_t c[3] = {(uint8_t)(rgb >> 16), (uint8_t)(rgb >> 8), (uint8_t)rgb};
    uint8_t op = 0;
    s_bus[bus].setRGBValue(id, 0, 1, c, 3, &op, 50);
}

bool scan(int& bus_out, int& id_out) {
    for (int b = 0; b < 2; ++b) {
        if (!s_bus[b].isDeviceConnected(3, 50)) continue;
        uint16_t n = 0;
        if (s_bus[b].getDeviceNum(&n, 500) != CHAIN_OK || n == 0 || n > 16) continue;
        device_info_t infos[16] = {};
        device_list_t list = {n, infos};
        if (!s_bus[b].getDeviceList(&list, 100)) continue;
        for (uint16_t i = 0; i < n; ++i) {
            ESP_LOGI(TAG, "%s device %u type 0x%04x", kBusName[b], infos[i].id, (unsigned)infos[i].device_type);
            if (infos[i].device_type == CHAIN_JOYSTICK_TYPE_CODE) {
                bus_out = b;
                id_out = infos[i].id;
                return true;
            }
        }
    }
    return false;
}

void configure(int bus, int id) {
    uint8_t op = 0;
    chain_status_t a = s_bus[bus].setJoystickButtonTriggerInterval(id, BUTTON_DOUBLE_CLICK_TIME_200MS,
                                                                   BUTTON_LONG_PRESS_TIME_3S, &op, 100);
    chain_status_t b = s_bus[bus].setJoystickButtonMode(id, CHAIN_BUTTON_REPORT_MODE, &op, 100);
    uint8_t op_mode = op;
    s_bus[bus].setRGBLight(id, 60, &op, CHAIN_SAVE_FLASH_DISABLE, 100);
    set_joy_led(bus, id, 0);
    uint8_t fw = 0;
    s_bus[bus].getFirmwareVersion(id, &fw, 100);
    ESP_LOGI(TAG, "joystick on %s id %d fw=%u: interval=%d mode=%d(op %u)", kBusName[bus], id, fw, a, b, op_mode);
#if DUALKEY_DIAG
    button_double_click_time_t dbl = BUTTON_DOUBLE_CLICK_TIME_100MS;
    button_long_press_time_t lng = BUTTON_LONG_PRESS_TIME_3S;
    chain_status_t c = s_bus[bus].getJoystickButtonTriggerInterval(id, &dbl, &lng, 100);
    chain_button_mode_t mode = CHAIN_BUTTON_NONE_REPORT_MODE;
    chain_status_t d = s_bus[bus].getJoystickButtonMode(id, &mode, 100);
    ESP_LOGI(TAG, "diag read back: interval=%d dbl=%d long=%d, mode=%d value=%d", c, (int)dbl, (int)lng, d, (int)mode);
#endif
}

void chain_task(void*) {
    s_bus[0].begin(UART_NUM_2, 115200, 6, 5);
    s_bus[1].begin(UART_NUM_1, 115200, 48, 47);
    dualkey::DirDetector detector;
    int bus = -1, id = 0, fails = 0;
    uint32_t last_scan = 0, led_shown = 0xFFFFFFFF, flash_until = 0;
    bool flashing = false;
    dualkey::ClickFuser clicks;
    [[maybe_unused]] uint8_t btn_level = 0;
    [[maybe_unused]] uint32_t btn_down_ms = 0;
    for (;;) {
        uint32_t now = now_ms();
        if (bus < 0) {
            if (last_scan == 0 || now - last_scan >= 2000) {
                last_scan = now ? now : 1;
                if (scan(bus, id)) {
                    configure(bus, id);
                    detector = dualkey::DirDetector();
                    fails = 0;
                    led_shown = 0xFFFFFFFF;
                    g_status.joystick_found = true;
                    g_status.joystick_bus = bus;
                    g_status.joystick_id = id;
                }
            }
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        int8_t x8 = 0, y8 = 0;
        if (s_bus[bus].getJoystickMappedInt8Value(id, &x8, &y8, 50) == CHAIN_OK) {
            fails = 0;
            int x = x8, y = y8;
            dualkey::mount_correct(config_get().joy_rotate, x, y);  // physical direction (see kMountRotate)
            g_status.joy_x = x;
            g_status.joy_y = y;
            uint32_t start = now;
            dualkey::Dir d = detector.update(x, y, now, &start);
            if (d != dualkey::Dir::None) {
                char body[dualkey::kBodyMax];
                dualkey::format_mode(d, body, sizeof(body));
                queue_push(body, start);
                leds_flash(Flash::ModePush);
                set_joy_led(bus, id, dir_colour(d));
                flashing = true;
                flash_until = now + 400;
            }
        } else if (++fails > 25) {
            ESP_LOGW(TAG, "joystick lost on %s", kBusName[bus]);
            bus = -1;
            g_status.joystick_found = false;
            set_last_error("joystick lost");
            continue;
        }
        // Button level, polled every loop: our own long press fires at 1 s (the joystick's is 3 s minimum).
        dualkey::ClickType click;
        uint8_t level = 0;
        if (s_bus[bus].getJoystickButtonStatus(id, &level, 50) == CHAIN_OK) {
#if DUALKEY_DIAG
            if (level != btn_level) {
                if (level) btn_down_ms = now;
                ESP_LOGI(TAG, "diag: joystick button %s%s", level ? "down" : "up",
                         level ? "" : (std::string(", held ") + std::to_string(now - btn_down_ms) + " ms").c_str());
            }
#endif
            btn_level = level;
            if (clicks.level(level != 0, now, &click)) emit_click(click, now);
        }
        chain_button_press_type_t press;
        while (s_bus[bus].getJoystickButtonPressStatus(id, &press)) {
#if DUALKEY_DIAG
            ESP_LOGI(TAG, "diag: joystick event value=%u (0 single, 1 double, 2 long)", (unsigned)press);
#endif
            dualkey::ClickType t = press == CHAIN_BUTTON_PRESS_DOUBLE ? dualkey::ClickType::Double
                                   : press == CHAIN_BUTTON_PRESS_LONG ? dualkey::ClickType::Long
                                                                      : dualkey::ClickType::Single;
            if (clicks.joystick_event(t, now_ms(), &click)) emit_click(click, now_ms());
        }
        if (s_bus[bus].getEnumPleaseNum() > 0) {
            ESP_LOGI(TAG, "chain re-enumeration requested");
            bus = -1;
            g_status.joystick_found = false;
            continue;
        }
        if (flashing && (int32_t)(now - flash_until) >= 0) {
            flashing = false;
            led_shown = 0xFFFFFFFF;
        }
        uint32_t want = g_joy_led_rgb.load();
        if (!flashing && want != led_shown) {
            set_joy_led(bus, id, want);
            led_shown = want;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
}  // namespace

void inputs_start() {
    xTaskCreate(key_task, "keys", 3072, nullptr, 6, nullptr);
    xTaskCreate(chain_task, "chain", 4096, nullptr, 5, nullptr);
}

}  // namespace app
