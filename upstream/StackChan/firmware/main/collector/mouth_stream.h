/* SPDX-License-Identifier: MIT */
#pragma once
// /mouth (collector-0.4.77): the server streams TTS as Opus packets, the robot plays them as they
// arrive. Protocol: voice/docs/tts-streaming.zh-CN.md §4. Pure logic shared with host tests:
// packet header, the packet ring (jitter buffer, storage in PSRAM on the robot), the per-stream
// state and the player's decision for each 20/60 ms step.
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <string>

namespace collector::mouth {
constexpr int kFrameMs = 60;
constexpr int kPrebufferMs = 180, kRebufferMs = 120;
constexpr int64_t kHoldUs = 1500000;          // speaker stays on for the next sentence
constexpr int64_t kProgressUs = 250000;
constexpr int64_t kGateWaitUs = 8000000;      // an SD clip still playing: wait at most this long
constexpr size_t kMaxMessage = 1600;          // largest WebSocket message taken on /mouth

struct Header { uint32_t stream = 0, seq = 0; uint16_t frame_ms = 0, flags = 0; const uint8_t* payload = nullptr; size_t payload_len = 0; };
inline uint32_t le32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
// "SPK1" | u32 stream | u32 seq | u16 frame_ms | u16 flags, little endian, then one Opus packet.
inline bool parse_header(const uint8_t* data, size_t len, Header& h)
{
    if (!data || len < 16 || std::memcmp(data, "SPK1", 4)) return false;
    h.stream = le32(data + 4); h.seq = le32(data + 8);
    h.frame_ms = uint16_t(data[12] | data[13] << 8); h.flags = uint16_t(data[14] | data[15] << 8);
    h.payload = data + 16; h.payload_len = len - 16;
    return true;
}

// Fixed slots in caller-owned storage of Slots*SlotBytes bytes (PSRAM on the robot). Single producer
// (link task) and single consumer (player task); the caller holds a mutex around every call.
template<size_t Slots, size_t SlotBytes>
class Ring {
public:
    explicit Ring(uint8_t* storage) : store_(storage) {}
    bool ready() const { return store_ != nullptr; }
    bool push(const uint8_t* p, size_t n)
    {
        if (!store_ || count_ == Slots || n > SlotBytes) return false;
        const size_t at = (head_ + count_) % Slots;
        std::memcpy(store_ + at * SlotBytes, p, n); len_[at] = static_cast<uint16_t>(n); ++count_;
        return true;
    }
    bool pop(uint8_t* out, size_t& n)
    {
        if (!count_) return false;
        n = len_[head_]; std::memcpy(out, store_ + head_ * SlotBytes, n);
        head_ = (head_ + 1) % Slots; --count_;
        return true;
    }
    void clear() { head_ = count_ = 0; }
    size_t size() const { return count_; }
    static constexpr size_t slots() { return Slots; }
    static constexpr size_t slot_bytes() { return SlotBytes; }
private:
    uint8_t* store_;
    uint16_t len_[Slots] = {};
    size_t head_ = 0, count_ = 0;
};

struct Report {
    enum Kind { Playing, Progress, Done, Stopped, Error } kind = Progress;
    uint32_t stream = 0;
    uint32_t played_ms = 0, buffered_ms = 0, underruns = 0, underrun_ms = 0;
    const char* error = "";
};

struct StreamState {
    uint32_t id = 0;
    bool started_msg = false, ended = false, stopped = false, finished = false;
    uint32_t played_frames = 0, underruns = 0, underrun_ms = 0;
    uint32_t stale = 0, overflow = 0, too_big = 0, received = 0;
    bool active() const { return started_msg && !finished; }

    // tts.start: a stream still running is replaced (it reports stopped), the ring starts empty.
    template<class R, class Out>
    void start(uint32_t sid, R& ring, Out&& report)
    {
        if (active()) {
            Report r; r.kind = Report::Stopped; r.stream = id; r.played_ms = played_frames * kFrameMs;
            report(r);
        }
        ring.clear();
        id = sid; started_msg = true; ended = stopped = finished = false;
        played_frames = underruns = underrun_ms = 0;
    }
    template<class R>
    bool push(const uint8_t* data, size_t len, R& ring)
    {
        Header h;
        if (!parse_header(data, len, h)) return false;
        if (!active() || h.stream != id || stopped) { ++stale; return false; }
        if (h.payload_len > R::slot_bytes()) { ++too_big; return false; }
        if (!ring.push(h.payload, h.payload_len)) { ++overflow; return false; }
        ++received;
        return true;
    }
    void end(uint32_t sid) { if (active() && sid == id) ended = true; }
    template<class R>
    void stop(uint32_t sid, R& ring) { if (active() && sid == id && !stopped) { stopped = true; ring.clear(); } }
};

struct View { bool has_stream = false, started = false, underrun = false, ended = false, stopped = false; size_t buffered = 0; };
enum class Action { Idle, Wait, Start, Play, Underrun, Silence, Done, Stopped };
// One decision per player step. Underrun keeps the speaker on and writes silence until 120 ms are
// buffered again (or the stream ended); an ended stream with an empty ring finishes at once.
inline Action decide(const View& v)
{
    if (!v.has_stream) return Action::Idle;
    if (v.stopped) return Action::Stopped;
    const size_t ms = v.buffered * kFrameMs;
    if (!v.started) {
        if (v.ended && !v.buffered) return Action::Done;
        return ms >= size_t(kPrebufferMs) || (v.ended && v.buffered) ? Action::Start : Action::Wait;
    }
    if (v.underrun) {
        if (v.ended && !v.buffered) return Action::Done;
        return ms >= size_t(kRebufferMs) || (v.ended && v.buffered) ? Action::Play : Action::Silence;
    }
    if (v.buffered) return Action::Play;
    return v.ended ? Action::Done : Action::Underrun;
}

// "ws://1.2.3.4:5/anything" → "ws://1.2.3.4:5/mouth"; "" without a ws scheme.
inline std::string mouth_url(const std::string& hearing_url)
{
    const size_t start = hearing_url.rfind("ws://", 0) == 0 ? 5 : hearing_url.rfind("wss://", 0) == 0 ? 6 : 0;
    if (!start) return {};
    const size_t slash = hearing_url.find('/', start);
    return hearing_url.substr(0, slash == std::string::npos ? hearing_url.size() : slash) + "/mouth";
}
}
