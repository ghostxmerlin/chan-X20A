/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace collector {
// M12 (UnitV2) running m12d, the user's own resident program (2026-11-10; replaces the
// factory firmware, whose {"function":...} switching, audio_fft standby and Face
// Recognition training no longer exist). Grove UART, 115200 8N1, one JSON object per
// line. Protocol: m12d docs/protocol.md; migration notes: docs/m12d-migration.md.
//   push   (no "id"):  detection line {"f":..,"num":..,"face":[..],"person":[..]}
//                      heartbeat {"hb":{"t":..,"fps":..,"tier":..,"up":..,...}} every 2 s
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
enum class M12Kind : uint8_t { invalid, detection, heartbeat, reply, other };
struct M12Message {
    M12Kind kind = M12Kind::invalid;
    M12Detection det;
    M12Heartbeat hb;
    M12Reply reply;
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

// Automatic identification (m12d §4.3): a face id seen without a name gets at most one
// {"do":"who","face":id}; afterwards the name rides on every detection line, so who is
// never polled (each run pauses detection ~0.22 s). "busy" backs off and retries the
// same id; standby/hot wait and retry; every other answer (name, null, noface, timeout,
// error) closes that id. One who is in flight at a time.
class M12WhoPolicy {
public:
    static constexpr size_t kRemembered = 32;
    static constexpr int64_t kFirstBackoffUs = 500000, kMaxBackoffUs = 4000000, kWaitUs = 5000000;
    int32_t next(const M12Detection& d, int64_t now) const
    {
        if (inflight_ >= 0 || now < hold_until_) return -1;
        for (size_t i = 0; i < d.count; ++i) {
            const auto& f = d.faces[i];
            if (f.id >= 0 && !f.name[0] && !asked(f.id)) return f.id;
        }
        return -1;
    }
    void sent(int32_t face, int64_t) { inflight_ = face; }
    // err: nullptr on success, else the m12d code or "client_timeout"/"send".
    void answered(int32_t face, const char* err, int64_t now)
    {
        if (face == inflight_) inflight_ = -1;
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
        remember(face);
    }
    void reset() { *this = M12WhoPolicy{}; }
    bool asked(int32_t face) const
    {
        for (size_t i = 0; i < count_; ++i) if (ids_[i] == face) return true;
        return false;
    }
    int32_t inflight() const { return inflight_; }
    uint32_t busy() const { return busy_; }

private:
    void remember(int32_t face)
    {
        if (face < 0 || asked(face)) return;
        ids_[head_] = face;
        head_ = (head_ + 1) % kRemembered;
        count_ = std::min(count_ + 1, kRemembered);
    }
    int32_t ids_[kRemembered] = {};
    size_t head_ = 0, count_ = 0;
    int32_t inflight_ = -1;
    int64_t hold_until_ = 0, backoff_ = 0;
    uint32_t busy_ = 0;
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
