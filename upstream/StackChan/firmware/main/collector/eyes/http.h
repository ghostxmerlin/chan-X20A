/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.h>
#include <esp_http_server.h>
namespace collector::eyes {
void start_asset_installer();
void catalog_json(JsonObject out);
void status_json(JsonObject out);
// Collector wrappers perform Bearer authentication and the mandatory boot check.
// command_handler receives the parsed body of POST /v1/control {"target":"eyes",...};
// install_handler serves POST /v1/assets (X-Voice-Id, or X-Eyes-Style + X-Eyes-State).
esp_err_t command_handler(httpd_req_t* request,JsonObject body);
esp_err_t install_handler(httpd_req_t* request);
}
