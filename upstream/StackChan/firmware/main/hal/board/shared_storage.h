/* SPDX-License-Identifier: MIT */
#pragma once
#include <esp_err.h>
#include <cstddef>
#include <cstdint>
#include "collector/eyes/catalog.generated.h"

namespace shared_storage {
struct Status { bool mounted; int mount_error; uint32_t reads, installs, failures; uint64_t read_us, write_us; };
// Initializes SPI3 and attempts SD mount ONCE on core 1, before LCD creation.
// Returns SPI initialization error only: a missing/bad card must not stop LCD.
esp_err_t initialize();
Status status();
bool available(); // Nonblocking: safe from display/audio control paths.
const char* read(const collector::eyes::Asset& asset, uint8_t* destination, size_t capacity);
const char* install(const collector::eyes::Asset& asset, const uint8_t* bytes, size_t length, bool& unchanged);
}
