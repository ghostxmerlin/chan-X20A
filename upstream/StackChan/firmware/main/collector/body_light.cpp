/* SPDX-License-Identifier: MIT */
#include "body_light.h"
#include "hal/hal.h"
#include <atomic>
#include <esp_log.h>
#include <esp_timer.h>
#include <mutex>

namespace collector::light {
namespace {
constexpr char kTag[] = "BodyLight";
std::mutex mutex;
uint8_t rgb[3] = {};
int64_t changed_us = 0;
uint32_t sets = 0;
}

bool set(uint8_t r, uint8_t g, uint8_t b)
{
    if (!GetHAL().servoPowerControlReady()) return false;   // no base IO expander: no LEDs either
    std::lock_guard<std::mutex> lock(mutex);
    GetHAL().showRgbColor(r, g, b);
    rgb[0] = r; rgb[1] = g; rgb[2] = b; changed_us = esp_timer_get_time(); ++sets;
    ESP_LOGI(kTag, "light %u %u %u", r, g, b);
    return true;
}

void json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(mutex);
    out["ready"] = GetHAL().servoPowerControlReady();
    out["r"] = rgb[0]; out["g"] = rgb[1]; out["b"] = rgb[2];
    out["on"] = rgb[0] || rgb[1] || rgb[2];
    out["changed_us"] = changed_us; out["sets"] = sets;
    out["target"] = "light";
}
}
