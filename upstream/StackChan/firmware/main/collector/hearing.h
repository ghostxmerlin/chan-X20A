/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <esp_http_server.h>
#include <cstdint>
#include <cstddef>
#include <string>
namespace collector::hearing {
void init(const std::string& device_id,const std::string& boot_id);
void feed_audio(const int16_t* interleaved,size_t frames,uint32_t seq,int64_t timestamp_us);
void asr_command(uint8_t id,bool has_value);
void json(ArduinoJson::JsonObject target);
esp_err_t handle(httpd_req_t* request);
}
