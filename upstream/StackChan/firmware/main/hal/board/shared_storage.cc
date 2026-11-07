/* SPDX-License-Identifier: MIT */
#include "shared_storage.h"
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <driver/sdspi_host.h>
#include <esp_vfs_fat.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <sdmmc_cmd.h>
#include <mbedtls/sha256.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <mutex>
#include <atomic>
#include <sys/stat.h>

namespace shared_storage {
namespace {
std::mutex mutex;
struct AtomicStatus {
    std::atomic<bool> mounted{false}; std::atomic<int> mount_error{0};
    std::atomic<uint32_t> reads{0}, installs{0}, failures{0};
    std::atomic<uint64_t> read_us{0}, write_us{0};
} stats;
bool attempted=false;
sdmmc_card_t* card=nullptr;
constexpr char kRoot[]="/sdcard";
bool known(const collector::eyes::Asset& a) { return collector::eyes::asset(a.style,a.state)==&a; }
void path_for(const collector::eyes::Asset& a,char* path,size_t size)
{ std::snprintf(path,size,"/sdcard/soul/%s/%s.soul",collector::eyes::style_id(a.style),collector::eyes::state_id(a.state)); }
const char* io_error() { ++stats.failures; stats.mounted=false; return "io_error"; }
bool file_matches(const char* path,const collector::eyes::Asset& a)
{
    struct stat st{};
    if (stat(path,&st) || size_t(st.st_size)!=a.bytes) return false;
    FILE* f=std::fopen(path,"rb"); if (!f) return false;
    mbedtls_sha256_context sha; mbedtls_sha256_init(&sha);
    bool ok=mbedtls_sha256_starts(&sha,0)==0;
    uint8_t chunk[4096],digest[32]; size_t total=0;
    while (ok && total<a.bytes) {
        const auto count=std::min(sizeof(chunk),size_t(a.bytes)-total);
        ok=std::fread(chunk,1,count,f)==count && mbedtls_sha256_update(&sha,chunk,count)==0;
        total+=count; vTaskDelay(1);
    }
    ok=ok && mbedtls_sha256_finish(&sha,digest)==0;
    mbedtls_sha256_free(&sha); const bool closed=std::fclose(f)==0;
    return ok && closed && !std::memcmp(digest,a.sha256,32);
}
struct Init { SemaphoreHandle_t done; esp_err_t result=ESP_FAIL; };
void init_task(void* p)
{
    auto* init=static_cast<Init*>(p);
    gpio_set_direction(GPIO_NUM_3,GPIO_MODE_OUTPUT); gpio_set_level(GPIO_NUM_3,1);
    gpio_set_direction(GPIO_NUM_4,GPIO_MODE_OUTPUT); gpio_set_level(GPIO_NUM_4,1);
    spi_bus_config_t bus{};
    bus.mosi_io_num=37; bus.miso_io_num=35; bus.sclk_io_num=36;
    bus.quadwp_io_num=-1; bus.quadhd_io_num=-1; bus.max_transfer_sz=320*240*2;
    init->result=spi_bus_initialize(SPI3_HOST,&bus,SPI_DMA_CH_AUTO);
    if (init->result==ESP_OK) {
        sdmmc_host_t host=SDSPI_HOST_DEFAULT(); host.slot=SPI3_HOST;
        host.max_freq_khz=20000; host.command_timeout_ms=500;
        sdspi_device_config_t slot=SDSPI_DEVICE_CONFIG_DEFAULT();
        slot.host_id=SPI3_HOST; slot.gpio_cs=GPIO_NUM_4;
        esp_vfs_fat_sdmmc_mount_config_t mount{};
        mount.format_if_mount_failed=false; mount.max_files=4; mount.allocation_unit_size=16384;
        stats.mount_error=esp_vfs_fat_sdspi_mount(kRoot,&host,&slot,&mount,&card);
        stats.mounted=stats.mount_error==ESP_OK;
        ESP_LOGI("SharedStorage","SD mount %s (0x%x); SPI3 remains available to LCD",stats.mounted?"ready":"unavailable",stats.mount_error.load());
    } else stats.mount_error=init->result;
    xSemaphoreGive(init->done); vTaskDelete(nullptr);
}
}
esp_err_t initialize()
{
    if (attempted) return ESP_ERR_INVALID_STATE;
    attempted=true;
    Init init{xSemaphoreCreateBinary()};
    if (!init.done) return ESP_ERR_NO_MEM;
    if (xTaskCreatePinnedToCore(init_task,"sd_init",6144,&init,4,nullptr,1)!=pdPASS) { vSemaphoreDelete(init.done); return ESP_ERR_NO_MEM; }
    // The mount's command timeout bounds card probing. Never continue into LCD
    // initialization while an SD initialization task might still own the pins.
    xSemaphoreTake(init.done,portMAX_DELAY); vSemaphoreDelete(init.done);
    return init.result;
}
Status status() { return {stats.mounted.load(),stats.mount_error.load(),stats.reads.load(),stats.installs.load(),stats.failures.load(),stats.read_us.load(),stats.write_us.load()}; }
bool available() { return stats.mounted.load(); }
const char* read(const collector::eyes::Asset& a,uint8_t* destination,size_t capacity)
{
    if (!known(a) || !destination || capacity<a.bytes) return "invalid_argument";
    std::lock_guard<std::mutex> lock(mutex);
    if (!stats.mounted) return "storage_unavailable";
    char path[96]; path_for(a,path,sizeof(path)); struct stat st{};
    if (stat(path,&st)) return errno==ENOENT?"asset_missing":io_error();
    if (size_t(st.st_size)!=a.bytes) return "asset_invalid";
    FILE* f=std::fopen(path,"rb"); if (!f) return io_error();
    const auto began=esp_timer_get_time(); size_t total=0; bool ok=true;
    while (total<a.bytes) {
        const size_t count=std::min<size_t>(4096,a.bytes-total);
        if (std::fread(destination+total,1,count,f)!=count) { ok=false; break; }
        total+=count; vTaskDelay(1);
    }
    ok=std::fclose(f)==0 && ok; stats.read_us+=esp_timer_get_time()-began;
    if (!ok) return io_error();
    ++stats.reads; return nullptr;
}
const char* install(const collector::eyes::Asset& a,const uint8_t* bytes,size_t length,bool& unchanged)
{
    unchanged=false;
    if (!known(a) || !bytes || length!=a.bytes) return "invalid_argument";
    uint8_t digest[32];
    if (mbedtls_sha256(bytes,length,digest,0) || std::memcmp(digest,a.sha256,32)) return "asset_invalid";
    std::lock_guard<std::mutex> lock(mutex);
    if (!stats.mounted) return "storage_unavailable";
    char path[96],part[104],previous[104],dir[64]; path_for(a,path,sizeof(path));
    std::snprintf(part,sizeof(part),"%s.part",path); std::snprintf(previous,sizeof(previous),"%s.prev",path);
    std::snprintf(dir,sizeof(dir),"/sdcard/soul/%s",collector::eyes::style_id(a.style));
    for (const char* d:{"/sdcard/soul",static_cast<const char*>(dir)}) {
        if (mkdir(d,0755) && errno!=EEXIST) return io_error();
        struct stat st{}; if (stat(d,&st) || !S_ISDIR(st.st_mode)) return "asset_path_conflict";
    }
    if (file_matches(path,a)) { unchanged=true; return nullptr; }
    struct stat st{}; const bool old=stat(path,&st)==0;
    if (old && stat(previous,&st)==0) return "asset_backup_exists";
    const auto began=esp_timer_get_time(); FILE* f=std::fopen(part,"wb");
    if (!f) return io_error();
    bool ok=true;
    for (size_t at=0;at<length;) {
        const size_t count=std::min<size_t>(4096,length-at);
        if (std::fwrite(bytes+at,1,count,f)!=count) { ok=false; break; }
        at+=count; vTaskDelay(1);
    }
    ok=std::fclose(f)==0 && ok;
    if (!ok || !file_matches(part,a)) { std::remove(part); return io_error(); }
    if (old && std::rename(path,previous)) { std::remove(part); return io_error(); }
    if (std::rename(part,path)) {
        if (old) std::rename(previous,path);
        std::remove(part); return io_error();
    }
    stats.write_us+=esp_timer_get_time()-began; ++stats.installs;
    return nullptr;
}
} // namespace shared_storage
