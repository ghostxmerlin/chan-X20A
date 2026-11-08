#pragma once
#include <vector>
#include <cstdint>
#include "nvs.h"
extern std::vector<uint8_t> stored_blob;
extern int write_opens, blob_writes, commits;
extern esp_err_t open_error, read_error, write_error, commit_error;
extern bool write_takes_effect_on_error, defer_write_visibility, fail_read_after_write;
void simulate_nvs_reinitialization();
void reset_nvs();
