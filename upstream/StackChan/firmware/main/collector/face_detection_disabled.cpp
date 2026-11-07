/* SPDX-License-Identifier: MIT */
// Basic profile adapter. The experimental ESP-DL implementation remains in
// face_detection.cpp for a later, separately validated feature rollout.
#include "face_detection.h"

namespace collector {
bool face_detection_enabled() { return false; }
void face_detection_set(bool) {}
void face_detection_json(ArduinoJson::JsonObject out)
{
    out["supported"] = false;
    out["enabled"] = false;
    out["ready"] = false;
    out["reason"] = "disabled_in_basic_profile";
    out["epoch"] = 0;
    out["frames"] = 0;
    out["errors"] = 0;
    out["frame_seq"] = 0;
    out["timestamp_us"] = 0;
    out["count"] = 0;
    out["inference_us"] = 0;
    out["error"] = "none";
    out["persistent"] = false;
}
std::string face_detect_frame(const uint8_t*, size_t, int, int,
                              uint32_t, uint32_t, int64_t)
{
    return {};
}
}
