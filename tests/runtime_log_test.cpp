/* SPDX-License-Identifier: MIT */
// c++ -std=c++17 -pthread -DCOLLECTOR_RUNTIME_LOG_TEST \
//   -I upstream/StackChan/firmware/main tests/runtime_log_test.cpp \
//   upstream/StackChan/firmware/main/collector/runtime_log.cpp -o /tmp/runtime-log-test
#include "collector/runtime_log.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <unistd.h>

using namespace collector;

static RuntimeLogSnapshot snapshot()
{
    RuntimeLogSnapshot result;
    assert(runtime_log_snapshot(result));
    return result;
}

int main()
{
    // Keep the real print sink, and verify that capture neither consumes its
    // va_list nor alters the original serial bytes/return value.
    FILE* output = std::tmpfile();
    assert(output);
    const int saved_stdout = dup(STDOUT_FILENO);
    assert(saved_stdout >= 0 && dup2(fileno(output), STDOUT_FILENO) >= 0);
    runtime_log_init();
    runtime_log_init();
    runtime_log_test_reset();
    const char* colored = "\x1b[32mI (7) CAM: frame 42\x1b[0m\n";
    assert(runtime_log_test_printf("%s", colored) == static_cast<int>(std::strlen(colored)));
    std::fflush(stdout);
    std::rewind(output);
    char original[100] = {};
    assert(std::fread(original, 1, std::strlen(colored), output) == std::strlen(colored));
    assert(std::strcmp(original, colored) == 0);
    auto s = snapshot();
    assert(s.count == 1 && s.sequence == 1);
    assert(std::strcmp(s.lines[0], "I (7) CAM: frame 42") == 0);

    runtime_log_test_reset();
    runtime_log_test_printf("\x1b[");
    runtime_log_test_printf("31mwarn\x1b[0m\t");
    assert(snapshot().count == 0);
    runtime_log_test_printf("continued\r\nsecond\n\n");
    s = snapshot();
    assert(s.count == 2 && s.sequence == 2);
    assert(std::strcmp(s.lines[0], "warn continued") == 0);
    assert(std::strcmp(s.lines[1], "second") == 0);
    runtime_log_test_printf("\x1b]0;discard title\x1b\\visible\x01\x7f\n");
    s = snapshot();
    assert(std::strcmp(s.lines[2], "visible") == 0);

    runtime_log_test_reset();
    runtime_log_test_printf("I: LOCAL_PAIRING_TO");
    runtime_log_test_printf("KEN=0123456789abcdef\nPassword: do-not-display\n");
    runtime_log_test_printf("header: Bearer never-display\n");
    s = snapshot();
    assert(s.count == 3);
    assert(std::strcmp(s.lines[0], "I: LOCAL_PAIRING_[REDACTED]") == 0);
    assert(std::strcmp(s.lines[1], "[REDACTED]") == 0);
    assert(std::strcmp(s.lines[2], "header: [REDACTED]") == 0);

    runtime_log_test_reset();
    runtime_log_test_printf("%s\n", std::string(200, 'x').c_str());
    s = snapshot();
    assert(s.count == 1 && std::strlen(s.lines[0]) == kRuntimeLogLineChars);
    assert(std::strcmp(s.lines[0] + kRuntimeLogLineChars - 3, "...") == 0);
    runtime_log_test_printf("%s", std::string(2000, 'y').c_str());
    runtime_log_test_printf("after\n");
    s = snapshot();
    assert(s.count == 3 && std::strcmp(s.lines[2], "after") == 0);
    assert(std::strlen(s.lines[1]) == kRuntimeLogLineChars);
    assert(std::strcmp(s.lines[1] + kRuntimeLogLineChars - 3, "...") == 0);

    runtime_log_test_reset();
    for (int i = 0; i < 50; ++i) runtime_log_test_printf("line %d\n", i);
    s = snapshot();
    assert(s.count == kRuntimeLogVisibleLines && s.sequence == 50);
    assert(std::strcmp(s.lines[0], "line 41") == 0);
    assert(std::strcmp(s.lines[8], "line 49") == 0);

    runtime_log_test_reset();
    std::thread producer_a([] {
        for (int i = 0; i < 2000; ++i) runtime_log_test_printf("A:%04d complete\n", i);
    });
    std::thread producer_b([] {
        for (int i = 0; i < 2000; ++i) runtime_log_test_printf("B:%04d complete\n", i);
    });
    // The cache is best effort under contention, but a snapshot must never
    // contain a torn line or a sequence/count from different cache versions.
    for (int i = 0; i < 3000; ++i) {
        RuntimeLogSnapshot concurrent;
        if (!runtime_log_snapshot(concurrent)) continue;
        assert(concurrent.count <= kRuntimeLogVisibleLines);
        for (size_t n = 0; n < concurrent.count; ++n) {
            assert(std::strlen(concurrent.lines[n]) == 15);
            assert(concurrent.lines[n][0] == 'A' || concurrent.lines[n][0] == 'B');
            assert(std::strcmp(concurrent.lines[n] + 6, " complete") == 0);
        }
    }
    producer_a.join();
    producer_b.join();
    runtime_log_test_printf("tail\n");
    runtime_log_test_printf("last complete\n");
    s = snapshot();
    assert(std::strcmp(s.lines[s.count - 1], "last complete") == 0);

    std::fflush(stdout);
    assert(dup2(saved_stdout, STDOUT_FILENO) >= 0);
    close(saved_stdout);
    std::fclose(output);
    std::puts("runtime_log_test: passed");
}
