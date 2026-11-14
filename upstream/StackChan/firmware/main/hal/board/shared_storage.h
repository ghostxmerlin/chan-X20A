/* SPDX-License-Identifier: MIT */
#pragma once
#include <esp_err.h>
#include <cstddef>
#include <cstdint>
#include <esp_lcd_panel_io.h>
#include "collector/eyes/catalog.generated.h"

namespace shared_storage {
struct Status { bool mounted; int mount_error; uint32_t reads, installs, failures; uint64_t read_us, write_us; };
// Initializes SPI3 and attempts SD mount ONCE on core 1, before LCD creation.
// Returns SPI initialization error only: a missing/bad card must not stop LCD.
esp_err_t initialize();
Status status();
bool available(); // Nonblocking: safe from display/audio control paths.
// Attach after LVGL display creation; file workers freeze new flushes and drain
// outstanding LCD DMA before entering SD polling transfers on the shared host.
void attach_display(esp_lcd_panel_io_handle_t panel);
// Firmware-owned paths and digests only; never populated from HTTP path input.
struct FileSpec { const char* path; size_t bytes; const uint8_t* sha256; };
const char* read_file(const FileSpec& file, uint8_t* destination, size_t capacity);
// nullptr when the file is there with its size (stat only: no read, no hash), else read_file's error words
// ("asset_missing", "asset_invalid", ...). Same lock and display lease as a read, held for one stat.
const char* check_file(const FileSpec& file);
const char* install_file(const FileSpec& file, const uint8_t* bytes, size_t length, bool& unchanged);
// 0.4.96 (/mouth assets): matches = the file is there with its catalog size and SHA-256 (read and hashed, as
// install_file's own unchanged check). nullptr or a storage error. Same lock and display lease as a read.
const char* match_file(const FileSpec& file, bool& matches);
// "/sdcard/soul/<style>/<state>.soul": the eyes asset's firmware path (the one read() and install() use).
void asset_path(const collector::eyes::Asset& asset, char* path, size_t size);
const char* read(const collector::eyes::Asset& asset, uint8_t* destination, size_t capacity);
const char* install(const collector::eyes::Asset& asset, const uint8_t* bytes, size_t length, bool& unchanged);
}
