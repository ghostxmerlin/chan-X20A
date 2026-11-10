/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector {
// USB-console memory diagnostics (MEM every 5 s, TASKS every 60 s / on low internal heap,
// ALLOCFAIL on every failed heap allocation, rate-limited). See mem_diag.cpp.
void mem_diag_start();
uint32_t mem_diag_failed_allocs();
uint32_t mem_diag_failed_internal();
}
