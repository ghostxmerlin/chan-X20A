/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdarg>
#include <cstddef>
#include <cstdint>

namespace collector {
constexpr size_t kRuntimeLogLineChars = 120;
constexpr size_t kRuntimeLogVisibleLines = 9;
// Nine maximum-width lines, eight separators, and the final NUL. Shared with
// the screen cache so retaining hidden history cannot inflate internal RAM.
constexpr size_t kRuntimeLogTextBytes = kRuntimeLogVisibleLines * kRuntimeLogLineChars +
    (kRuntimeLogVisibleLines - 1) + 1;

struct RuntimeLogSnapshot {
    uint32_t sequence = 0;
    size_t count = 0;
    // Complete lines, oldest first. Strings are always NUL terminated.
    char lines[kRuntimeLogVisibleLines][kRuntimeLogLineChars + 1] = {};
};

// Install once, early in collector startup. UART output keeps its previous
// vprintf sink. Captures subsequent ESP logs only, not ROM/early/raw printf.
void runtime_log_init();
void runtime_log_set_output(int (*sink)(const char*,va_list));
// Best-effort local display cache, not a durable log. Never waits for a writer;
// false leaves out unchanged so the display can retain its previous snapshot.
bool runtime_log_snapshot(RuntimeLogSnapshot& out);

#ifdef COLLECTOR_RUNTIME_LOG_TEST
// Exercise the same capture path without ESP-IDF or a physical UART.
void runtime_log_test_reset();
int runtime_log_test_printf(const char* format, ...);
#endif
}
