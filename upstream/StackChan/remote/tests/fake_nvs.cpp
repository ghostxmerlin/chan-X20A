#include "fake_nvs.h"
#include <cassert>
#include <cstring>
std::vector<uint8_t> stored_blob;
static std::vector<uint8_t> deferred_blob;
int write_opens, blob_writes, commits;
esp_err_t open_error, read_error, write_error, commit_error;
bool write_takes_effect_on_error, defer_write_visibility, fail_read_after_write;
void reset_nvs()
{
    stored_blob.clear(); deferred_blob.clear();
    write_opens = blob_writes = commits = 0;
    open_error = read_error = write_error = commit_error = ESP_OK;
    write_takes_effect_on_error = defer_write_visibility = fail_read_after_write = false;
}
void simulate_nvs_reinitialization()
{ if (!deferred_blob.empty()) { stored_blob = deferred_blob; deferred_blob.clear(); } }
extern "C" {
esp_err_t nvs_open(const char *ns, nvs_open_mode_t mode, nvs_handle_t *handle)
{
    assert(std::strcmp(ns, "remote_cfg") == 0);
    if (mode == NVS_READWRITE) ++write_opens;
    if (open_error) return open_error;
    *handle = 1;
    return ESP_OK;
}
esp_err_t nvs_get_blob(nvs_handle_t handle, const char *key, void *data, size_t *size)
{
    assert(handle == 1 && std::strcmp(key, "connection") == 0);
    if (read_error) return read_error;
    if (stored_blob.empty()) return ESP_ERR_NVS_NOT_FOUND;
    if (*size < stored_blob.size()) { *size = stored_blob.size(); return ESP_ERR_NVS_INVALID_LENGTH; }
    *size = stored_blob.size();
    std::memcpy(data, stored_blob.data(), *size);
    return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t handle, const char *key, const void *data, size_t size)
{
    assert(handle == 1 && std::strcmp(key, "connection") == 0);
    ++blob_writes;
    const auto *bytes = static_cast<const uint8_t *>(data);
    // IDF 5.5.4 writes inside set_blob. commit is not a transaction boundary.
    if (!write_error || write_takes_effect_on_error) {
        auto &visible = defer_write_visibility ? deferred_blob : stored_blob;
        visible.assign(bytes, bytes + size);
    }
    if (fail_read_after_write) read_error = ESP_FAIL;
    return write_error;
}
esp_err_t nvs_commit(nvs_handle_t handle)
{
    assert(handle == 1);
    ++commits;
    if (commit_error) return commit_error;
    return ESP_OK;
}
void nvs_close(nvs_handle_t handle) { assert(handle == 1); }
}
