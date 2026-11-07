/* SPDX-License-Identifier: MIT */
#include "temperature.h"
#include "temperature_sample.h"
#include <driver/temperature_sensor.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mutex>

namespace collector {
namespace {
std::mutex mutex;
TemperatureSample sample;
constexpr char kTag[] = "ChipTemperature";
void temperature_task(void*) {
    // This task is the sole owner of the IDF driver; HTTP only copies a snapshot.
    temperature_sensor_handle_t handle = nullptr;
    const temperature_sensor_config_t config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 100);
    esp_err_t error = temperature_sensor_install(&config, &handle);
    if (error == ESP_OK) error = temperature_sensor_enable(handle);
    {
        std::lock_guard<std::mutex> lock(mutex);
        sample.available = error == ESP_OK; sample.error = error;
    }
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Sensor unavailable: %s", esp_err_to_name(error));
        if (handle) temperature_sensor_uninstall(handle);
        vTaskDelete(nullptr); return;
    }
    ESP_LOGI(kTag, "ESP32-S3 internal sensor ready (20..100 C, 1 Hz)");
    while (true) {
        float value = 0;
        error = temperature_sensor_get_celsius(handle, &value);
        {
            std::lock_guard<std::mutex> lock(mutex);
            sample.update(value, error, esp_timer_get_time());
        }
        // No catch-up loop or coupling to audio, IMU, M12, or actuator tasks.
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
}
void temperature_init() {
    if (xTaskCreatePinnedToCore(temperature_task, "collector_temp", 3072, nullptr, 1, nullptr, 1) != pdPASS) {
        std::lock_guard<std::mutex> lock(mutex);
        sample.error = ESP_ERR_NO_MEM;
        ESP_LOGW(kTag, "Temperature task unavailable");
    }
}
void temperature_json(ArduinoJson::JsonObject out) {
    TemperatureSample copy;
    { std::lock_guard<std::mutex> lock(mutex); copy = sample; }
    const bool valid = copy.fresh(esp_timer_get_time());
    out["available"] = copy.available; out["valid"] = valid;
    out["source"] = "esp32s3_internal"; out["scope"] = "chip_not_ambient_or_case";
    out["unit"] = "degC"; out["range_min"] = 20; out["range_max"] = 100;
    out["target_hz"] = 1; out["seq"] = copy.seq;
    out["timestamp_us"] = copy.timestamp_us; out["error"] = copy.error;
    if (valid) out["celsius"] = copy.celsius; else out["celsius"] = nullptr;
}
}
