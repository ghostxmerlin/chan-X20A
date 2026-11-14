/* SPDX-License-Identifier: MIT */
#include "ui_font.h"
#include "ui_font_lvgl.h"
#include "ui_font_catalog.generated.h"
#include "psram_task.h"
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <new>

namespace collector::ui_font {
namespace {
constexpr char kTag[] = "UiFont";

// One per catalog variant, in PSRAM. LVGL keeps pointing at `font` once it is published.
struct Slot {
    std::atomic<const lv_font_t*> published{nullptr};
    std::atomic<const char*> error{"not_loaded"};   // static strings only
    std::atomic<uint32_t> load_ms{0}, attempts{0};
    lv_font_t font{};
    lv_font_fmt_txt_dsc_t dsc{};
};
// Set once by start() (screen creation); read by the screen, HTTP status and the loader.
std::atomic<Slot*> slot_table{nullptr};
std::atomic<uint32_t> loaded_generation{0};
std::atomic<TaskHandle_t> loader{nullptr};

const char* prepare(Slot& s, const Variant& v, uint8_t* blob)
{
    Header h;
    if (const char* e = validate(blob, v.bytes, h, kMaxBitmapIndex)) return e;
    if (h.px != v.px || h.bpp != v.bpp) return "variant_mismatch";
    auto* glyphs = static_cast<lv_font_fmt_txt_glyph_dsc_t*>(
        heap_caps_calloc(h.glyph_count, sizeof(lv_font_fmt_txt_glyph_dsc_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    auto* cmaps = static_cast<lv_font_fmt_txt_cmap_t*>(
        heap_caps_calloc(h.cmap_count, sizeof(lv_font_fmt_txt_cmap_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!glyphs || !cmaps) { heap_caps_free(glyphs); heap_caps_free(cmaps); return "no_memory"; }
    ui_font::bind(blob, h, glyphs, cmaps, s.dsc, s.font);
    return nullptr;
}

// Read every part of variant `i` into one PSRAM buffer (each part's size and SHA-256 checked by shared storage,
// which also takes the LVGL lock and drains LCD DMA per file), then bind it. On failure nothing stays allocated.
void load(size_t i)
{
    Slot& s = slot_table.load()[i];
    const Variant& v = kVariants[i];
    ++s.attempts;
    const int64_t began = esp_timer_get_time();
    if (!shared_storage::available()) { s.error = "storage_unavailable"; return; }
    auto* blob = static_cast<uint8_t*>(heap_caps_malloc(v.bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!blob) { s.error = "no_memory"; return; }
    size_t at = 0;
    const char* error = nullptr;
    for (uint8_t k = 0; k < v.part_count && !error; ++k) {
        const Part& p = v.parts[k];
        if (p.bytes > v.bytes - at) { error = "catalog_invalid"; break; }
        error = shared_storage::read_file({p.path, p.bytes, p.sha256}, blob + at, v.bytes - at);
        at += p.bytes;
        vTaskDelay(1);
    }
    if (!error && at != v.bytes) error = "catalog_invalid";
    if (!error) error = prepare(s, v, blob);
    if (error) {
        heap_caps_free(blob);
        s.error = error;
        ESP_LOGW(kTag, "%s not loaded: %s", v.id, error);
        return;
    }
    s.load_ms = static_cast<uint32_t>((esp_timer_get_time() - began) / 1000);
    s.error = nullptr;
    s.published.store(&s.font, std::memory_order_release);
    ++loaded_generation;
    ESP_LOGI(kTag, "%s loaded: %u px %u bpp, %lu bytes PSRAM, %lu ms", v.id, v.px, v.bpp,
             static_cast<unsigned long>(v.bytes), static_cast<unsigned long>(s.load_ms.load()));
}

void loader_task(void*)
{
    Slot* slots = slot_table.load();
    for (;;) {
        for (size_t i = 0; i < kVariantCount; ++i) {
            // The LVGL lock (1 s bound in shared storage) can be busy at boot: retry that, nothing else.
            for (int tries = 0; tries < 3 && !slots[i].published.load(std::memory_order_acquire); ++tries) {
                load(i);
                const char* e = slots[i].error.load();
                if (!e || std::strcmp(e, "display_bus_unavailable")) break;
                vTaskDelay(pdMS_TO_TICKS(2000));
            }
        }
        // Wait for reload() (an installed part); a card inserted later needs a reboot (mount happens once).
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    }
}
}

const lv_font_t* cjk(int px)
{
    Slot* slots = slot_table.load(std::memory_order_acquire);
    if (!slots) return nullptr;
    uint8_t sizes[kVariantCount];
    bool loaded[kVariantCount];
    for (size_t i = 0; i < kVariantCount; ++i) {
        sizes[i] = kVariants[i].px;
        loaded[i] = slots[i].published.load(std::memory_order_acquire) != nullptr;
    }
    const int i = pick_variant(px, sizes, loaded, kVariantCount);
    return i < 0 ? nullptr : slots[i].published.load(std::memory_order_acquire);
}

uint32_t generation() { return loaded_generation.load(); }

void start()
{
    if (slot_table.load()) return;
    void* memory = heap_caps_calloc(kVariantCount, sizeof(Slot), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!memory) { ESP_LOGW(kTag, "no PSRAM for the font slots; ASCII only"); return; }
    auto* created = static_cast<Slot*>(memory);
    for (size_t i = 0; i < kVariantCount; ++i) new (&created[i]) Slot();
    slot_table.store(created, std::memory_order_release);
    TaskHandle_t handle = nullptr;
    // Priority 1: after boot work; SD reads take the LVGL lock per part (~0.2 s each), as the eyes do.
    if (xTaskCreatePinnedToCoreWithCaps(loader_task, "ui_font", 6144, nullptr, 1, &handle, tskNO_AFFINITY,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        ESP_LOGW(kTag, "loader task not started; ASCII only");
        return;
    }
    loader = handle;
}

void reload()
{
    if (TaskHandle_t t = loader.load()) xTaskNotifyGive(t);
}

bool part(const char* id, shared_storage::FileSpec& out)
{
    if (!id) return false;
    if (!std::strcmp(id, "license")) { out = {kLicense.path, kLicense.bytes, kLicense.sha256}; return true; }
    for (size_t i = 0; i < kVariantCount; ++i) {
        const Variant& v = kVariants[i];
        const size_t n = std::strlen(v.id);
        if (std::strncmp(id, v.id, n) || id[n] != '.') continue;
        const char* digits = id + n + 1;
        if (digits[0] < '0' || digits[0] > '9' || digits[1]) return false;   // one digit: at most 10 parts
        const int k = digits[0] - '0';
        if (k >= v.part_count) return false;
        out = {v.parts[k].path, v.parts[k].bytes, v.parts[k].sha256};
        return true;
    }
    return false;
}

void status_json(ArduinoJson::JsonObject out)
{
    Slot* slots = slot_table.load(std::memory_order_acquire);
    out["started"] = slots != nullptr;
    out["loader_ready"] = loader.load() != nullptr;
    out["generation"] = generation();
    out["install_header"] = "X-Font-Part";
    auto variants = out["variants"].to<ArduinoJson::JsonArray>();
    for (size_t i = 0; i < kVariantCount; ++i) {
        const Variant& v = kVariants[i];
        auto o = variants.add<ArduinoJson::JsonObject>();
        o["id"] = v.id; o["px"] = v.px; o["bpp"] = v.bpp; o["bytes"] = v.bytes; o["parts"] = v.part_count;
        char digest[17];
        for (int b = 0; b < 8; ++b) std::snprintf(digest + 2 * b, 3, "%02x", v.parts[0].sha256[b]);
        o["part0_sha256_prefix"] = digest;     // the installer matches it against assets/ui-font/manifest.json
        if (slots) {
            o["loaded"] = slots[i].published.load() != nullptr;
            o["error"] = slots[i].error.load();
            o["load_ms"] = slots[i].load_ms.load();
            o["attempts"] = slots[i].attempts.load();
        } else {
            o["loaded"] = false;
            o["error"] = "not_started";
        }
    }
}
}
