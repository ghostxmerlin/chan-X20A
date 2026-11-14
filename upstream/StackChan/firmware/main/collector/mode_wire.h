/* SPDX-License-Identifier: MIT */
#pragma once
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string_view>
namespace collector::modes {
// hello modes step 1 (collector-0.4.92): the /mouth mode wire, robot side. Pure logic, no FreeRTOS or LVGL,
// so it is tested on the host (tests/mode_wire_test.cpp).
//
// mind is the only place that changes the mode. The ear pushes mind's snapshot down /mouth as
// {"type":"mode", ...} (at least every 60 s, possibly twice in a row after the handshake) and answers each
// robot input with exactly one {"type":"mode_result","seq":<robot input seq>,"code","toast"}. The robot applies
// the snapshot, shows the toast, and sends {"type":"mode_input",...} and {"type":"mode_applied","seq":S}.
//  - A snapshot is applied when its seq != the applied seq (mind's seq can go backwards after a DB failure
//    and a restart; the ear never sends an older fetch after a newer one on one link), and the first snapshot
//    after each handshake is always applied. An equal seq is a no-op, not an error. A malformed snapshot never
//    changes state. Ack only Applied with mode_applied.
//  - mode_result and the new snapshot may arrive in either order: each is handled on its own.
//  - Every input carries the robot's seq (unique per boot; mind dedups on device + boot + seq) and age_ms since
//    it was queued (mind drops device inputs older than 3000 ms as stale). Do not resend on busy.
// No heap: lines and queue slots are fixed buffers; the caller places the queue slots (PSRAM) and holds its
// own lock. Nothing here writes NVS (the /mouth task runs on a PSRAM stack, fw/psram_task.h).

enum class Eye : uint8_t { LaughingMan, V05, V14, V18, PageSubtitle, PageSettings, PageIdle };
enum class Pool : uint8_t { Default, Lively };              // look.pool: null = the eye's own natural loop
enum class TouchAct : uint8_t { Server, Badge, None };      // server = send to mind; badge = IDLE's local badge page

struct Snapshot {
    uint32_t seq;
    char mode[12];      // mode id (talk, chat, settings, event, subtitle, crowd, companion, idle); not interpreted here
    char dir[8];        // "" (null), or the subtitle direction zh_en / en_zh
    bool hosted, billing;   // hosted = host is not null ("event": event-driven hosts this mode)
    Eye eye;
    Pool pool;
    char label[12];     // printable ASCII, 1..10 chars (e.g. "SUB ZH>EN")
    uint32_t color;     // 0xRRGGBB
    TouchAct tap, dbl;
};

enum class Parse { Applied, Same, Bad };

namespace detail {
// A wire word: [a-z0-9_], 1..cap-1 chars. Words never need JSON escaping.
inline bool word(const char* s, size_t cap)
{
    if (!s) return false;
    size_t n = 0;
    for (; s[n]; ++n) {
        const char c = s[n];
        if (n + 1 >= cap || !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_')) return false;
    }
    return n > 0;
}
inline bool copy_word(ArduinoJson::JsonVariantConst v, char* out, size_t cap)
{
    if (!v.is<const char*>() || !word(v.as<const char*>(), cap)) return false;
    std::strcpy(out, v.as<const char*>());
    return true;
}
// Printable ASCII (0x20..0x7E), lo..hi chars, into out (cap > hi).
inline bool copy_ascii(ArduinoJson::JsonVariantConst v, char* out, size_t lo, size_t hi)
{
    if (!v.is<const char*>()) return false;
    const char* s = v.as<const char*>();
    const size_t n = std::strlen(s);
    if (n < lo || n > hi) return false;
    for (size_t i = 0; i < n; ++i) if (s[i] < 0x20 || s[i] > 0x7E) return false;
    std::memcpy(out, s, n + 1);
    return true;
}
template <typename E, size_t N>
bool pick(ArduinoJson::JsonVariantConst v, const char* const (&names)[N], E& out)
{
    if (!v.is<const char*>()) return false;
    for (size_t i = 0; i < N; ++i) if (!std::strcmp(v.as<const char*>(), names[i])) { out = E(i); return true; }
    return false;
}
inline bool hex_color(ArduinoJson::JsonVariantConst v, uint32_t& out)
{
    if (!v.is<const char*>()) return false;
    const char* s = v.as<const char*>();
    if (std::strlen(s) != 6) return false;
    uint32_t c = 0;
    for (int i = 0; i < 6; ++i) {
        const char h = s[i];
        const int d = h >= '0' && h <= '9' ? h - '0' : h >= 'A' && h <= 'F' ? h - 'A' + 10 : h >= 'a' && h <= 'f' ? h - 'a' + 10 : -1;
        if (d < 0) return false;
        c = c << 4 | uint32_t(d);
    }
    out = c;
    return true;
}
}

// Validates the whole snapshot first: a missing or mistyped field, an eye/pool/touch word outside the
// vocabulary, a label that is not printable ASCII 1..10, or a color that is not RRGGBB -> Bad, cur untouched.
// Extra fields (type, return_to, since, by, ...) are ignored. Then: first_after_hello or seq != cur.seq ->
// Applied (cur replaced); else Same (cur untouched).
inline Parse parse_snapshot(ArduinoJson::JsonObjectConst o, Snapshot& cur, bool first_after_hello)
{
    using namespace ArduinoJson;
    static constexpr const char* kEyes[] = {"laughing_man", "v05", "v14", "v18", "page_subtitle", "page_settings", "page_idle"};
    static constexpr const char* kTouch[] = {"server", "badge", "none"};
    if (o.isNull()) return Parse::Bad;
    Snapshot s{};
    if (!o["seq"].is<uint32_t>()) return Parse::Bad;
    s.seq = o["seq"].as<uint32_t>();
    if (!detail::copy_word(o["mode"], s.mode, sizeof s.mode)) return Parse::Bad;
    if (!o["dir"].isNull() && !detail::copy_word(o["dir"], s.dir, sizeof s.dir)) return Parse::Bad;
    char host[12] = {};
    if (!o["host"].isNull() && !detail::copy_word(o["host"], host, sizeof host)) return Parse::Bad;
    s.hosted = host[0] != 0;
    if (!o["billing"].is<bool>()) return Parse::Bad;
    s.billing = o["billing"].as<bool>();
    const JsonVariantConst look = o["look"], touch = o["touch"];
    if (!look.is<JsonObjectConst>() || !touch.is<JsonObjectConst>()) return Parse::Bad;
    if (!detail::pick(look["eye"], kEyes, s.eye)) return Parse::Bad;
    if (look["pool"].isNull()) s.pool = Pool::Default;
    else if (look["pool"].is<const char*>() && !std::strcmp(look["pool"].as<const char*>(), "lively")) s.pool = Pool::Lively;
    else return Parse::Bad;
    if (!detail::copy_ascii(look["label"], s.label, 1, 10)) return Parse::Bad;
    if (!detail::hex_color(look["color"], s.color)) return Parse::Bad;
    if (!detail::pick(touch["tap"], kTouch, s.tap) || !detail::pick(touch["double"], kTouch, s.dbl)) return Parse::Bad;
    if (!first_after_hello && s.seq == cur.seq) return Parse::Same;
    cur = s;
    return Parse::Applied;
}

// The applied snapshot plus the "first after handshake" flag, per robot (one /mouth link at a time).
// handshake() on every /mouth (re)connect; offer() for every {"type":"mode"} message. The flag stays set until
// a snapshot is applied, so a bad first snapshot does not use it up.
class SnapshotGate {
public:
    void handshake() { fresh_ = true; }
    Parse offer(ArduinoJson::JsonObjectConst o)
    {
        const Parse p = parse_snapshot(o, cur_, fresh_ || !have_);
        if (p == Parse::Applied) { fresh_ = false; have_ = true; }
        else if (p == Parse::Bad) ++bad_;
        return p;
    }
    const Snapshot& current() const { return cur_; }
    bool have() const { return have_; }       // false until the first snapshot since boot
    uint32_t bad() const { return bad_; }
private:
    Snapshot cur_{};
    bool fresh_ = false, have_ = false;
    uint32_t bad_ = 0;
};

// {"type":"mode_result","seq":N,"code":"ok","toast":"SAYING"}: seq = the robot's own input seq; code a wire
// word (unknown codes are kept, not refused); toast printable ASCII <= 32, may be empty or missing.
struct Result {
    uint32_t seq;
    char code[20];
    char toast[33];
};
inline bool parse_result(ArduinoJson::JsonObjectConst o, Result& out)
{
    if (o.isNull() || !o["seq"].is<uint32_t>()) return false;
    Result r{};
    r.seq = o["seq"].as<uint32_t>();
    if (!detail::copy_word(o["code"], r.code, sizeof r.code)) return false;
    if (!o["toast"].isNull() && !detail::copy_ascii(o["toast"], r.toast, 0, 32)) return false;
    out = r;
    return true;
}

// One outgoing text message in a fixed buffer (the longest mode_input is about 160 B).
constexpr size_t kLineMax = 192;
struct Line {
    char text[kLineMax] = {};
    size_t n = 0;
    const char* c_str() const { return text; }
    size_t size() const { return n; }
    std::string_view view() const { return {text, n}; }
};
namespace detail {
inline bool refuse(Line& l) { l.text[0] = 0; l.n = 0; return false; }
// Appends; false (and an empty line) when it does not fit. No printf: words are checked, numbers are decimal.
inline bool put(Line& l, const char* s)
{
    const size_t n = std::strlen(s);
    if (l.n + n >= sizeof l.text) return refuse(l);
    std::memcpy(l.text + l.n, s, n + 1);
    l.n += n;
    return true;
}
inline bool put(Line& l, uint32_t v)
{
    char b[11], *p = b + sizeof b;
    *--p = 0;
    do { *--p = char('0' + v % 10); v /= 10; } while (v);
    return put(l, p);
}
inline bool input_head(Line& l, uint32_t seq, uint32_t age_ms, const char* source, const char* input)
{
    l.n = 0; l.text[0] = 0;
    return put(l, "{\"type\":\"mode_input\",\"seq\":") && put(l, seq) && put(l, ",\"age_ms\":") && put(l, age_ms) &&
           put(l, ",\"source\":\"") && put(l, source) && put(l, "\",\"input\":\"") && put(l, input) && put(l, "\",\"value\":");
}
}
// source: asr | touch | select | quick ...; input: forward, play, digit, tap, double_tap, ... (wire words).
// value < 0 = null, else the integer (digit 0..9). False (and an empty line) when a word is not a wire word.
inline bool mode_input(Line& l, uint32_t seq, uint32_t age_ms, const char* source, const char* input, int value)
{
    if (!detail::word(source, 8) || !detail::word(input, 12)) return detail::refuse(l);
    return detail::input_head(l, seq, age_ms, source, input) &&
           (value < 0 ? detail::put(l, "null") : detail::put(l, uint32_t(value))) && detail::put(l, "}");
}
// input "select", value {"mode":m} or {"mode":m,"dir":d} (dir null or "" = left out).
inline bool mode_input_select(Line& l, uint32_t seq, uint32_t age_ms, const char* source, const char* mode, const char* dir)
{
    const bool has_dir = dir && *dir;
    if (!detail::word(source, 8) || !detail::word(mode, 12) || (has_dir && !detail::word(dir, 8))) return detail::refuse(l);
    return detail::input_head(l, seq, age_ms, source, "select") && detail::put(l, "{\"mode\":\"") && detail::put(l, mode) &&
           (!has_dir || (detail::put(l, "\",\"dir\":\"") && detail::put(l, dir))) && detail::put(l, "\"}}");
}
inline bool mode_applied(Line& l, uint32_t seq)
{
    l.n = 0; l.text[0] = 0;
    return detail::put(l, "{\"type\":\"mode_applied\",\"seq\":") && detail::put(l, seq) && detail::put(l, "}");
}

// A queued input: built into a Line only when it is sent, so age_ms covers the time spent in the queue.
struct Input {
    uint32_t seq;
    int64_t at_us;
    bool select;
    int value;          // < 0 = null
    char source[8];
    char input[12];
    char mode[12];      // select only
    char dir[8];        // select only, "" = none
};

// Bounded input queue (kQueueCap = 8, like the ear's per-link queue). Full -> the oldest is dropped and counted.
// Storage comes from the caller (PSRAM). The queue numbers the inputs 1, 2, 3 ... for the boot (clear() keeps
// counting), and a push with a non-wire word is refused (returns 0, uses no seq). Not thread safe: the caller
// holds its lock. clear() on every /mouth disconnect.
constexpr size_t kQueueCap = 8;
class InputQueue {
public:
    InputQueue(Input* slots, size_t cap) : slots_(slots), cap_(slots ? std::min(cap, kQueueCap) : 0) {}
    uint32_t push(int64_t now_us, const char* source, const char* input, int value = -1)
    {
        if (!detail::word(source, sizeof(Input::source)) || !detail::word(input, sizeof(Input::input))) return 0;
        Input in{};
        in.at_us = now_us; in.value = value < 0 ? -1 : value;
        std::strcpy(in.source, source); std::strcpy(in.input, input);
        return add(in);
    }
    uint32_t push_select(int64_t now_us, const char* source, const char* mode, const char* dir = nullptr)
    {
        const bool has_dir = dir && *dir;
        if (!detail::word(source, sizeof(Input::source)) || !detail::word(mode, sizeof(Input::mode)) ||
            (has_dir && !detail::word(dir, sizeof(Input::dir)))) return 0;
        Input in{};
        in.at_us = now_us; in.select = true; in.value = -1;
        std::strcpy(in.source, source); std::strcpy(in.input, "select"); std::strcpy(in.mode, mode);
        if (has_dir) std::strcpy(in.dir, dir);
        return add(in);
    }
    const Input* front() const { return n_ ? &slots_[head_] : nullptr; }
    // The front input as a mode_input line, age_ms = now - queued (never negative, capped at uint32).
    bool front_line(int64_t now_us, Line& out) const
    {
        const Input* in = front();
        if (!in) return detail::refuse(out);
        const int64_t d = std::max<int64_t>(0, now_us - in->at_us) / 1000;
        const uint32_t age = d > int64_t(UINT32_MAX) ? UINT32_MAX : uint32_t(d);
        return in->select ? mode_input_select(out, in->seq, age, in->source, in->mode, in->dir)
                          : mode_input(out, in->seq, age, in->source, in->input, in->value);
    }
    void pop() { if (n_) { head_ = (head_ + 1) % cap_; --n_; } }
    void clear() { head_ = 0; n_ = 0; }
    size_t size() const { return n_; }
    bool empty() const { return n_ == 0; }
    size_t capacity() const { return cap_; }
    uint32_t dropped() const { return dropped_; }
    uint32_t last_seq() const { return seq_; }
private:
    uint32_t add(Input& in)
    {
        if (!cap_) { ++dropped_; return 0; }
        if (++seq_ == 0) seq_ = 1;                 // 0 is "refused"; wraps after 4e9 inputs
        in.seq = seq_;
        if (n_ == cap_) { pop(); ++dropped_; }
        slots_[(head_ + n_) % cap_] = in;
        ++n_;
        return in.seq;
    }
    Input* slots_;
    size_t cap_, head_ = 0, n_ = 0;
    uint32_t seq_ = 0, dropped_ = 0;
};
}
