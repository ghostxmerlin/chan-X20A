/* SPDX-License-Identifier: MIT */
#include "face_detection.h"
#include "face_pixels.h"
#include <human_face_detect.hpp>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <linux/videodev2.h>
#include <memory>
#include <mutex>
#include <cmath>

namespace collector {
namespace {
std::mutex mutex;
bool enabled = false, ready = false;
uint32_t epoch = 0, frames = 0, errors = 0, last_seq = 0, inference_us = 0;
int64_t last_timestamp_us = 0;
size_t count = 0;
const char* error = "none";
// Owned exclusively by the existing camera task, including destruction.
std::unique_ptr<human_face_detect::MSRMNP> detector;
using PixelBuffer = std::unique_ptr<uint8_t, decltype(&heap_caps_free)>;
PixelBuffer input(nullptr, heap_caps_free);
}
bool face_detection_enabled() { std::lock_guard<std::mutex> lock(mutex); return enabled; }
void face_detection_set(bool value)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (enabled != value) { enabled = value; ++epoch; count = 0; last_seq = 0; last_timestamp_us = 0; }
    error = "none";
}
void face_detection_json(ArduinoJson::JsonObject out)
{
    std::lock_guard<std::mutex> lock(mutex);
    out["supported"] = true; out["enabled"] = enabled; out["ready"] = ready;
    out["epoch"] = epoch; out["frames"] = frames; out["errors"] = errors;
    out["frame_seq"] = last_seq; out["timestamp_us"] = last_timestamp_us;
    out["count"] = count; out["inference_us"] = inference_us; out["error"] = error;
    out["target_hz"] = kFaceTargetFps; out["max_boxes"] = kFaceMaxBoxes;
    out["model"] = "msr_mnp_s8_v1"; out["persistent"] = false;
}
std::string face_detect_frame(const uint8_t* pixels, size_t bytes, int width, int height,
                              uint32_t format, uint32_t seq, int64_t timestamp_us)
{
    uint32_t started_epoch;
    bool run;
    { std::lock_guard<std::mutex> lock(mutex); run = enabled; started_epoch = epoch; }
    if (!run) {
        detector.reset(); input.reset();
        std::lock_guard<std::mutex> lock(mutex); ready = false;
        return {};
    }
    const int64_t began = esp_timer_get_time();
    ArduinoJson::JsonDocument result;
    result["epoch"] = started_epoch;
    result["frame_seq"] = seq;
    auto boxes = result["boxes"].to<ArduinoJson::JsonArray>();
    const char* failure = nullptr;
    try {
        if (!detector) {
            // Leave existing audio, network and servo tasks room to operate.
            if (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) < 1500000 ||
                    heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) < 48000) failure = "memory_low";
            else {
                input.reset(static_cast<uint8_t*>(heap_caps_malloc(kFaceWidth * kFaceHeight * 3, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)));
                if (!input) failure = "memory_low";
                else {
                    detector = std::make_unique<human_face_detect::MSRMNP>(
                        "human_face_detect_msr_s8_v1.espdl", 0.6f, 0.5f,
                        "human_face_detect_mnp_s8_v1.espdl", 0.65f, 0.5f);
                }
            }
        }
        FacePixelFormat type = format == V4L2_PIX_FMT_YUYV ? FacePixelFormat::Yuyv :
            format == V4L2_PIX_FMT_RGB565 ? FacePixelFormat::Rgb565 :
            format == V4L2_PIX_FMT_RGB24 ? FacePixelFormat::Rgb888 : FacePixelFormat::Unsupported;
        if (!failure && !face_pixels(pixels, bytes, width, height, type, input.get())) failure = "pixel_format";
        if (!failure) {
            dl::image::img_t image = {.data = input.get(), .width = kFaceWidth, .height = kFaceHeight,
                                      .pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB888};
            // ESP-DL 3.3.0 defaults to single-core inference in this task.
            auto& faces = detector->run(image);
            for (const auto& face : faces) {
                if (boxes.size() >= kFaceMaxBoxes) break;
                if (face.box.size() != 4 || !std::isfinite(face.score)) continue;
                int x1 = std::clamp(face.box[0], 0, kFaceWidth), y1 = std::clamp(face.box[1], 0, kFaceHeight);
                int x2 = std::clamp(face.box[2], 0, kFaceWidth), y2 = std::clamp(face.box[3], 0, kFaceHeight);
                if (x2 <= x1 || y2 <= y1) continue;
                auto box = boxes.add<ArduinoJson::JsonArray>();
                box.add(x1 * 1000 / kFaceWidth); box.add(y1 * 1000 / kFaceHeight);
                box.add(x2 * 1000 / kFaceWidth); box.add(y2 * 1000 / kFaceHeight);
                box.add(static_cast<int>(std::clamp(face.score, 0.f, 1.f) * 1000));
            }
        }
    } catch (const std::bad_alloc&) { failure = "memory_low"; }
    const uint32_t elapsed = esp_timer_get_time() - began;
    std::lock_guard<std::mutex> lock(mutex);
    if (epoch != started_epoch || !enabled) return {}; // OFF/ON invalidates in-flight work.
    if (failure) {
        error = failure; ++errors; enabled = false; ++epoch; ready = false; count = 0;
        detector.reset(); input.reset();
        ESP_LOGW("FaceDetect", "Detection stopped: %s", failure);
        return {};
    }
    ready = true; error = "none"; ++frames; last_seq = seq; last_timestamp_us = timestamp_us;
    inference_us = elapsed; count = boxes.size(); result["inference_us"] = elapsed;
    std::string json; serializeJson(result, json); return json;
}
}
