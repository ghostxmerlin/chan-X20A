#include "remote_settings.h"
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "nvs.h"

static const remote_settings_t defaults = {6, 1};
static remote_settings_t confirmed = {6, 1};

static bool valid(const remote_settings_t *settings)
{
    return settings->channel >= 1 && settings->channel <= 14 &&
           settings->id >= 0 && settings->id <= 50;
}

static esp_err_t read_settings(remote_settings_t *settings)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open("remote_cfg", NVS_READONLY, &handle);
    if (err != ESP_OK) return err;

    // One versioned blob keeps channel and receiver ID together. No structure
    // padding or native integer representation is written to persistent storage.
    uint8_t blob[8];
    size_t size = sizeof(blob);
    err = nvs_get_blob(handle, "connection", blob, &size);
    nvs_close(handle);
    if (err != ESP_OK) return err;
    if (size != sizeof(blob)) return ESP_ERR_INVALID_SIZE;
    if (memcmp(blob, "RCFG", 4) || blob[4] != 1 || blob[7] != 0) return ESP_ERR_INVALID_VERSION;
    const remote_settings_t loaded = {blob[5], blob[6]};
    if (!valid(&loaded)) return ESP_ERR_INVALID_ARG;
    *settings = loaded;
    return ESP_OK;
}

esp_err_t remote_settings_load(remote_settings_t *settings)
{
    confirmed = defaults;
    const esp_err_t err = read_settings(&confirmed);
    *settings = confirmed;
    return err;
}

esp_err_t remote_settings_confirm(remote_settings_t *selection)
{
    esp_err_t err = ESP_OK;
    if (!valid(selection)) {
        err = ESP_ERR_INVALID_ARG;
    } else if (selection->channel != confirmed.channel || selection->id != confirmed.id) {
        nvs_handle_t handle;
        err = nvs_open("remote_cfg", NVS_READWRITE, &handle);
        if (err == ESP_OK) {
            const uint8_t blob[8] = {'R', 'C', 'F', 'G', 1,
                                    (uint8_t)selection->channel, (uint8_t)selection->id, 0};
            err = nvs_set_blob(handle, "connection", blob, sizeof(blob));
            if (err == ESP_OK) err = nvs_commit(handle);
            nvs_close(handle);

            // IDF 5.5.4 writes during set_blob; commit is not a transaction
            // boundary. Even REMOVE_FAILED can mean the new blob was written.
            // Use a valid readback for running state and retain the error for
            // reporting. An old readback after an error is not proof of rollback:
            // NVS may complete that update during the next initialization.
            remote_settings_t observed;
            const esp_err_t read_err = read_settings(&observed);
            if (read_err == ESP_OK) {
                confirmed = observed;
                if (err == ESP_OK && (observed.channel != selection->channel || observed.id != selection->id)) {
                    err = ESP_FAIL;
                }
            } else if (err == ESP_OK) {
                err = read_err;
            }
        }
    }
    *selection = confirmed;
    return err;
}
