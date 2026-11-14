/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <lvgl.h>
#include <cstdint>
#include "hal/board/shared_storage.h"
namespace collector::ui_font {
// The screen's CJK font, Source Han Sans (思源黑体), 16 px 4 bpp and 24 px 2 bpp, GB2312 level 1 + ASCII +
// CJK punctuation (collector-0.4.95-font; docs/sd-media.md). The glyphs live on the SD card in parts
// (ui_font_catalog.generated.h); at boot a PSRAM task reads each variant into one PSRAM buffer, checks the parts'
// SHA-256 and the container (ui_font_blob.h) and only then publishes it. Nothing is in the app image or internal
// RAM but this code. Without a card or the parts the screen keeps its ASCII font: callers use choose() with an
// ASCII stand-in (ui_font_blob.h).

// The loaded CJK font closest to `px` (16 or 24), or nullptr (still loading, SD or parts missing, invalid).
// Callable from any task; a font once returned stays valid until reboot.
const lv_font_t* cjk(int px);
// Exactly the `px` variant, or nullptr (not loaded). For layouts sized to one variant (the screen pages, 16 px).
const lv_font_t* cjk_exact(int px);
// Grows by one each time a variant becomes available, so the screen can redraw labels that fell back to ASCII.
uint32_t generation();
// Once, at boot, after the LCD is attached to shared storage (screen creation): starts the loader task.
void start();
// An SD font part was installed: retry the variants that are not loaded yet (no reboot needed).
void reload();
// The install whitelist: "cjk16.0" style ids from X-Font-Part, plus "license" (the OFL text, kept next to the
// parts on SD); false when unknown. Path, size and digest are the firmware's own (never from the request).
bool part(const char* id, shared_storage::FileSpec& out);
void status_json(ArduinoJson::JsonObject out);
}
// The name used in the 0.4.95 brief.
inline const lv_font_t* ui_font_cjk(int px) { return collector::ui_font::cjk(px); }
