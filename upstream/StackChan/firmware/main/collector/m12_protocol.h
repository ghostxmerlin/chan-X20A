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

// What the M12 should run (user, 2026-11-07): after power-on look around with face
// detection for a while, find out who a new face is with a short recognition
// burst, keep detecting while someone is there, and only then doze in standby
// (Audio FFT, camera off) to cool down. A wake event brings detection back.
// Pure logic: the M12 task feeds it results and time, then sends a command
// whenever function() changes.
enum class M12Function : uint8_t { none, detect, recognize, standby };
enum class M12Phase : uint8_t { idle, looking, watching, identifying, dozing };
struct M12Identity {
    char name[16] = {};
    float match_prob = 0;
    int64_t at_us = 0;  // when the burst ended; 0 = never
    bool known = false; // false: nobody enrolled matched
};
class M12Policy {
public:
    static constexpr int64_t kLookUs = 60000000;            // look around after boot / wake
    static constexpr int64_t kIdleUs = 60000000;            // no face, no activity -> doze
    static constexpr int64_t kIdentifyMaxUs = 10000000;     // one recognition burst at most
    static constexpr int64_t kIdentifyCooldownUs = 120000000;
    static constexpr int64_t kNewFaceGapUs = 5000000;       // a face after this much absence is "new"
    static constexpr float kMatch = 0.5f;                   // UnitV2 framework threshold
    static constexpr int kUnknownAfter = 6;                 // recognition results without a match

    void start(int64_t now_us) { look(now_us); }
    // Someone is interacting (voice command, touch, remote): wake up or stay awake.
    void wake(int64_t now_us)
    {
        activity_us_ = now_us;
        if (phase_ == M12Phase::dozing || phase_ == M12Phase::idle) look(now_us);
    }
    // The face page is open: never doze while it is shown.
    void view(bool on, int64_t now_us)
    {
        view_ = on;
        if (on) wake(now_us);
        else activity_us_ = now_us;
    }
    void result(int64_t now_us, size_t count, bool recognition, const char* best_name, float best_match)
    {
        if (recognition) {
            if (phase_ != M12Phase::identifying) return; // trailing results after a switch
            ++recognized_;
            if (count > 0 && best_name && best_name[0] && best_match >= kMatch) finish(now_us, best_name, best_match, true);
            else if (recognized_ >= kUnknownAfter) finish(now_us, "", 0, false);
            return;
        }
        if (count == 0) return;
        const bool new_face = last_face_us_ == 0 || now_us - last_face_us_ >= kNewFaceGapUs;
        last_face_us_ = activity_us_ = now_us;
        if (phase_ == M12Phase::looking && !seen_) {
            seen_ = true; // look-around time counts from the first real result
            look_until_us_ = now_us + kLookUs;
        }
        if ((phase_ == M12Phase::looking || phase_ == M12Phase::watching) && new_face &&
            (last_identify_us_ == 0 || now_us - last_identify_us_ >= kIdentifyCooldownUs)) {
            phase_ = M12Phase::identifying;
            identify_started_us_ = now_us;
            recognized_ = 0;
        }
    }
    void tick(int64_t now_us)
    {
        if (phase_ == M12Phase::identifying && now_us - identify_started_us_ >= kIdentifyMaxUs)
            finish(now_us, "", 0, false);
        if (phase_ == M12Phase::looking && now_us >= look_until_us_) phase_ = M12Phase::watching;
        if (phase_ == M12Phase::watching && !view_ && now_us - activity_us_ >= kIdleUs) phase_ = M12Phase::dozing;
    }
    M12Function function() const
    {
        switch (phase_) {
        case M12Phase::looking:
        case M12Phase::watching: return M12Function::detect;
        case M12Phase::identifying: return M12Function::recognize;
        case M12Phase::dozing: return M12Function::standby;
        default: return M12Function::none;
        }
    }
    M12Phase phase() const { return phase_; }
    const M12Identity& identity() const { return identity_; }

private:
    void look(int64_t now_us)
    {
        phase_ = M12Phase::looking;
        look_until_us_ = now_us + kLookUs;
        activity_us_ = now_us;
        seen_ = false;
    }
    void finish(int64_t now_us, const char* name, float match, bool known)
    {
        std::snprintf(identity_.name, sizeof(identity_.name), "%s", name ? name : "");
        identity_.match_prob = match;
        identity_.at_us = now_us;
        identity_.known = known;
        phase_ = M12Phase::watching;
        last_identify_us_ = last_face_us_ = activity_us_ = now_us; // the gap while identifying is not "absence"
    }
    M12Phase phase_ = M12Phase::idle;
    M12Identity identity_;
    bool view_ = false, seen_ = false;
    int recognized_ = 0;
    int64_t look_until_us_ = 0, activity_us_ = 0, last_face_us_ = 0, last_identify_us_ = 0, identify_started_us_ = 0;
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
