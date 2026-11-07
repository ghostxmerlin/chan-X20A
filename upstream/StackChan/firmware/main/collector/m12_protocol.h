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
// 2-3 results/s, about 92-93 C; only in short bursts to learn who a new face is.
constexpr char kM12FaceRecognitionCommand[] = "{\"function\":\"Face Recognition\",\"args\":\"\"}\r\n";
// Standby: camera off, about 15% CPU, nothing written to the serial port.
constexpr char kM12StandbyCommand[] = "{\"function\":\"Audio FFT\",\"args\":\"\"}\r\n";
constexpr int kM12FrameWidth = 640, kM12FrameHeight = 480; // face coordinates
constexpr size_t kM12MaxFaces = 6;
constexpr size_t kM12MaxLine = 4096;

struct M12Face {
    int16_t x = 0, y = 0, w = 0, h = 0;
    float prob = 0;
    char name[16] = {};   // Face Recognition only: enrolled name of the best match
    float match_prob = 0; // Face Recognition only
};
enum class M12LineKind : uint8_t { other, faces, reply, error, invalid };
struct M12Line {
    M12LineKind kind = M12LineKind::other;
    char running[24] = {}; // "running" field, e.g. "Face Detector"
    char text[64] = {};    // "msg" or "error" text
    uint16_t num = 0;      // face count reported by the device
    size_t count = 0;      // faces kept, at most kM12MaxFaces
    bool recognition = false; // from Face Recognition (coordinates not used for drawing)
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
    for (const char* key : {"x", "y", "w", "h", "prob", "name", "match_prob"}) face[key] = true;
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
    out.recognition = std::strcmp(out.running, "Face Recognition") == 0;
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
        if (const char* name = item["name"]) std::snprintf(f.name, sizeof(f.name), "%s", name);
        f.match_prob = item["match_prob"] | 0.0f;
        if (f.w <= 0 || f.h <= 0) continue;
        out.faces[out.count++] = f;
    }
    return out;
}

// Basic controls (2026-11-07): standby after boot; only an explicit face-page
// request selects Face Detector. Time, face results and ordinary wake events
// must never change the requested function. Recognition parsing remains available
// for diagnostics, but this policy never requests Face Recognition.
enum class M12Function : uint8_t { none, detect, recognize, standby };
// A recent detector frame may trail a standby request. Every requested function
// change must therefore send a command, independently of received face data.
inline bool m12_switch_required(M12Function active, M12Function want)
{
    return want != active && want != M12Function::none;
}
enum class M12Phase : uint8_t { idle, looking, watching, identifying, dozing };
struct M12Identity {
    char name[16] = {};
    float match_prob = 0;
    int64_t at_us = 0;
    bool known = false;
};
class M12Policy {
public:
    void start(int64_t) { detecting_ = false; }
    void wake(int64_t) {} // compatibility: ordinary interaction cannot start the camera
    void view(bool on, int64_t) { detecting_ = on; }
    void result(int64_t, size_t, bool, const char*, float) {}
    void tick(int64_t) {}
    M12Function function() const { return detecting_ ? M12Function::detect : M12Function::standby; }
    M12Phase phase() const { return detecting_ ? M12Phase::watching : M12Phase::dozing; }
    const M12Identity& identity() const { return identity_; }

private:
    bool detecting_ = false;
    M12Identity identity_;
};

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
