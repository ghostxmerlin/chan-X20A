/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <cstdint>
namespace collector {
// Busiest tasks over the last 5 s window: [{name, prio, core_pct (of one core), stack: psram|internal}].
void mem_diag_cpu_json(ArduinoJson::JsonArray out);
// USB-console memory diagnostics (MEM every 5 s, TASKS every 60 s / on low internal heap,
// ALLOCFAIL on every failed heap allocation, rate-limited). See mem_diag.cpp.
void mem_diag_start();
uint32_t mem_diag_failed_allocs();
uint32_t mem_diag_failed_internal();
}
