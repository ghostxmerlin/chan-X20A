/* SPDX-License-Identifier: MIT */
// Memory diagnostics on the USB console for the network-stack wedge (2026-11-10): every 5 s one
// MEM line (internal/DMA/PSRAM free, largest block, minimum, failed allocations); every 60 s, and
// whenever internal free drops below kLowInternal, a TASKS table with each stack's size class,
// high-water mark and whether it lives in internal RAM or PSRAM. Failed heap allocations are
// printed from the allocator's context with esp_rom_printf (rate-limited) so they still show
// when the network and the main loop are stuck. Runs on a PSRAM stack; never writes NVS/flash.
// 0.4.62: the 0.4.61 repro showed memory is not exhausted at the wedge, so this also pings the
// gateway every 5 s (NET line) and, after two missed pings, dumps who is stuck: whether the lwIP
// thread still runs callbacks, each task's state and CPU time since the last dump, and the Wi-Fi
// driver's buffer/RX/TX/HW statistics.
#include "mem_diag.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_memory_utils.h>
#include <esp_rom_sys.h>
#include <esp_timer.h>
#include <esp_netif.h>
#include <esp_wifi.h>
#include <ping/ping_sock.h>
#include <lwip/tcpip.h>
#include <freertos/semphr.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
namespace collector {
namespace {
constexpr char kTag[] = "MemDiag";
constexpr size_t kLowInternal = 16 * 1024;
constexpr uint32_t kStackBytes = 6144, kMaxTasks = 48, kFailPrintsPerWindow = 12;
std::atomic<uint32_t> failed_allocs{0}, failed_internal{0}, fail_prints{0};
std::atomic<uint32_t> last_fail_size{0}, last_fail_caps{0};
std::atomic<uint32_t> ping_ok{0}, ping_timeout{0}, ping_last_ms{0};
esp_ping_handle_t ping_session = nullptr;
uint32_t previous_runtime[kMaxTasks]; char previous_names[kMaxTasks][configMAX_TASK_NAME_LEN];
// CPU sampler for telemetry (0.4.79): top tasks by run time over each 5 s window.
struct CpuRow { char name[configMAX_TASK_NAME_LEN]; uint8_t prio; uint16_t permille; bool psram; };
constexpr size_t kCpuRows = 10;
CpuRow cpu_rows[kCpuRows]; size_t cpu_n = 0; portMUX_TYPE cpu_lock = portMUX_INITIALIZER_UNLOCKED;
uint32_t cpu_prev[kMaxTasks]; char cpu_prev_names[kMaxTasks][configMAX_TASK_NAME_LEN]; int64_t cpu_prev_us = 0;

void sample_cpu()
{
    static TaskStatus_t tasks[kMaxTasks];
    static CpuRow rows[kMaxTasks];
    const UBaseType_t n = uxTaskGetSystemState(tasks, kMaxTasks, nullptr);
    const int64_t now = esp_timer_get_time();
    const int64_t window = now - cpu_prev_us;
    size_t m = 0;
    for (UBaseType_t i = 0; i < n && cpu_prev_us; ++i) {
        uint32_t before = tasks[i].ulRunTimeCounter;           // a new task: no history, counts 0
        for (uint32_t k = 0; k < kMaxTasks; ++k) if (!strncmp(cpu_prev_names[k], tasks[i].pcTaskName, configMAX_TASK_NAME_LEN)) { before = cpu_prev[k]; break; }
        CpuRow r{};
        strncpy(r.name, tasks[i].pcTaskName, configMAX_TASK_NAME_LEN - 1);
        r.prio = static_cast<uint8_t>(tasks[i].uxCurrentPriority);
        r.permille = static_cast<uint16_t>(std::min<int64_t>(1000, window > 0 ? int64_t(tasks[i].ulRunTimeCounter - before) * 1000 / window : 0));
        r.psram = esp_ptr_external_ram(tasks[i].pxStackBase);
        rows[m++] = r;
    }
    std::sort(rows, rows + m, [](const CpuRow& a, const CpuRow& b) { return a.permille > b.permille; });
    portENTER_CRITICAL(&cpu_lock);
    cpu_n = std::min(m, kCpuRows); std::copy(rows, rows + cpu_n, cpu_rows);
    portEXIT_CRITICAL(&cpu_lock);
    for (UBaseType_t i = 0; i < n && i < kMaxTasks; ++i) {
        strncpy(cpu_prev_names[i], tasks[i].pcTaskName, configMAX_TASK_NAME_LEN); cpu_prev[i] = tasks[i].ulRunTimeCounter;
    }
    cpu_prev_us = now;
}

void on_ping_ok(esp_ping_handle_t h, void*)
{
    uint32_t elapsed = 0; esp_ping_get_profile(h, ESP_PING_PROF_TIMEGAP, &elapsed, sizeof(elapsed));
    ping_last_ms = elapsed; ++ping_ok;
}
void on_ping_timeout(esp_ping_handle_t, void*) { ++ping_timeout; }

bool start_ping()
{
    esp_netif_t* sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip{};
    if (!sta || esp_netif_get_ip_info(sta, &ip) != ESP_OK || ip.gw.addr == 0) return false;
    esp_ping_config_t config = ESP_PING_DEFAULT_CONFIG();
    config.count = ESP_PING_COUNT_INFINITE; config.interval_ms = 5000; config.timeout_ms = 1500; config.data_size = 16;
    config.task_stack_size = 2560; config.task_prio = 1;
    config.target_addr.type = IPADDR_TYPE_V4; config.target_addr.u_addr.ip4.addr = ip.gw.addr;
    esp_ping_callbacks_t callbacks{}; callbacks.on_ping_success = on_ping_ok; callbacks.on_ping_timeout = on_ping_timeout;
    if (esp_ping_new_session(&config, &callbacks, &ping_session) != ESP_OK) return false;
    esp_ping_start(ping_session);
    ESP_LOGI(kTag, "NET pinging gateway " IPSTR " every 5 s", IP2STR(&ip.gw));
    return true;
}

SemaphoreHandle_t tcpip_probe_done = nullptr;
void tcpip_probe(void*) { xSemaphoreGive(tcpip_probe_done); }
// True if the lwIP thread ran a queued callback within 1 s.
bool tcpip_alive()
{
    if (!tcpip_probe_done) tcpip_probe_done = xSemaphoreCreateBinary();
    xSemaphoreTake(tcpip_probe_done, 0);
    if (tcpip_try_callback(tcpip_probe, nullptr) != ERR_OK) return false;   // mailbox full counts as stuck
    return xSemaphoreTake(tcpip_probe_done, pdMS_TO_TICKS(1000)) == pdTRUE;
}

void dump_stuck()
{
    static TaskStatus_t tasks[kMaxTasks];
    uint32_t total = 0;
    const UBaseType_t n = uxTaskGetSystemState(tasks, kMaxTasks, &total);
    static const char* const states[] = {"run", "ready", "blocked", "suspended", "deleted", "invalid"};
    ESP_LOGW(kTag, "STUCK tcpip_alive=%d", tcpip_alive() ? 1 : 0);
    for (UBaseType_t i = 0; i < n; ++i) {
        uint32_t before = 0;
        for (uint32_t k = 0; k < kMaxTasks; ++k) if (strncmp(previous_names[k], tasks[i].pcTaskName, configMAX_TASK_NAME_LEN) == 0) { before = previous_runtime[k]; break; }
        ESP_LOGW(kTag, "STUCK task %-16s %-9s prio=%2u cpu_delta=%lu", tasks[i].pcTaskName,
                 states[std::min<int>(tasks[i].eCurrentState, 5)], (unsigned)tasks[i].uxCurrentPriority,
                 (unsigned long)(tasks[i].ulRunTimeCounter - before));
    }
    for (UBaseType_t i = 0; i < n && i < kMaxTasks; ++i) {
        strncpy(previous_names[i], tasks[i].pcTaskName, configMAX_TASK_NAME_LEN); previous_runtime[i] = tasks[i].ulRunTimeCounter;
    }
    wifi_ap_record_t ap{};
    const esp_err_t ap_err = esp_wifi_sta_get_ap_info(&ap);
    ESP_LOGW(kTag, "STUCK wifi ap_info=%s rssi=%d channel=%u", esp_err_to_name(ap_err), ap_err == ESP_OK ? ap.rssi : 0, ap_err == ESP_OK ? ap.primary : 0);
    esp_wifi_statis_dump(WIFI_STATIS_ALL);
}

void on_failed_alloc(size_t size, uint32_t caps, const char* function)
{
    ++failed_allocs;
    if (!(caps & MALLOC_CAP_SPIRAM)) ++failed_internal;
    last_fail_size = size; last_fail_caps = caps;
    if (fail_prints.fetch_add(1) < kFailPrintsPerWindow)
        // Only the lock-free free-size counter here: this may run in any task or ISR context.
        esp_rom_printf("ALLOCFAIL size=%u caps=0x%08x fn=%s int_free=%u\n", (unsigned)size, (unsigned)caps,
                       function ? function : "?", (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
}

void print_tasks()
{
    static TaskStatus_t tasks[kMaxTasks];
    const UBaseType_t n = uxTaskGetSystemState(tasks, kMaxTasks, nullptr);
    unsigned internal_tasks = 0;
    for (UBaseType_t i = 0; i < n; ++i) {
        const bool psram = esp_ptr_external_ram(tasks[i].pxStackBase);
        if (!psram) ++internal_tasks;
        // usStackHighWaterMark is in bytes on ESP-IDF (StackType_t is uint8_t).
        ESP_LOGI(kTag, "TASK %-16s prio=%2u stack_free_min=%5u %s", tasks[i].pcTaskName,
                 (unsigned)tasks[i].uxCurrentPriority, (unsigned)tasks[i].usStackHighWaterMark, psram ? "psram" : "INTERNAL");
    }
    ESP_LOGI(kTag, "TASKS total=%u internal_stacks=%u", (unsigned)n, internal_tasks);
}

void diag_task(void*)
{
    int64_t last_tasks = 0; bool was_low = false;
    uint32_t seen_ok = 0, seen_timeout = 0, misses = 0, dumps = 0;
    while (true) {
        if (!ping_session) start_ping();
        else {
            const uint32_t ok = ping_ok.load(), timeout = ping_timeout.load();
            if (timeout != seen_timeout && ok == seen_ok) ++misses; else if (ok != seen_ok) { misses = 0; dumps = 0; }
            ESP_LOGI(kTag, "NET gw_ping ok=%lu timeout=%lu last_ms=%lu misses=%lu", (unsigned long)ok, (unsigned long)timeout,
                     (unsigned long)ping_last_ms.load(), (unsigned long)misses);
            seen_ok = ok; seen_timeout = timeout;
            // Two missed pings in a row (10 s): record who is stuck; repeat every 6th miss, at most 5 times.
            if (misses >= 2 && (misses - 2) % 6 == 0 && dumps < 5) { ++dumps; dump_stuck(); print_tasks(); }
        }
        const size_t int_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
        ESP_LOGI(kTag, "MEM int_free=%u int_largest=%u int_min=%u dma_free=%u dma_largest=%u psram_free=%u psram_largest=%u allocfail=%u int_fail=%u last=%u/0x%x",
                 (unsigned)int_free, (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
                 (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA),
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
                 (unsigned)failed_allocs.load(), (unsigned)failed_internal.load(), (unsigned)last_fail_size.load(), (unsigned)last_fail_caps.load());
        fail_prints = 0;
        const int64_t now = esp_timer_get_time();
        const bool low = int_free < kLowInternal;
        if (now - last_tasks >= 60000000 || (low && !was_low)) { last_tasks = now; print_tasks(); }
        sample_cpu();
        was_low = low;
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
}

void mem_diag_start()
{
    heap_caps_register_failed_alloc_callback(on_failed_alloc);
    if (xTaskCreateWithCaps(diag_task, "mem_diag", kStackBytes, nullptr, 1, nullptr, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS)
        ESP_LOGE(kTag, "diagnostics task not started");
}

void mem_diag_cpu_json(ArduinoJson::JsonArray out)
{
    CpuRow rows[kCpuRows]; size_t n;
    portENTER_CRITICAL(&cpu_lock); n = cpu_n; std::copy(cpu_rows, cpu_rows + n, rows); portEXIT_CRITICAL(&cpu_lock);
    for (size_t i = 0; i < n; ++i) {
        auto o = out.add<ArduinoJson::JsonObject>();
        o["name"] = rows[i].name; o["prio"] = rows[i].prio; o["core_pct"] = rows[i].permille / 10.0; o["stack"] = rows[i].psram ? "psram" : "internal";
    }
}

uint32_t mem_diag_failed_allocs() { return failed_allocs.load(); }
uint32_t mem_diag_failed_internal() { return failed_internal.load(); }
}
