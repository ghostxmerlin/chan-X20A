#ifndef REMOTE_SETTINGS_H
#define REMOTE_SETTINGS_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int channel;
    int id;
} remote_settings_t;

// Read-only at boot. Missing, incompatible or unreadable settings use CH6/ID1.
esp_err_t remote_settings_load(remote_settings_t *settings);
// Called only on explicit setup confirmation; unchanged pairs never write.
// Read back after writing, including ambiguous write failures, and adopt any
// valid observed pair. Invalid input or failed readback keeps the last good
// running pair. Non-OK does NOT promise flash rollback or the next boot's pair.
esp_err_t remote_settings_confirm(remote_settings_t *selection);

#ifdef __cplusplus
}
#endif
#endif
