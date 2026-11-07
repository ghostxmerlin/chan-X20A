/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace collector {
// M5Stack UnitV2 (M12) built-in firmware on its Grove UART, 115200 8N1.
// Commands are one JSON object per line ending in CRLF (pyserial LineReader). The
// device lower-cases the function name and replaces spaces with '_' before looking
// it up (server_core.py SerialRX.handle_line), so "Audio FFT" selects audio_fft.
constexpr char kM12FaceDetectorCommand[] = "{\"function\":\"Face Detector\",\"args\":\"\"}\r\n";
// Standby: camera off, about 15% CPU, nothing written to the serial port.
constexpr char kM12StandbyCommand[] = "{\"function\":\"Audio FFT\",\"args\":\"\"}\r\n";
constexpr int kM12FrameWidth = 640, kM12FrameHeight = 480; // face coordinates
constexpr size_t kM12MaxFaces = 6;
constexpr size_t kM12MaxLine = 4096;

struct M12Face {
    int16_t x = 0, y = 0, w = 0, h = 0;
    float prob = 0;
};
enum class M12LineKind : uint8_t { other, faces, reply, error, invalid };
struct M12Line {
    M12LineKind kind = M12LineKind::other;
    char running[24] = {}; // "running" field, e.g. "Face Detector"
    char text[64] = {};    // "msg" or "error" text
    uint16_t num = 0;      // face count reported by the device
    size_t count = 0;      // faces kept, at most kM12MaxFaces
    M12Face faces[kM12MaxFaces];
};

// Assemble newline-terminated lines from the byte stream. CR is ignored; a line
// longer than kM12MaxLine is dropped whole rather than parsed half-way.
class M12LineBuffer {
public:
    bool push(char c, std::string& line)
    {
        if (c == '\r') return false;
        if (c == '\n') {
            const bool complete = !dropping_ && !buffer_.empty();
            if (complete) line.swap(buffer_);
            buffer_.clear();
            dropping_ = false;
            return complete;
        }
        if (dropping_) return false;
        if (buffer_.size() >= kM12MaxLine) {
            buffer_.clear();
            dropping_ = true;
            ++overflows_;
            return false;
        }
        buffer_ += c;
        return false;
    }
    uint32_t overflows() const { return overflows_; }

private:
    std::string buffer_;
    bool dropping_ = false;
    uint32_t overflows_ = 0;
};

inline M12Line parse_m12_line(const std::string& line)
{
    M12Line out;
    ArduinoJson::JsonDocument filter;
    filter["running"] = true;
    filter["num"] = true;
    filter["msg"] = true;
    filter["error"] = true;
    auto face = filter["face"][0].to<ArduinoJson::JsonObject>();
    for (const char* key : {"x", "y", "w", "h", "prob"}) face[key] = true;
    ArduinoJson::JsonDocument doc;
    if (deserializeJson(doc, line, ArduinoJson::DeserializationOption::Filter(filter)) ||
        !doc.is<ArduinoJson::JsonObject>()) {
        out.kind = M12LineKind::invalid;
        return out;
    }
    if (const char* running = doc["running"]) std::snprintf(out.running, sizeof(out.running), "%s", running);
    if (const char* error = doc["error"]) {
        out.kind = M12LineKind::error;
        std::snprintf(out.text, sizeof(out.text), "%s", error);
        return out;
    }
    if (const char* msg = doc["msg"]) {
        out.kind = M12LineKind::reply;
        std::snprintf(out.text, sizeof(out.text), "%s", msg);
        return out;
    }
    const bool face_function = std::strcmp(out.running, "Face Detector") == 0 ||
                               std::strcmp(out.running, "Face Recognition") == 0;
    auto faces = doc["face"].as<ArduinoJson::JsonArray>();
    if (!face_function || faces.isNull()) return out;
    out.kind = M12LineKind::faces;
    out.num = static_cast<uint16_t>(std::clamp<int>(doc["num"] | static_cast<int>(faces.size()), 0, 1000));
    // The device sends coordinates as floats ("x":168.92...). Read them as floats:
    // ArduinoJson's `value | 0` would return the int default for a float value.
    auto coord = [](ArduinoJson::JsonVariantConst value, int limit) {
        const float v = value | 0.0f;
        return std::clamp(static_cast<int>(v + 0.5f), 0, limit);
    };
    for (auto item : faces) {
        if (out.count == kM12MaxFaces) break;
        M12Face f;
        const int x = coord(item["x"], kM12FrameWidth);
        const int y = coord(item["y"], kM12FrameHeight);
        f.x = x;
        f.y = y;
        f.w = coord(item["w"], kM12FrameWidth - x);
        f.h = coord(item["h"], kM12FrameHeight - y);
        f.prob = item["prob"] | 0.0f;
        if (f.w <= 0 || f.h <= 0) continue;
        out.faces[out.count++] = f;
    }
    return out;
}

// M12 face box (640x480) on the 320x240 screen, mirrored like a selfie camera so
// the box moves the same way as the person in front of the robot.
struct ScreenBox {
    int16_t x = 0, y = 0, w = 0, h = 0;
};
inline ScreenBox m12_to_screen(const M12Face& f, bool mirror = true)
{
    const int x = mirror ? kM12FrameWidth - f.x - f.w : f.x;
    ScreenBox b;
    b.x = static_cast<int16_t>(std::clamp(x / 2, 0, 319));
    b.y = static_cast<int16_t>(std::clamp(f.y / 2, 0, 239));
    b.w = static_cast<int16_t>(std::clamp(f.w / 2, 1, 320 - b.x));
    b.h = static_cast<int16_t>(std::clamp(f.h / 2, 1, 240 - b.y));
    return b;
}
}
