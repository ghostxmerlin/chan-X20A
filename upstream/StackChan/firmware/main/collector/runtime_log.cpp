/* SPDX-License-Identifier: MIT */
#include "runtime_log.h"
#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#ifdef ESP_PLATFORM
#include <esp_log.h>
#endif

namespace collector {
namespace {
constexpr size_t kStoredLines = kRuntimeLogVisibleLines;
[[maybe_unused]] constexpr size_t kFormatBytes = 384;
enum class Escape { None, Start, Csi, Osc, OscEnd };
std::atomic_flag cache_lock = ATOMIC_FLAG_INIT;
std::atomic<bool> capture_gap{false};
char lines[kStoredLines][kRuntimeLogLineChars + 1] = {};
char pending[kRuntimeLogLineChars + 1] = {};
size_t next_line = 0, line_count = 0, pending_size = 0;
uint32_t sequence = 0;
bool line_truncated = false, discard_until_newline = false;
Escape escape = Escape::None;

char ascii_lower(char c)
{
    return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : c;
}

// Deliberately conservative: hide the rest of a credential-bearing line.
// This is a display guard, not a general-purpose secret scrubber or an export.
size_t credential_offset(const char* text, size_t length)
{
    constexpr const char* keys[] = {"token", "password", "passwd", "authorization", "bearer", "psk"};
    for (size_t i = 0; i < length; ++i) {
        for (const char* key : keys) {
            size_t k = 0;
            while (key[k] && i + k < length && ascii_lower(text[i + k]) == key[k]) ++k;
            if (!key[k]) return i;
        }
    }
    return length;
}

void finish_line()
{
    if (pending_size) {
        size_t credential = credential_offset(pending, pending_size);
        if (credential < pending_size) {
            constexpr char hidden[] = "[REDACTED]";
            if (credential + sizeof(hidden) > sizeof(pending))
                credential = sizeof(pending) - sizeof(hidden);
            std::memcpy(pending + credential, hidden, sizeof(hidden));
            pending_size = credential + sizeof(hidden) - 1;
        } else if (line_truncated) {
            const size_t mark = pending_size > 3 ? pending_size - 3 : 0;
            std::memcpy(pending + mark, "...", 4);
            pending_size = mark + 3;
        }
        pending[pending_size] = '\0';
        std::memcpy(lines[next_line], pending, pending_size + 1);
        next_line = (next_line + 1) % kStoredLines;
        if (line_count < kStoredLines) ++line_count;
        ++sequence;
    }
    pending_size = 0;
    line_truncated = false;
    escape = Escape::None;
}

[[maybe_unused]] void capture_bytes(const char* bytes, size_t size, bool format_truncated)
{
    if (cache_lock.test_and_set(std::memory_order_acquire)) {
        // If any fragment is lost, suppress the remainder of that line rather
        // than displaying a credential value whose label may have been lost.
        capture_gap.store(true, std::memory_order_release);
        return;
    }
    if (capture_gap.exchange(false, std::memory_order_acq_rel)) {
        pending_size = 0;
        line_truncated = false;
        escape = Escape::None;
        discard_until_newline = true;
    }
    for (size_t i = 0; i < size; ++i) {
        const unsigned char c = static_cast<unsigned char>(bytes[i]);
        if (c == '\n') {
            if (discard_until_newline) discard_until_newline = false;
            else finish_line();
            escape = Escape::None;
            continue;
        }
        if (discard_until_newline) continue;
        if (escape == Escape::Csi) {
            if (c >= 0x40 && c <= 0x7e) escape = Escape::None;
            continue;
        }
        if (escape == Escape::Osc) {
            if (c == 7) escape = Escape::None;
            else if (c == 0x1b) escape = Escape::OscEnd;
            continue;
        }
        if (escape == Escape::OscEnd) {
            escape = c == '\\' ? Escape::None : Escape::Osc;
            continue;
        }
        if (escape == Escape::Start) {
            if (c == '[') escape = Escape::Csi;
            else if (c == ']') escape = Escape::Osc;
            else if (c >= 0x30 && c <= 0x7e) escape = Escape::None;
            continue;
        }
        if (c == 0x1b) { escape = Escape::Start; continue; }
        if ((c < 0x20 && c != '\t') || c == 0x7f) continue;
        if (pending_size < kRuntimeLogLineChars)
            pending[pending_size++] = c == '\t' ? ' ' : static_cast<char>(c);
        else line_truncated = true;
    }
    if (format_truncated) {
        // The bounded formatter omitted bytes (possibly including newlines).
        // Finish the captured prefix and begin fresh at the next hook call.
        line_truncated = true;
        if (!discard_until_newline) finish_line();
        discard_until_newline = false;
        escape = Escape::None;
    }
    cache_lock.clear(std::memory_order_release);
}

#if defined(ESP_PLATFORM) || defined(COLLECTOR_RUNTIME_LOG_TEST)
using PrintSink = int (*)(const char*, va_list);
std::atomic<PrintSink> previous_sink{&std::vprintf};

int capture_vprintf(const char* format, va_list args)
{
    // Do not hold the cache lock while formatting or while the UART sink runs.
    va_list output_args;
    va_copy(output_args, args);
    const int result = previous_sink.load(std::memory_order_acquire)(format, output_args);
    va_end(output_args);
    char formatted[kFormatBytes];
    va_list capture_args;
    va_copy(capture_args, args);
    const int size = std::vsnprintf(formatted, sizeof(formatted), format, capture_args);
    va_end(capture_args);
    if (size > 0) {
        const bool truncated = static_cast<size_t>(size) >= sizeof(formatted);
        capture_bytes(formatted, truncated ? sizeof(formatted) - 1 : static_cast<size_t>(size), truncated);
    }
    return result;
}
#endif
}

void runtime_log_init()
{
#ifdef ESP_PLATFORM
    static std::atomic<bool> installed{false};
    if (installed.exchange(true, std::memory_order_acq_rel)) return;
    PrintSink previous = esp_log_set_vprintf(capture_vprintf);
    if (previous && previous != capture_vprintf) previous_sink.store(previous, std::memory_order_release);
#endif
}

bool runtime_log_snapshot(RuntimeLogSnapshot& out)
{
    if (cache_lock.test_and_set(std::memory_order_acquire)) return false;
    out.sequence = sequence;
    out.count = line_count < kRuntimeLogVisibleLines ? line_count : kRuntimeLogVisibleLines;
    const size_t first = (next_line + kStoredLines - out.count) % kStoredLines;
    for (size_t i = 0; i < out.count; ++i)
        std::memcpy(out.lines[i], lines[(first + i) % kStoredLines], sizeof(out.lines[i]));
    cache_lock.clear(std::memory_order_release);
    return true;
}

#ifdef COLLECTOR_RUNTIME_LOG_TEST
void runtime_log_test_reset()
{
    // Test setup only, with all producers stopped.
    next_line = line_count = pending_size = 0;
    sequence = 0;
    line_truncated = discard_until_newline = false;
    escape = Escape::None;
    capture_gap.store(false);
    cache_lock.clear();
}

int runtime_log_test_printf(const char* format, ...)
{
    va_list args;
    va_start(args, format);
    const int result = capture_vprintf(format, args);
    va_end(args);
    return result;
}
#endif
}
