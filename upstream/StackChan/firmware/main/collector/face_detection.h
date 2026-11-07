/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <cstddef>
#include <cstdint>
#include <string>

namespace collector {
// Called only by the camera producer. Results belong to this exact JPEG frame.
std::string face_detect_frame(const uint8_t* pixels, size_t bytes, int width, int height,
                              uint32_t format, uint32_t seq, int64_t timestamp_us);
bool face_detection_enabled();
void face_detection_set(bool enabled);
void face_detection_json(ArduinoJson::JsonObject out);
}
