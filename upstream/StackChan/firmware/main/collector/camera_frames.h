/* SPDX-License-Identifier: MIT */
#pragma once
// The body camera's newest frame, shared by the HTTP /v1/frame route and the vision uplink.
#include "frame_change.h"
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <string>

namespace collector {
struct CameraFrame {
    uint8_t* bytes = nullptr;        // JPEG, owned (freed with the frame)
    size_t length = 0;
    uint32_t seq = 0;
    int64_t timestamp_us = 0;
    int width = 0, height = 0;
    std::string faces;
    FrameThumb thumb;                // 16×12 luma signature of the raw frame (frame_change.h)
    ~CameraFrame() { free(bytes); }
};
// nullptr when the camera is off or the newest frame is older than 3 s.
std::shared_ptr<const CameraFrame> camera_latest_frame();
bool camera_streaming();
}
