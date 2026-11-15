/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <cstddef>
#include <cstdint>
#include <string>

namespace collector {
// Vision uplink (2026-11-10): M12 change events go to the ear server on their own WebSocket,
// independent of continuous hearing. Same backend host, port and token as hearing, path
// "/vision". One task (PSRAM stack) connects whenever the backend is configured, reconnects
// on its own, and drains m12_take_vision_events(); events wait in the M12 queue meanwhile.
void vision_uplink_init(const std::string& device_id, const std::string& boot_id);
void vision_uplink_json(ArduinoJson::JsonObject out);

// 视觉爆发 snap (0.4.99, vision_snap.h): called from the /mouth task only; each takes a short lock, never waits on
// the network or the camera. request: the ear's {"type":"snap","id"}; a refusal (busy, camera_off, no_uplink) is
// queued as its result at once, an accepted snap wakes the vision task. take_result: the next snap_result line
// for /mouth (runs the 2 s deadline first); false when none. link_down: /mouth dropped, the snap and results go.
void vision_snap_request(uint32_t id);
bool vision_snap_take_result(char* line, size_t cap, size_t& n);
void vision_snap_link_down();

// "ws://1.2.3.4:5/anything" → "ws://1.2.3.4:5/vision"; "" when the URL has no scheme.
inline std::string vision_url(const std::string& hearing_url)
{
    const size_t start = hearing_url.rfind("ws://", 0) == 0 ? 5 : hearing_url.rfind("wss://", 0) == 0 ? 6 : 0;
    if (!start) return {};
    const size_t slash = hearing_url.find('/', start);
    return hearing_url.substr(0, slash == std::string::npos ? hearing_url.size() : slash) + "/vision";
}
}
