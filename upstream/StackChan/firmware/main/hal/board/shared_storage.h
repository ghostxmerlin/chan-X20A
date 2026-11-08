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
const char* install_file(const FileSpec& file, const uint8_t* bytes, size_t length, bool& unchanged);
const char* read(const collector::eyes::Asset& asset, uint8_t* destination, size_t capacity);
const char* install(const collector::eyes::Asset& asset, const uint8_t* bytes, size_t length, bool& unchanged);
}
