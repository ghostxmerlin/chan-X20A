/* SPDX-License-Identifier: MIT */
#pragma once
// 视觉爆发 / vision burst (collector-0.4.99, owner 2026-11-15; contract docs/superpowers/specs/2026-11-15-vision-burst-snap.md).
// The ear asks on /mouth {"type":"snap","id":<uint32>} (<= 200 B); the robot sends the next fresh body-camera frame
// on /vision ("SVF1" header gets "snap":<id>) outside the change rule and the 1 s gap, then answers on /mouth
// {"type":"snap_result","id",..}. At most one snap in flight, 2 s deadline. Pure logic (no FreeRTOS, no socket),
// driven by tests/vision_snap_test.cpp; vision_uplink.cpp owns one Slot in PSRAM behind its own mutex.
#include <ArduinoJson.hpp>
#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace collector::snap {
constexpr size_t kTextMax = 200;                 // a snap request longer than this is a bad message (no answer)
constexpr int64_t kDeadlineUs = 2000000;         // accepted → result (ok or timeout) within 2 s, robot clock
constexpr size_t kResultCap = 4;                 // results waiting for /mouth; full: the oldest goes
constexpr size_t kLineMax = 128;                 // the longest snap_result line is about 90 B

enum class Why : uint8_t { None, CameraOff, NoUplink, Busy, Timeout };
inline const char* why_name(Why w)
{
    switch (w) {
    case Why::CameraOff: return "camera_off";
    case Why::NoUplink: return "no_uplink";
    case Why::Busy: return "busy";
    case Why::Timeout: return "timeout";
    default: return "";
    }
}

// A /mouth text already read as type "snap" (mouth_modes.h read_text), `n` its length. false: too long, or "id" is
// not an integer 0..4294967295 (bool, float, string, negative: bad). Unknown extra fields are ignored.
inline bool parse(ArduinoJson::JsonObjectConst o, size_t n, uint32_t& id)
{
    if (n > kTextMax) return false;
    const auto v = o["id"];
    if (v.is<bool>() || !v.is<uint32_t>()) return false;
    id = v.as<uint32_t>();
    return true;
}

struct Result {
    uint32_t id = 0;
    bool ok = false;
    Why why = Why::None;           // ok == false only
    uint32_t seq = 0, bytes = 0;   // ok only: the camera frame seq (= the SVF1 header "seq") and its JPEG bytes
    uint32_t ms = 0;               // ok only: request accepted → frame handed to the socket, robot clock
};

// {"type":"snap_result","id":7,"ok":true,"seq":1234,"bytes":18000,"ms":180}
// {"type":"snap_result","id":7,"ok":false,"why":"busy"}. Returns the length; 0 when it does not fit `cap`.
inline size_t result_line(const Result& r, char* out, size_t cap)
{
    const int n = r.ok
        ? std::snprintf(out, cap, "{\"type\":\"snap_result\",\"id\":%lu,\"ok\":true,\"seq\":%lu,\"bytes\":%lu,\"ms\":%lu}",
                        (unsigned long)r.id, (unsigned long)r.seq, (unsigned long)r.bytes, (unsigned long)r.ms)
        : std::snprintf(out, cap, "{\"type\":\"snap_result\",\"id\":%lu,\"ok\":false,\"why\":\"%s\"}",
                        (unsigned long)r.id, why_name(r.why));
    return n > 0 && static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : 0;
}

// The accepted snap as the vision task sees it; `gen` tells a late completion from the current snap.
struct Ticket {
    uint32_t id = 0, gen = 0;
    int64_t since_us = 0;          // accepted at; the frame sent must be captured at or after this
};
// A frame captured at `frame_us` answers a snap accepted at `since_us` ("now": never an older frame).
inline bool fresh(int64_t frame_us, int64_t since_us) { return frame_us >= since_us; }

struct Counters { uint32_t accepted = 0, ok = 0, refused = 0, timeouts = 0, failed = 0, late = 0, dropped = 0; };

// Single flight: busy from request() until its result is queued (sent / failed / the 2 s deadline). Every
// request gets exactly one result (ok or not) unless reset() drops it (the /mouth link went away).
class Slot {
public:
    // /mouth task. Why::None = accepted (the result follows within kDeadlineUs); otherwise its failure result is
    // already queued. Order: busy, camera_off, no_uplink.
    Why request(uint32_t id, int64_t now_us, bool camera_on, bool uplink_up)
    {
        expire(now_us);
        const Why why = active_ ? Why::Busy : !camera_on ? Why::CameraOff : !uplink_up ? Why::NoUplink : Why::None;
        if (why != Why::None) {
            Result r; r.id = id; r.why = why;
            push(r); ++c_.refused;
            return why;
        }
        active_ = true; id_ = id; since_ = now_us; ++gen_; ++c_.accepted;
        return Why::None;
    }
    // Vision task: the snap waiting for its frame (inside its deadline). false: none.
    bool waiting(int64_t now_us, Ticket& t)
    {
        expire(now_us);
        if (!active_) return false;
        t.id = id_; t.gen = gen_; t.since_us = since_;
        return true;
    }
    // Vision task: the frame for `t` went to the socket. false: `t` is no longer the snap (timed out, reset):
    // nothing is queued (counted late).
    bool sent(const Ticket& t, int64_t now_us, uint32_t seq, uint32_t bytes)
    {
        if (!current(t)) { ++c_.late; return false; }
        Result r; r.id = t.id; r.ok = true; r.seq = seq; r.bytes = bytes;
        r.ms = static_cast<uint32_t>((now_us - t.since_us) / 1000);
        push(r); active_ = false; ++c_.ok;
        return true;
    }
    // Vision task: the snap cannot be sent (the socket failed: no_uplink).
    bool failed(const Ticket& t, Why why)
    {
        if (!current(t)) { ++c_.late; return false; }
        Result r; r.id = t.id; r.why = why;
        push(r); active_ = false; ++c_.failed;
        return true;
    }
    // /mouth task, every pass: runs the deadline, then hands out the oldest result.
    bool take(int64_t now_us, Result& r)
    {
        expire(now_us);
        if (!n_) return false;
        r = results_[0];
        for (size_t i = 1; i < n_; ++i) results_[i - 1] = results_[i];
        --n_;
        return true;
    }
    // The /mouth link dropped: the snap in flight and unsent results go (a frame already being sent still goes
    // to /vision with its "snap" tag; its completion is late).
    void reset() { if (active_) ++gen_; active_ = false; n_ = 0; }
    bool busy() const { return active_; }
    size_t queued() const { return n_; }
    const Counters& counters() const { return c_; }

private:
    bool current(const Ticket& t) const { return active_ && t.gen == gen_ && t.id == id_; }
    void expire(int64_t now_us)
    {
        if (!active_ || now_us - since_ < kDeadlineUs) return;
        Result r; r.id = id_; r.why = Why::Timeout;
        push(r); active_ = false; ++gen_; ++c_.timeouts;
    }
    void push(const Result& r)
    {
        if (n_ == kResultCap) {
            for (size_t i = 1; i < n_; ++i) results_[i - 1] = results_[i];
            --n_; ++c_.dropped;
        }
        results_[n_++] = r;
    }
    bool active_ = false;
    uint32_t id_ = 0, gen_ = 0;
    int64_t since_ = 0;
    Result results_[kResultCap];
    size_t n_ = 0;
    Counters c_;
};
}
