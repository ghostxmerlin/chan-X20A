/* SPDX-License-Identifier: MIT */
#include "eyes.h"
#include "loader.h"
#include "hal/board/shared_storage.h"
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mbedtls/sha256.h>
#include <cstring>

namespace collector::eyes {
namespace {
struct EspBackend final:Backend {
    int64_t now_us() const override { return esp_timer_get_time(); }
    bool available() const override { return shared_storage::available(); }
    void* allocate(size_t size) override { return heap_caps_malloc(size,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT); }
    void release(void* p) override { heap_caps_free(p); }
    Error read(const Asset& a,uint8_t* out,size_t capacity) override {
        const char* error=shared_storage::read(a,out,capacity);
        if (!error) return Error::None;
        if (!std::strcmp(error,"asset_missing")) return Error::AssetMissing;
        if (!std::strcmp(error,"asset_invalid")) return Error::AssetInvalid;
        if (!std::strcmp(error,"storage_unavailable")) return Error::StorageUnavailable;
        return Error::IoError;
    }
    bool sha256(const uint8_t* data,size_t size,uint8_t* digest) override { return mbedtls_sha256(data,size,digest,0)==0; }
    uint32_t internal_low_water() const override { return heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT); }
} backend;
void worker(void*) { for (;;) {step();vTaskDelay(1);} }
}
void start() {
    if (!initialize(backend)) { ESP_LOGW("Eyes","Storage or PSRAM unavailable; retaining original home");return; }
    if (xTaskCreatePinnedToCore(worker,"eyes",8192,nullptr,1,nullptr,0)!=pdPASS) {
        startup_failed();
        ESP_LOGE("Eyes","Worker unavailable; caches released, eye requests disabled");
    }
}
}
