/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace collector {
// M12 (UnitV2) running m12d, the user's own resident program (2026-11-10; replaces the
// factory firmware, whose {"function":...} switching, audio_fft standby and Face
// Recognition training no longer exist). Grove UART, 115200 8N1, one JSON object per
// line. Protocol: m12d docs/protocol.md; migration notes: docs/m12d-migration.md.
//   push   (no "id"):  detection line {"f":..,"num":..,"face":[..],"person":[..]}
//                      heartbeat {"hb":{"t":..,"fps":..,"tier":..,"up":..,...}} every 2 s
//                      event {"ev":{"t":"nod","fr":..,...}} (m12d 0.2.0+, §3.3; unknown t ignored)
//   request:           {"id":N,"do":"ping|ver|status|cfg|learn|who|forget|faces",...}
//   reply:             {"id":N,"ok":true,...} or {"id":N,"ok":false,"err":"<code>"}
// Replies and pushes interleave; match replies by id. Unknown fields are ignored.
constexpr int kM12FrameWidth = 640, kM12FrameHeight = 480;    // face/person coordinates
constexpr size_t kM12MaxFaces = 6;          // kept per detection line (m12d sends up to 10)
constexpr size_t kM12MaxPersons = 3;        // kept per detection line (m12d sends up to 5)
constexpr size_t kM12MaxLine = 4096;        // receive line buffer (PSRAM); longer lines are dropped
constexpr size_t kM12MaxRequest = 512;      // m12d drops longer request lines ("toolong")
constexpr size_t kM12NameBytes = 32;        // UTF-8, 1..32 bytes, no control characters
constexpr size_t kM12LibraryMax = 16;       // names in the M12 face library
constexpr int64_t kM12HeartbeatTimeoutUs = 6000000;   // no hb for 6 s: M12 offline
constexpr int64_t kM12SlowReplyUs = 4000000;  // learn/who (m12d answers within 3 s)
constexpr int64_t kM12FastReplyUs = 1000000;  // everything else

enum class M12AgeGroup : uint8_t { none, child, young, adult, elder };
inline const char* m12_age_group_name(M12AgeGroup g)
{
    switch (g) {
    case M12AgeGroup::child: return "child";
    case M12AgeGroup::young: return "young";
    case M12AgeGroup::adult: return "adult";
    case M12AgeGroup::elder: return "elder";
    default: return "";
    }
}
struct M12Face {
    int16_t x = 0, y = 0, w = 0, h = 0;   // 640x480
    float prob = 0;
    int32_t id = -1;                      // tracking id; changes after the face is gone > 1 s
    char gender = 0;                      // 'm', 'f', '?'; 0 = not reported yet
    int16_t age = -1;
    M12AgeGroup age_group = M12AgeGroup::none;
    char name[kM12NameBytes + 1] = {};    // set once who/learn recognised this face
    // Head pose (m12d 0.2.0+, cfg pose:true, faces with an id; all optional, approximate).
    int16_t yaw = 0, pitch = 0, roll = 0; // degrees; valid when has_pose. yaw +: face turned to frame right,
                                          // pitch +: head up, roll +: crown towards frame right
    bool has_pose = false;
    int8_t look = -1;                     // facing StackChan (|yaw|, |pitch| <= 15): 1 / 0; -1 = not reported
    int16_t dist_cm = -1;                 // rough distance from the face width (uncalibrated); -1 = not reported
};
struct M12Person {
    int16_t x = 0, y = 0, w = 0, h = 0;
    float prob = 0;
    int32_t id = -1;                      // separate numbering from face ids
    int32_t face = -1;                    // paired face id, -1 none
};
struct M12Detection {
    uint32_t f = 0;                       // frame number
    uint16_t num = 0;                     // faces m12d reported
    size_t count = 0;                     // faces kept
    M12Face faces[kM12MaxFaces];
    size_t persons = 0;
    M12Person person[kM12MaxPersons];
};
struct M12Heartbeat {
    bool valid = false;
    float t = 0;                          // chip temperature, C
    float fps = 0;
    int tier = 0;                         // 3 = detection paused, too hot (>= 90 C, resumes < 87 C)
    uint32_t up = 0, mem = 0, frames = 0, reopens = 0, drop = 0, mhz = 0;
};
struct M12Config {
    bool valid = false;
    bool det = true, lm = true, attr = true, person = true, sub = true;
    float id_th = 0.5f, fps_cap = 0;
    int max_faces = 10;
};
struct M12LibraryEntry {
    char name[kM12NameBytes + 1] = {};
    uint8_t samples = 0;
};
struct M12Reply {
    int32_t id = -1;
    bool ok = false;
    char err[12] = {};                    // error code when !ok
    bool has_name = false;                // a "name" string (learn/forget/who/conflict); who null → false
    char name[kM12NameBytes + 1] = {};
    int samples = -1;
    int32_t face = -1;
    float sim = -1;
    uint32_t f = 0;
    char ver[16] = {};
    M12Config cfg;
    M12Heartbeat hb;                      // status reply
    bool has_library = false;
    uint8_t library_count = 0;
    M12LibraryEntry library[kM12LibraryMax];
};
// ---------- events (m12d 0.2.0+, protocol §3.3) ----------
// One {"ev":{"t":..,"fr":..,...}} push line: what happened in view (2026-11-17: shown only, nothing reacts yet).
// Known types only; a line with any other t parses as M12Kind::other (ignored, counted). Field sentinels: -1 /
// 0 / false = not in the line.
enum class M12EvType : uint8_t {
    wake, cover, uncover, light, dark, bright, enter, leave, near, far, look, look_end, nod, shake, tilt, smile, count
};
inline const char* m12_ev_type_name(M12EvType t)
{
    static const char* const names[] = {"wake", "cover", "uncover", "light", "dark", "bright", "enter", "leave",
                                        "near", "far", "look", "look_end", "nod", "shake", "tilt", "smile"};
    static_assert(sizeof(names) / sizeof(names[0]) == static_cast<size_t>(M12EvType::count), "one name per type");
    return t < M12EvType::count ? names[static_cast<size_t>(t)] : "";
}
inline bool m12_ev_type_from_name(const char* s, M12EvType& out)
{
    if (!s) return false;
    for (uint8_t i = 0; i < static_cast<uint8_t>(M12EvType::count); ++i)
        if (!std::strcmp(s, m12_ev_type_name(static_cast<M12EvType>(i)))) { out = static_cast<M12EvType>(i); return true; }
    return false;
}
constexpr size_t kM12EvFaces = 3, kM12EvPersons = 3;   // kept per wake / light analysis (count of all in *_seen)
struct M12EvBox { int16_t x = 0, y = 0, w = 0, h = 0; };
struct M12EvFace {
    M12EvBox box;
    float sim = -1;                       // with a name: who similarity
    char name[kM12NameBytes + 1] = {};
};
struct M12Event {
    M12EvType type = M12EvType::wake;
    uint32_t fr = 0;                      // m12d frame number (same count as the detection line's f)
    int32_t face = -1, person = -1;       // ids: look/nod/... face; enter/leave/near/far face or person
    int16_t n = -1;                       // nod / shake: how many
    char side = 0;                        // tilt: 'l' / 'r'
    int8_t on = -1;                       // light: 1 on / 0 off
    bool pan = false, hot = false;        // wake: the M12 was turned / analysis skipped (too hot)
    bool has_area = false, has_scan = false;   // wake: area given / faces or persons given
    float dur = -1;                       // uncover / leave / look_end: seconds
    float amt = -1;                       // wake: changed share of pixels, 0..1
    float d[2] = {-1, -1};                // near / far (faces): estimated distance before, after, m
    int16_t lum[2] = {-1, -1};            // cover / uncover / light / dark / bright: mean brightness before, after
    int16_t cd = -1;                      // wake: cooldown from now, s
    M12EvBox area;                        // wake: changed region, 640x480
    uint8_t faces_seen = 0, persons_seen = 0;   // wake / light analysis: how many m12d reported
    uint8_t faces = 0, persons = 0;             // ... and kept here
    M12EvFace face_list[kM12EvFaces];
    M12EvBox person_list[kM12EvPersons];
};

enum class M12Kind : uint8_t { invalid, detection, heartbeat, reply, other, event };
struct M12Message {
    M12Kind kind = M12Kind::invalid;
    M12Detection det;
    M12Heartbeat hb;
    M12Reply reply;
    M12Event ev;
    bool ev_unknown = false;              // an {"ev":...} line whose t this build does not know (kind other)
};

// Newline-terminated lines from the byte stream, assembled in caller-provided storage
// (PSRAM on the robot). CR is ignored. A line longer than the storage is dropped whole;
// the first line after power-up may be a fragment and simply fails to parse.
class M12LineBuffer {
public:
    M12LineBuffer(char* storage, size_t capacity) : buf_(storage), cap_(capacity) {}
    // True when a complete line is ready in line()/size(); valid until the next push.
    bool push(char c)
    {
        if (ready_) { size_ = 0; ready_ = false; }
        if (c == '\r') return false;
        if (c == '\n') {
            const bool complete = !dropping_ && size_ > 0;
            dropping_ = false;
            if (!complete) { size_ = 0; return false; }
            buf_[size_] = 0;
            ready_ = true;
            return true;
        }
        if (dropping_) return false;
        if (size_ + 1 >= cap_) { size_ = 0; dropping_ = true; ++overflows_; return false; }
        buf_[size_++] = c;
        return false;
    }
    const char* line() const { return buf_; }
    size_t size() const { return size_; }
    uint32_t overflows() const { return overflows_; }

private:
    char* buf_;
    size_t cap_;
    size_t size_ = 0;
    bool dropping_ = false, ready_ = false;
    uint32_t overflows_ = 0;
};

inline bool m12_valid_name(const char* name)
{
    if (!name) return false;
    const size_t n = std::strlen(name);
    if (n < 1 || n > kM12NameBytes) return false;
    for (size_t i = 0; i < n; ++i) {
        const auto c = static_cast<unsigned char>(name[i]);
        if (c < 0x20 || c == 0x7f) return false;
    }
    return true;
}

namespace m12_detail {
inline int16_t coord(ArduinoJson::JsonVariantConst v, int limit)
{
    const float f = v | 0.0f;
    return static_cast<int16_t>(std::clamp(static_cast<int>(std::lround(std::isfinite(f) ? f : 0.0f)), 0, limit));
}
inline void copy_name(char (&out)[kM12NameBytes + 1], const char* in)
{
    out[0] = 0;
    if (in && m12_valid_name(in)) std::snprintf(out, sizeof(out), "%s", in);
}
inline void read_hb(ArduinoJson::JsonObjectConst o, M12Heartbeat& hb)
{
    if (o.isNull()) return;
    hb.valid = true;
    hb.t = o["t"] | 0.0f; hb.fps = o["fps"] | 0.0f; hb.tier = o["tier"] | 0;
    hb.up = o["up"] | 0u; hb.mem = o["mem"] | 0u; hb.frames = o["frames"] | 0u;
    hb.reopens = o["reopens"] | 0u; hb.drop = o["drop"] | 0u; hb.mhz = o["mhz"] | 0u;
}
inline bool number(ArduinoJson::JsonVariantConst v, float& out)
{
    if (!v.is<float>() || v.is<bool>()) return false;
    const float f = v.as<float>();
    if (!std::isfinite(f)) return false;
    out = f;
    return true;
}
inline int32_t id(ArduinoJson::JsonVariantConst v)
{
    if (!v.is<long long>()) return -1;
    const long long n = v.as<long long>();
    return n < 0 || n > 2147483647LL ? -1 : static_cast<int32_t>(n);
}
inline M12EvBox box(ArduinoJson::JsonObjectConst o)
{
    M12EvBox b;
    b.x = coord(o["x"], kM12FrameWidth);
    b.y = coord(o["y"], kM12FrameHeight);
    b.w = coord(o["w"], kM12FrameWidth - b.x);
    b.h = coord(o["h"], kM12FrameHeight - b.y);
    return b;
}
// The five 0.2.0 pose fields of one detection-line face; each optional (cfg pose:false sends none).
inline void read_pose(ArduinoJson::JsonObjectConst item, M12Face& f)
{
    float yaw, pitch, roll, dist;
    if (number(item["yaw"], yaw) && number(item["pitch"], pitch) && number(item["roll"], roll)) {
        f.has_pose = true;
        f.yaw = static_cast<int16_t>(std::clamp<long>(std::lround(yaw), -180, 180));
        f.pitch = static_cast<int16_t>(std::clamp<long>(std::lround(pitch), -180, 180));
        f.roll = static_cast<int16_t>(std::clamp<long>(std::lround(roll), -180, 180));
    }
    if (item["look"].is<bool>()) f.look = item["look"].as<bool>() ? 1 : 0;
    if (number(item["dist"], dist) && dist >= 0) f.dist_cm = static_cast<int16_t>(std::min<long>(std::lround(dist * 100), 10000));
}
// {"ev":{...}} → event. false: a type this build does not know (ignored).
inline bool read_event(ArduinoJson::JsonObjectConst o, M12Event& e)
{
    using namespace ArduinoJson;
    if (!m12_ev_type_from_name(o["t"].as<const char*>(), e.type)) return false;
    e.fr = o["fr"] | 0u;
    e.face = id(o["face"]);
    e.person = id(o["person"]);
    if (o["n"].is<int>()) e.n = static_cast<int16_t>(std::clamp(o["n"].as<int>(), 0, 1000));
    if (const char* s = o["side"]; s && (s[0] == 'l' || s[0] == 'r') && !s[1]) e.side = s[0];
    if (o["on"].is<bool>()) e.on = o["on"].as<bool>() ? 1 : 0;
    e.pan = o["pan"] | false;
    e.hot = o["hot"] | false;
    float v;
    if (number(o["dur"], v) && v >= 0) e.dur = std::min(v, 86400.0f);
    if (number(o["amt"], v) && v >= 0) e.amt = std::min(v, 1.0f);
    if (number(o["cd"], v) && v >= 0) e.cd = static_cast<int16_t>(std::min<long>(std::lround(v), 32767));
    if (JsonArrayConst d = o["d"]; d.size() == 2) {
        float a, b;
        if (number(d[0], a) && number(d[1], b) && a >= 0 && b >= 0) { e.d[0] = a; e.d[1] = b; }
    }
    auto level = [](JsonVariantConst x) -> int16_t {
        float f;
        return number(x, f) ? static_cast<int16_t>(std::clamp<long>(std::lround(f), 0, 255)) : -1;
    };
    if (JsonArrayConst lum = o["lum"]; !lum.isNull()) { if (lum.size() >= 1) e.lum[0] = level(lum[0]); if (lum.size() >= 2) e.lum[1] = level(lum[1]); }
    else if (o["lum"].is<float>()) e.lum[1] = level(o["lum"]);   // a single value: the brightness now
    if (JsonObjectConst a = o["area"]; !a.isNull()) { e.area = box(a); e.has_area = e.area.w > 0 && e.area.h > 0; }
    JsonArrayConst faces = o["faces"], persons = o["persons"];
    e.has_scan = !faces.isNull() || !persons.isNull();
    e.faces_seen = static_cast<uint8_t>(std::min<size_t>(faces.size(), 255));
    e.persons_seen = static_cast<uint8_t>(std::min<size_t>(persons.size(), 255));
    for (JsonObjectConst item : faces) {
        if (e.faces == kM12EvFaces) break;
        M12EvFace f;
        f.box = box(item);
        if (f.box.w <= 0 || f.box.h <= 0) continue;
        copy_name(f.name, item["name"]);
        if (f.name[0] && number(item["sim"], v)) f.sim = v;
        e.face_list[e.faces++] = f;
    }
    for (JsonObjectConst item : persons) {
        if (e.persons == kM12EvPersons) break;
        const M12EvBox b = box(item);
        if (b.w > 0 && b.h > 0) e.person_list[e.persons++] = b;
    }
    return true;
}
}

// Event fields as JSON (telemetry m12.events and the vision uplink's m12_ev). Only what the line had; "ev" = type.
inline void m12_event_json(const M12Event& e, ArduinoJson::JsonObject out)
{
    auto box = [](ArduinoJson::JsonArray a, const M12EvBox& b) { a.add(b.x); a.add(b.y); a.add(b.w); a.add(b.h); };
    out["ev"] = m12_ev_type_name(e.type);
    if (e.face >= 0) out["face"] = e.face;
    if (e.person >= 0) out["person"] = e.person;
    if (e.n >= 0) out["n"] = e.n;
    if (e.side) out["side"] = e.side == 'l' ? "l" : "r";
    if (e.on >= 0) out["on"] = e.on == 1;
    if (e.dur >= 0) out["dur"] = std::round(e.dur * 10) / 10;
    if (e.d[0] >= 0) { auto d = out["d"].to<ArduinoJson::JsonArray>(); d.add(std::round(e.d[0] * 10) / 10); d.add(std::round(e.d[1] * 10) / 10); }
    if (e.lum[0] >= 0 || e.lum[1] >= 0) { auto l = out["lum"].to<ArduinoJson::JsonArray>(); l.add(e.lum[0]); l.add(e.lum[1]); }
    if (e.has_area) box(out["area"].to<ArduinoJson::JsonArray>(), e.area);
    if (e.amt >= 0) out["amt"] = std::round(e.amt * 1000) / 1000;
    if (e.cd >= 0) out["cd"] = e.cd;
    if (e.pan) out["pan"] = true;
    if (e.hot) out["hot"] = true;
    if (e.has_scan) {
        auto faces = out["faces"].to<ArduinoJson::JsonArray>();
        for (uint8_t i = 0; i < e.faces; ++i) {
            const auto& f = e.face_list[i];
            auto o = faces.add<ArduinoJson::JsonObject>();
            box(o["box"].to<ArduinoJson::JsonArray>(), f.box);
            if (f.name[0]) o["name"] = std::string(f.name);
            if (f.name[0] && f.sim >= 0) o["sim"] = std::round(f.sim * 1000) / 1000;
        }
        auto persons = out["persons"].to<ArduinoJson::JsonArray>();
        for (uint8_t i = 0; i < e.persons; ++i) box(persons.add<ArduinoJson::JsonArray>(), e.person_list[i]);
        if (e.faces_seen > e.faces) out["faces_seen"] = e.faces_seen;
        if (e.persons_seen > e.persons) out["persons_seen"] = e.persons_seen;
    }
}

// One line → message. `doc` may carry a PSRAM allocator; it is cleared here.
inline bool parse_m12_line(const char* line, size_t n, M12Message& out, ArduinoJson::JsonDocument& doc)
{
    using namespace ArduinoJson;
    out = M12Message{};
    doc.clear();
    if (deserializeJson(doc, line, n) || !doc.is<JsonObjectConst>()) return false;
    JsonObjectConst o = doc.as<JsonObjectConst>();
    if (o["id"].is<int>() || o["id"].is<long long>()) {
        auto& r = out.reply;
        out.kind = M12Kind::reply;
        const long long id = o["id"].as<long long>();
        r.id = id < -1 || id > 2147483647LL ? -1 : static_cast<int32_t>(id);
        r.ok = o["ok"] | false;
        if (const char* e = o["err"]) std::snprintf(r.err, sizeof(r.err), "%s", e);
        if (!r.ok && !r.err[0]) std::snprintf(r.err, sizeof(r.err), "unknown");
        // who answers "name":null when nobody matched: has_name stays false, name empty.
        if (o["name"].is<const char*>()) {
            r.has_name = true;
            m12_detail::copy_name(r.name, o["name"].as<const char*>());
        }
        r.samples = o["samples"] | -1;
        r.face = o["face"] | -1;
        r.sim = o["sim"] | -1.0f;
        r.f = o["f"] | 0u;
        if (const char* v = o["ver"]) std::snprintf(r.ver, sizeof(r.ver), "%s", v);
        if (JsonObjectConst c = o["cfg"]; !c.isNull()) {
            r.cfg.valid = true;
            r.cfg.det = c["det"] | true; r.cfg.lm = c["lm"] | true; r.cfg.attr = c["attr"] | true;
            r.cfg.person = c["person"] | true; r.cfg.sub = c["sub"] | true;
            r.cfg.id_th = c["id_th"] | 0.5f; r.cfg.fps_cap = c["fps_cap"] | 0.0f; r.cfg.max_faces = c["max_faces"] | 10;
        }
        m12_detail::read_hb(o["hb"], r.hb);
        if (JsonArrayConst list = o["faces"]; !list.isNull()) {
            r.has_library = true;
            for (JsonObjectConst e : list) {
                if (r.library_count == kM12LibraryMax) break;
                auto& entry = r.library[r.library_count];
                m12_detail::copy_name(entry.name, e["name"]);
                if (!entry.name[0]) continue;
                entry.samples = static_cast<uint8_t>(std::clamp(e["samples"] | 0, 0, 255));
                ++r.library_count;
            }
        }
        return true;
    }
    if (JsonObjectConst hb = o["hb"]; !hb.isNull()) {
        out.kind = M12Kind::heartbeat;
        m12_detail::read_hb(hb, out.hb);
        return true;
    }
    if (o["f"].is<unsigned long>() || o["f"].is<long long>()) {
        auto& d = out.det;
        out.kind = M12Kind::detection;
        d.f = o["f"] | 0u;
        JsonArrayConst faces = o["face"];
        d.num = static_cast<uint16_t>(std::clamp<int>(o["num"] | static_cast<int>(faces.size()), 0, 1000));
        for (JsonObjectConst item : faces) {
            if (d.count == kM12MaxFaces) break;
            M12Face f;
            f.x = m12_detail::coord(item["x"], kM12FrameWidth);
            f.y = m12_detail::coord(item["y"], kM12FrameHeight);
            f.w = m12_detail::coord(item["w"], kM12FrameWidth - f.x);
            f.h = m12_detail::coord(item["h"], kM12FrameHeight - f.y);
            f.prob = item["prob"] | 0.0f;
            f.id = item["id"] | -1;
            if (const char* g = item["gender"]; g && (g[0] == 'm' || g[0] == 'f' || g[0] == '?') && !g[1]) f.gender = g[0];
            f.age = static_cast<int16_t>(std::clamp(item["age"] | -1, -1, 150));
            if (const char* a = item["age_group"]) {
                f.age_group = !std::strcmp(a, "child") ? M12AgeGroup::child : !std::strcmp(a, "young") ? M12AgeGroup::young :
                    !std::strcmp(a, "adult") ? M12AgeGroup::adult : !std::strcmp(a, "elder") ? M12AgeGroup::elder : M12AgeGroup::none;
            }
            m12_detail::copy_name(f.name, item["name"]);
            m12_detail::read_pose(item, f);
            if (f.w <= 0 || f.h <= 0 || !std::isfinite(f.prob)) continue;
            d.faces[d.count++] = f;
        }
        for (JsonObjectConst item : o["person"].as<JsonArrayConst>()) {
            if (d.persons == kM12MaxPersons) break;
            M12Person p;
            p.x = m12_detail::coord(item["x"], kM12FrameWidth);
            p.y = m12_detail::coord(item["y"], kM12FrameHeight);
            p.w = m12_detail::coord(item["w"], kM12FrameWidth - p.x);
            p.h = m12_detail::coord(item["h"], kM12FrameHeight - p.y);
            p.prob = item["prob"] | 0.0f;
            p.id = item["id"] | -1;
            p.face = item["face"] | -1;
            if (p.w <= 0 || p.h <= 0) continue;
            d.person[d.persons++] = p;
        }
        return true;
    }
    // m12d 0.2.0 event line: only "ev" at the top (no id / hb / f). Unknown t: ignored like any unknown line.
    if (JsonObjectConst ev = o["ev"]; !ev.isNull()) {
        if (m12_detail::read_event(ev, out.ev)) { out.kind = M12Kind::event; return true; }
        out.ev = M12Event{};
        out.ev_unknown = true;
    }
    out.kind = M12Kind::other;
    return true;
}

// ---------- requests ----------
enum class M12Op : uint8_t { ping, ver, status, cfg, learn, who, forget, faces };
inline const char* m12_op_name(M12Op op)
{
    switch (op) {
    case M12Op::ping: return "ping";
    case M12Op::ver: return "ver";
    case M12Op::status: return "status";
    case M12Op::cfg: return "cfg";
    case M12Op::learn: return "learn";
    case M12Op::who: return "who";
    case M12Op::forget: return "forget";
    default: return "faces";
    }
}
inline bool m12_op_from_name(const char* s, M12Op& op)
{
    for (M12Op o : {M12Op::ping, M12Op::ver, M12Op::status, M12Op::cfg, M12Op::learn, M12Op::who, M12Op::forget, M12Op::faces})
        if (s && !std::strcmp(s, m12_op_name(o))) { op = o; return true; }
    return false;
}
inline int64_t m12_reply_timeout_us(M12Op op)
{
    return op == M12Op::learn || op == M12Op::who ? kM12SlowReplyUs : kM12FastReplyUs;
}
struct M12RequestArgs {
    const char* name = nullptr;           // learn/forget (required), who/learn never take it otherwise
    int32_t face = -1;                    // learn/who: face id from a detection line; -1 = largest face
    int det = -1;                         // cfg: -1 leave, 0 standby, 1 detect
    int lm = -1;                          // cfg: -1 leave, 0/1 five-point landmarks in detection lines
};
// The request line including '\n', or 0 if the arguments are invalid / it does not fit.
inline size_t m12_build_request(char* out, size_t capacity, int32_t id, M12Op op, const M12RequestArgs& a = {})
{
    ArduinoJson::JsonDocument doc;
    doc["id"] = id;
    doc["do"] = m12_op_name(op);
    if (op == M12Op::learn || op == M12Op::forget) {
        if (!m12_valid_name(a.name)) return 0;
        doc["name"] = a.name;
    }
    if ((op == M12Op::learn || op == M12Op::who) && a.face >= 0) doc["face"] = a.face;
    if (op == M12Op::cfg && a.det >= 0) doc["det"] = a.det == 1;
    if (op == M12Op::cfg && a.lm >= 0) doc["lm"] = a.lm == 1;
    const size_t n = ArduinoJson::serializeJson(doc, out, capacity);
    if (n == 0 || n + 2 > capacity || n + 1 > kM12MaxRequest) return 0;
    out[n] = '\n';
    out[n + 1] = 0;
    return n + 1;
}

// Requests waiting for their reply. Ids count up from 1 and wrap before 2^31.
struct M12Pending {
    bool used = false;
    int32_t id = 0;
    M12Op op = M12Op::ping;
    int64_t deadline_us = 0;
    uint32_t tag = 0;                     // caller data (e.g. the mode revision a cfg applies)
    int32_t face = -1;
};
class M12Requests {
public:
    static constexpr size_t kSlots = 8;
    // Reserves a slot and returns its id, or -1 when full.
    int32_t add(M12Op op, int64_t now, uint32_t tag = 0, int32_t face = -1)
    {
        for (auto& p : slots_) {
            if (p.used) continue;
            p = {true, next_, op, now + m12_reply_timeout_us(op), tag, face};
            next_ = next_ >= 2147483646 ? 1 : next_ + 1;
            return p.id;
        }
        return -1;
    }
    bool take(int32_t id, M12Pending& out)
    {
        for (auto& p : slots_) if (p.used && p.id == id) { out = p; p.used = false; return true; }
        return false;
    }
    // One overdue request per call, removed from the table.
    bool expire(int64_t now, M12Pending& out)
    {
        for (auto& p : slots_) if (p.used && now >= p.deadline_us) { out = p; p.used = false; return true; }
        return false;
    }
    void cancel(int32_t id) { M12Pending ignored; take(id, ignored); }
    size_t outstanding(M12Op op) const
    {
        size_t n = 0;
        for (const auto& p : slots_) n += p.used && p.op == op;
        return n;
    }
    size_t outstanding() const
    {
        size_t n = 0;
        for (const auto& p : slots_) n += p.used;
        return n;
    }
    void clear() { for (auto& p : slots_) p.used = false; }

private:
    M12Pending slots_[kSlots];
    int32_t next_ = 1;
};

// Automatic identification (m12d §4.3). A face id seen without a name is asked {"do":"who","face":id} as soon as it
// is seen (first ask, largest face first); afterwards a name rides on every detection line. Each who pauses
// detection ~0.22 s.
// - 2026-11-15 (owner: 没认出就再问; 最多再问 5 次，无间隔询问): a track that is still unknown is asked again as soon as
//   its previous who was answered, at most kReasks more times (kReasks + 1 asks in all), only while the face is at
//   least kReaskMinW wide. No interval, no global gap; first asks of new tracks always go before re-asks; among
//   re-asks the track with the fewest asks goes first (then the larger face).
// - A name only ever comes from a who answer (宁可叫不出名字，也不要叫错): no threshold is lowered, nothing is guessed.
// - "busy" backs off and retries the same id; standby/hot/send wait and retry; every other answer (name, null,
//   noface, timeout, error) counts as one ask; a name closes the track.
// - look() (2026-11-15, crowd tap): every face in view without a name is asked now, largest first, ignoring the
//   re-ask limit (still one in flight, busy backoff kept), until all answered or kLookUs passed. A look ask is the
//   track's first ask when it had none; otherwise it does not use up the track's re-asks.
// One who is in flight at a time.
class M12WhoPolicy {
public:
    static constexpr size_t kTracks = 16;
    static constexpr int64_t kFirstBackoffUs = 500000, kMaxBackoffUs = 4000000, kWaitUs = 5000000;
    static constexpr uint8_t kReasks = 5;
    static constexpr int16_t kReaskMinW = 48;            // of 640: smaller faces are not asked again
    static constexpr int64_t kLookUs = 800000;
    static constexpr int64_t kLookLineUs = 300000;       // look() ignores a line older than this

    // The face id to ask now, or -1.
    int32_t next(const M12Detection& d, int64_t now)
    {
        pick_look_ = false;
        if (inflight_ >= 0 || now < hold_until_) return -1;
        const M12Face* best = nullptr;
        auto take = [&](const M12Face& f) { if (!best || area(f) > area(*best)) best = &f; };
        if (look_active(now)) {
            for (size_t i = 0; i < d.count; ++i) {
                const auto& f = d.faces[i];
                if (f.id >= 0 && !f.name[0] && look_pending(f.id)) take(f);
            }
            if (best) { pick_look_ = true; return best->id; }
        }
        for (size_t i = 0; i < d.count; ++i) {             // first asks
            const auto& f = d.faces[i];
            const Track* t = find(f.id);
            if (f.id >= 0 && !f.name[0] && (!t || (!t->done && t->asks == 0))) take(f);
        }
        if (best) return best->id;
        const Track* best_t = nullptr;
        for (size_t i = 0; i < d.count; ++i) {             // re-asks: fewest asks first, then the larger face
            const auto& f = d.faces[i];
            const Track* t = find(f.id);
            if (f.id < 0 || f.name[0] || !t || t->done || t->asks == 0 || t->asks > kReasks || f.w < kReaskMinW) continue;
            if (!best || t->asks < best_t->asks || (t->asks == best_t->asks && area(f) > area(*best))) { best = &f; best_t = t; }
        }
        return best ? best->id : -1;
    }
    void sent(int32_t face, int64_t)
    {
        inflight_ = face;
        sent_look_ = pick_look_;
        pick_look_ = false;
        if (!find(face)) add(face);
    }
    // err: nullptr on success, else the m12d code or "client_timeout"/"send". named: who answered a name.
    void answered(int32_t face, const char* err, int64_t now, bool named = false)
    {
        const bool look_ask = sent_look_ && face == inflight_;
        if (face == inflight_) { inflight_ = -1; sent_look_ = false; }
        if (err && !std::strcmp(err, "busy")) {
            backoff_ = backoff_ ? std::min(backoff_ * 2, kMaxBackoffUs) : kFirstBackoffUs;
            hold_until_ = now + backoff_;
            ++busy_;
            return;
        }
        if (err && (!std::strcmp(err, "standby") || !std::strcmp(err, "hot") || !std::strcmp(err, "send"))) {
            hold_until_ = now + kWaitUs;
            return;
        }
        backoff_ = 0;
        if (face < 0) return;
        Track* t = find(face);
        if (!t) t = add(face);
        t->seen = ++clock_;
        if (!look_ask || t->asks == 0) ++t->asks;
        else ++look_extra_;
        if (named || t->asks > kReasks) t->done = true;
        if (t->asks > 1 && !look_ask) ++reasks_;
        for (size_t i = 0; i < look_n_; ++i) if (look_[i].id == face) { look_[i].answered = true; look_[i].named = named; }
    }
    // Crowd tap: ask every face of `d` (the line received at line_us) without a name now. A line older than
    // kLookLineUs is not "in view": no targets (the look then only waits for attributes / settles).
    void look(const M12Detection& d, int64_t line_us, int64_t now)
    {
        look_n_ = 0;
        for (size_t i = 0; now - line_us <= kLookLineUs && i < d.count && look_n_ < kM12MaxFaces; ++i) {
            const auto& f = d.faces[i];
            if (f.id >= 0 && !f.name[0]) look_[look_n_++] = {f.id, false, false};
        }
        look_on_ = true;
        look_until_ = now + kLookUs;
    }
    // The look has what it came for: every target answered (a named one also shows its name on the line) or left
    // the view, and (need_attrs) every face in view carries gender / age; or kLookUs passed. True when no look.
    bool look_settled(const M12Detection& d, int64_t now, bool need_attrs) const
    {
        if (!look_on_ || now >= look_until_) return true;
        for (size_t k = 0; k < look_n_; ++k) {
            const M12Face* f = face_in(d, look_[k].id);
            if (!f) continue;                                  // gone: nothing more to learn
            if (!look_[k].answered && !f->name[0]) return false;
            if (look_[k].named && !f->name[0]) return false;   // the name has not reached a detection line yet
        }
        if (need_attrs)
            for (size_t i = 0; i < d.count; ++i) if (d.faces[i].id >= 0 && !d.faces[i].gender) return false;
        return true;
    }
    void look_end() { look_on_ = false; look_n_ = 0; }
    bool look_active(int64_t now) const { return look_on_ && now < look_until_; }
    // In place (review 2026-11-15): no ~1 KB temporary on the M12 task's 8 KB stack.
    void reset()
    {
        for (auto& t : tracks_) t = Track{};
        look_n_ = 0;
        look_on_ = pick_look_ = sent_look_ = false;
        look_until_ = 0; inflight_ = -1; hold_until_ = 0; backoff_ = 0;
        busy_ = reasks_ = look_extra_ = 0; clock_ = 0;
    }
    bool asked(int32_t face) const { const Track* t = find(face); return t && t->asks > 0; }
    uint8_t asks(int32_t face) const { const Track* t = find(face); return t ? t->asks : 0; }
    int32_t inflight() const { return inflight_; }
    uint32_t busy() const { return busy_; }
    uint32_t reasks() const { return reasks_; }
    uint32_t look_extra() const { return look_extra_; }

private:
    struct Track {
        int32_t id = -1;
        uint8_t asks = 0;                 // answered asks (busy / standby / send do not count)
        bool done = false;                // named, or out of re-asks
        uint32_t seen = 0;                // last answer (policy clock): the oldest track is replaced when full
    };
    struct LookTarget { int32_t id; bool answered, named; };
    static int32_t area(const M12Face& f) { return int32_t(f.w) * f.h; }
    static const M12Face* face_in(const M12Detection& d, int32_t id)
    {
        for (size_t i = 0; i < d.count; ++i) if (d.faces[i].id == id) return &d.faces[i];
        return nullptr;
    }
    bool look_pending(int32_t id) const
    {
        for (size_t i = 0; i < look_n_; ++i) if (look_[i].id == id) return !look_[i].answered;
        return false;
    }
    const Track* find(int32_t id) const
    {
        if (id < 0) return nullptr;
        for (const auto& t : tracks_) if (t.id == id) return &t;
        return nullptr;
    }
    Track* find(int32_t id) { return const_cast<Track*>(static_cast<const M12WhoPolicy*>(this)->find(id)); }
    // A free slot, else the track answered longest ago (m12d ids count up; old ids do not come back).
    Track* add(int32_t id)
    {
        Track* slot = nullptr;
        for (auto& t : tracks_) {
            if (t.id < 0) { slot = &t; break; }
            if (!slot || t.seen < slot->seen) slot = &t;
        }
        *slot = Track{};
        slot->id = id;
        slot->seen = ++clock_;
        return slot;
    }
    Track tracks_[kTracks];
    LookTarget look_[kM12MaxFaces] = {};
    size_t look_n_ = 0;
    bool look_on_ = false, pick_look_ = false, sent_look_ = false;
    int64_t look_until_ = 0;
    int32_t inflight_ = -1;
    int64_t hold_until_ = 0, backoff_ = 0;
    uint32_t busy_ = 0, reasks_ = 0, look_extra_ = 0, clock_ = 0;
};

// What face tracking follows: the same face id as last time while it is in view, else
// the largest face; with no face (beyond ~1.5 m) a person box, its top part standing in
// for the head.
struct M12Target {
    bool valid = false, person = false;
    int32_t id = -1;
    int16_t x = 0, y = 0, w = 0, h = 0;
};
inline M12Target m12_track_target(const M12Detection& d, const M12Target& previous)
{
    M12Target t;
    const M12Face* face = nullptr;
    for (size_t i = 0; i < d.count; ++i) {
        const auto& f = d.faces[i];
        if (previous.valid && !previous.person && f.id >= 0 && f.id == previous.id) { face = &f; break; }
        if (!face || int(f.w) * f.h > int(face->w) * face->h) face = &f;
    }
    if (face) return {true, false, face->id, face->x, face->y, face->w, face->h};
    const M12Person* body = nullptr;
    for (size_t i = 0; i < d.persons; ++i) {
        const auto& p = d.person[i];
        if (previous.valid && previous.person && p.id >= 0 && p.id == previous.id) { body = &p; break; }
        if (!body || int(p.w) * p.h > int(body->w) * body->h) body = &p;
    }
    if (!body) return t;
    const int16_t head = static_cast<int16_t>(std::min<int>(body->h, std::max<int>(body->w / 2, 1)));
    return {true, true, body->id, body->x, body->y, body->w, head};
}

// ---------- vision change events (2026-11-10) ----------
// The robot reports what changed in view, not every detection line: raw material for the
// server's mind. Faces and persons are tracked by m12d's ids; an id unseen for kLostUs is lost.
// Everything learnt about a face is reported as soon as it is known (user, 2026-11-10): name
// (face_named) and gender / age group (face_attrs, ~0.6 s after the face appears), not at loss.
enum class M12VisionKind : uint8_t {
    face_found, face_named, face_unknown, face_lost, person_found, person_lost,
    vision_on, vision_off, m12_online, m12_offline, face_attrs,
    m12_ev                                // an m12d 0.2.0 event line, passed on as it came (M12VisionEvent::ev)
};
inline const char* m12_vision_kind_name(M12VisionKind k)
{
    static const char* const names[] = {"face_found", "face_named", "face_unknown", "face_lost", "person_found",
                                        "person_lost", "vision_on", "vision_off", "m12_online", "m12_offline", "face_attrs",
                                        "m12_ev"};
    return names[static_cast<size_t>(k)];
}
struct M12VisionEvent {
    M12VisionKind kind = M12VisionKind::face_found;
    int64_t t_us = 0;                     // robot clock (esp_timer)
    uint32_t f = 0;                       // m12d frame number (0 for status events)
    int32_t face = -1, person = -1;       // ids (person events: `face` = paired face)
    int16_t x = 0, y = 0, w = 0, h = 0;   // 640x480
    float prob = 0, sim = -1;             // sim: who similarity (face_unknown)
    char gender = 0;
    int16_t age = -1;
    M12AgeGroup age_group = M12AgeGroup::none;
    char name[kM12NameBytes + 1] = {};
    char reason[12] = {};                 // lost: "timeout" or "vision_off"
    // kind m12_ev: the event itself rides beside this one (m12_peek_vision_events' ev_out), so this struct, built on
    // task stacks by the change tracker, stays small.
};
class M12ChangeTracker {
public:
    static constexpr int64_t kLostUs = 1500000;
    static constexpr size_t kFaces = 8, kPersons = 5;
    template <typename Emit> void detection(const M12Detection& d, int64_t now, Emit emit)
    {
        for (size_t i = 0; i < d.count; ++i) {
            const auto& f = d.faces[i];
            if (f.id < 0) continue;
            Face* t = find_face(f.id);
            if (!t) {
                t = slot_face(now, emit);
                t->used = true; t->face = f; t->last_us = now; t->named[0] = 0; t->gender = 0; t->age_group = M12AgeGroup::none;
                emit(face_event(M12VisionKind::face_found, f, d.f, now));
            }
            t->face = f; t->last_us = now; t->f = d.f;
            if (f.name[0] && std::strcmp(f.name, t->named)) {
                std::snprintf(t->named, sizeof(t->named), "%s", f.name);
                emit(face_event(M12VisionKind::face_named, f, d.f, now));
            }
            if (f.gender && (f.gender != t->gender || f.age_group != t->age_group)) {
                t->gender = f.gender; t->age_group = f.age_group;
                emit(face_event(M12VisionKind::face_attrs, f, d.f, now));
            }
        }
        for (size_t i = 0; i < d.persons; ++i) {
            const auto& p = d.person[i];
            if (p.id < 0) continue;
            Person* t = find_person(p.id);
            if (!t) {
                t = slot_person(now, emit);
                t->used = true;
                emit(person_event(M12VisionKind::person_found, p, d.f, now));
            }
            t->person = p; t->last_us = now; t->f = d.f;
        }
    }
    template <typename Emit> void tick(int64_t now, Emit emit)
    {
        for (auto& t : faces_) if (t.used && now - t.last_us >= kLostUs) lose_face(t, now, "timeout", emit);
        for (auto& t : persons_) if (t.used && now - t.last_us >= kLostUs) lose_person(t, now, "timeout", emit);
    }
    // Detection stopped (standby / M12 offline): everything in view is lost now.
    template <typename Emit> void clear(int64_t now, const char* reason, Emit emit)
    {
        for (auto& t : faces_) if (t.used) lose_face(t, now, reason, emit);
        for (auto& t : persons_) if (t.used) lose_person(t, now, reason, emit);
    }
    // Crowd tap (2026-11-15): the attributes of every face seen within kFreshUs go up again now (face_attrs with
    // the latest box), so the greeting reads fresh boxes and attributes. Faces without attributes yet report them
    // the moment they arrive, as always.
    static constexpr int64_t kFreshUs = 500000;
    template <typename Emit> size_t refresh(int64_t now, Emit emit)
    {
        size_t n = 0;
        for (auto& t : faces_) {
            if (!t.used || !t.gender || now - t.last_us > kFreshUs) continue;
            auto e = face_event(M12VisionKind::face_attrs, t.face, t.f, now);
            e.gender = t.gender; e.age_group = t.age_group;
            emit(e);
            ++n;
        }
        return n;
    }
    size_t faces_in_view() const { size_t n = 0; for (const auto& t : faces_) n += t.used; return n; }
    size_t persons_in_view() const { size_t n = 0; for (const auto& t : persons_) n += t.used; return n; }

private:
    struct Face {
        bool used = false; M12Face face; int64_t last_us = 0; uint32_t f = 0; char named[kM12NameBytes + 1] = {};
        char gender = 0; M12AgeGroup age_group = M12AgeGroup::none;     // last attributes reported
    };
    struct Person { bool used = false; M12Person person; int64_t last_us = 0; uint32_t f = 0; };
    Face faces_[kFaces];
    Person persons_[kPersons];
    static M12VisionEvent face_event(M12VisionKind k, const M12Face& f, uint32_t frame, int64_t now)
    {
        M12VisionEvent e; e.kind = k; e.t_us = now; e.f = frame; e.face = f.id;
        e.x = f.x; e.y = f.y; e.w = f.w; e.h = f.h; e.prob = f.prob; e.gender = f.gender; e.age = f.age; e.age_group = f.age_group;
        std::snprintf(e.name, sizeof(e.name), "%s", f.name);
        return e;
    }
    static M12VisionEvent person_event(M12VisionKind k, const M12Person& p, uint32_t frame, int64_t now)
    {
        M12VisionEvent e; e.kind = k; e.t_us = now; e.f = frame; e.person = p.id; e.face = p.face;
        e.x = p.x; e.y = p.y; e.w = p.w; e.h = p.h; e.prob = p.prob;
        return e;
    }
    template <typename Emit> void lose_face(Face& t, int64_t now, const char* reason, Emit emit)
    {
        auto e = face_event(M12VisionKind::face_lost, t.face, t.f, now);
        if (!e.name[0]) std::snprintf(e.name, sizeof(e.name), "%s", t.named);   // last name it carried
        if (!e.gender) { e.gender = t.gender; e.age_group = t.age_group; }
        std::snprintf(e.reason, sizeof(e.reason), "%s", reason);
        t.used = false;
        emit(e);
    }
    template <typename Emit> void lose_person(Person& t, int64_t now, const char* reason, Emit emit)
    {
        auto e = person_event(M12VisionKind::person_lost, t.person, t.f, now);
        std::snprintf(e.reason, sizeof(e.reason), "%s", reason);
        t.used = false;
        emit(e);
    }
    Face* find_face(int32_t id) { for (auto& t : faces_) if (t.used && t.face.id == id) return &t; return nullptr; }
    Person* find_person(int32_t id) { for (auto& t : persons_) if (t.used && t.person.id == id) return &t; return nullptr; }
    // A free slot, or the least recently seen track (lost first) when the table is full.
    template <typename Emit> Face* slot_face(int64_t now, Emit emit)
    {
        Face* oldest = nullptr;
        for (auto& t : faces_) { if (!t.used) return &t; if (!oldest || t.last_us < oldest->last_us) oldest = &t; }
        lose_face(*oldest, now, "evicted", emit);
        return oldest;
    }
    template <typename Emit> Person* slot_person(int64_t now, Emit emit)
    {
        Person* oldest = nullptr;
        for (auto& t : persons_) { if (!t.used) return &t; if (!oldest || t.last_us < oldest->last_us) oldest = &t; }
        lose_person(*oldest, now, "evicted", emit);
        return oldest;
    }
};

// Explicit vision modes. m12d always detects while det is on; recognition is a request.
enum class M12Function : uint8_t { standby = 0, detect = 1, none = 3 };
inline uint32_t next_m12_request(uint32_t previous, M12Function mode)
{
    return ((previous & ~uint32_t{3}) + 4) | static_cast<uint32_t>(mode);
}
inline M12Function m12_requested_function(uint32_t request)
{
    return (request & 3) == 1 ? M12Function::detect : (request & 3) == 0 ? M12Function::standby : M12Function::none;
}
inline bool m12_requested_view(uint32_t request) { return (request & 3) == 1; }

// M12 box (640x480) on the 320x240 screen, mirrored like a selfie camera so the box
// moves the same way as the person in front of the robot.
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
