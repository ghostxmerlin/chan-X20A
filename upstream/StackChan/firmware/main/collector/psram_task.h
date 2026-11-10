/* SPDX-License-Identifier: MIT */
#pragma once
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/idf_additions.h>
#include <freertos/task.h>
namespace collector {
// Task with its stack (and TCB) in PSRAM (user, 2026-11-10: internal RAM only where it is
// required). Internal RAM is left for Wi-Fi DMA buffers; at 0.4.62 the robot ran out of it and
// stopped receiving. Rules for a task created here:
//  - no flash access from the task at all: no NVS read or write, no esp_partition/esp_flash,
//    no OTA (the cache, and with it PSRAM, is off during flash operations);
//  - no buffer on its stack handed to DMA;
//  - it ends itself with vTaskDeleteWithCaps(nullptr), never vTaskDelete.
// Tasks that save settings (httpd, main, collector_serial, collector_asr) stay internal.
inline BaseType_t create_psram_task(TaskFunction_t entry, const char* name, uint32_t stack_bytes, void* arg,
                                    UBaseType_t priority, BaseType_t core = tskNO_AFFINITY)
{
    return xTaskCreatePinnedToCoreWithCaps(entry, name, stack_bytes, arg, priority, nullptr, core,
                                           MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}
}
