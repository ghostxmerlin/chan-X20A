/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.h>
#include "../control_reply.h"
namespace collector::eyes {
void start_asset_installer();
// 0.4.96: run(ctx) on the asset install worker (PSRAM stack, priority 1), the same one POST /v1/assets uses.
// false: the worker is not running or an install (HTTP or another job) is in progress. Never blocks.
bool run_storage_job(void (*run)(void*), void* ctx);
void catalog_json(JsonObject out);
void status_json(JsonObject out);
// Collector wrappers perform Bearer authentication and the mandatory boot check.
// command_handler receives the parsed body of POST /v1/control {"target":"eyes",...};
// install_handler serves POST /v1/assets (X-Voice-Id, or X-Eyes-Style + X-Eyes-State).
esp_err_t command_handler(ControlReply* request,JsonObject body);
esp_err_t install_handler(httpd_req_t* request);
}
