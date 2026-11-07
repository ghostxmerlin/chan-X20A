/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.h>
#include <esp_http_server.h>
namespace collector::eyes {
void catalog_json(JsonObject out);
void status_json(JsonObject out);
// Collector wrappers perform Bearer authentication and mandatory boot check.
esp_err_t catalog_handler(httpd_req_t* request);
esp_err_t status_handler(httpd_req_t* request);
esp_err_t command_handler(httpd_req_t* request);
esp_err_t install_handler(httpd_req_t* request);
}
