// collector-0.4.77 /mouth: binary WebSocket messages, the packet ring (jitter buffer), the player's
// decisions and the 嘴 respond flag. Host build: c++ -std=c++17 -I<collector>.
#include "hearing_transport_policy.h"
#include "hearing_policy.h"
#include "mouth_stream.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>
using namespace collector;
using namespace collector::mouth;

static std::vector<uint8_t> packet(uint32_t sid, uint32_t seq, size_t payload = 200)
{
    std::vector<uint8_t> p(16 + payload, uint8_t(seq));
    std::memcpy(p.data(), "SPK1", 4);
    for (int i = 0; i < 4; ++i) { p[4 + i] = uint8_t(sid >> (8 * i)); p[8 + i] = uint8_t(seq >> (8 * i)); }
    p[12] = 60; p[13] = 0; p[14] = p[15] = 0;
    return p;
}

int main()
{
    // Hearing keeps refusing binary; the mouth accepts it up to its own limit.
    hearing::MessageBuffer text_only;
    assert(text_only.add(2, true, 3, "abc", 3) == hearing::MessageBuffer::Invalid);
    hearing::MessageBuffer b; b.allow_binary = true; b.max_bytes = 1600;
    assert(b.add(2, true, 3, "abc", 3) == hearing::MessageBuffer::Complete && b.binary);
    b.clear();
    assert(b.add(1, true, 2, "{}", 2) == hearing::MessageBuffer::Complete && !b.binary);
    b.clear();
    assert(b.add(2, false, 2, "ab", 2) == hearing::MessageBuffer::More);
    assert(b.add(0, true, 1, "c", 1) == hearing::MessageBuffer::Complete && b.binary && b.message == "abc");
    b.clear();
    assert(b.add(2, true, 1601, "x", 1) == hearing::MessageBuffer::Invalid);   // over the mouth limit
    b.clear();
    assert(b.add(9, true, 0, "", 0) == hearing::MessageBuffer::Invalid);      // control frames never reach it

    // Packet header.
    auto p = packet(7, 3);
    Header h{};
    assert(parse_header(p.data(), p.size(), h) && h.stream == 7 && h.seq == 3 && h.frame_ms == 60 && h.payload_len == 200);
    p[0] = 'X'; assert(!parse_header(p.data(), p.size(), h));
    assert(!parse_header(p.data(), 10, h));

    // Ring: only the current stream's packets, in order; stale and overflow counted.
    static uint8_t store[4 * 256];
    Ring<4, 256> ring(store);
    StreamState s;
    std::vector<Report> out;
    auto report = [&](const Report& r) { out.push_back(r); };
    s.start(5, ring, report);
    assert(s.id == 5 && s.active() && out.empty());
    auto a = packet(5, 0), stale = packet(4, 9);
    assert(s.push(a.data(), a.size(), ring));
    assert(!s.push(stale.data(), stale.size(), ring) && s.stale == 1);
    for (uint32_t i = 1; i < 4; ++i) { auto q = packet(5, i); assert(s.push(q.data(), q.size(), ring)); }
    auto full = packet(5, 4);
    assert(!s.push(full.data(), full.size(), ring) && s.overflow == 1);
    auto big = packet(5, 5, 300);
    s.overflow = 0; assert(!s.push(big.data(), big.size(), ring) && s.too_big == 1);
    assert(ring.size() == 4);
    uint8_t buf[256]; size_t n = 0;
    assert(ring.pop(buf, n) && n == 200 && buf[0] == 0);

    // A new start replaces the old one: the old stream reports stopped, the ring is cleared.
    s.played_frames = 2;
    s.start(6, ring, report);
    assert(ring.size() == 0 && out.size() == 1 && out[0].kind == Report::Stopped && out[0].stream == 5 && out[0].played_ms == 120);
    // stop for another stream is ignored; for this one it clears and marks stopped.
    auto c = packet(6, 0); s.push(c.data(), c.size(), ring);
    s.stop(5, ring); assert(!s.stopped && ring.size() == 1);
    s.stop(6, ring); assert(s.stopped && ring.size() == 0);
    auto late = packet(6, 1); assert(!s.push(late.data(), late.size(), ring));    // after stop: dropped
    // end only for the current stream
    s.start(8, ring, report); s.end(7); assert(!s.ended); s.end(8); assert(s.ended);

    // Player decisions (60 ms frames): prebuffer 180 ms, rebuffer 120 ms, done when ended and empty.
    View v{};
    v.has_stream = true;
    v.buffered = 2; assert(decide(v) == Action::Wait);
    v.buffered = 3; assert(decide(v) == Action::Start);
    v.buffered = 1; v.ended = true; assert(decide(v) == Action::Start);       // a short sentence that already ended
    v.buffered = 0; assert(decide(v) == Action::Done);                         // ended with nothing: finish at once
    v = View{}; v.has_stream = true; v.started = true;
    v.buffered = 1; assert(decide(v) == Action::Play);
    v.buffered = 0; assert(decide(v) == Action::Underrun);
    v.underrun = true; v.buffered = 1; assert(decide(v) == Action::Silence);
    v.buffered = 2; assert(decide(v) == Action::Play);
    v.buffered = 0; v.ended = true; assert(decide(v) == Action::Done);
    v.stopped = true; assert(decide(v) == Action::Stopped);
    v = View{}; assert(decide(v) == Action::Idle);

    // PCM queue between the decoder and the speaker writer: frames carry their stream; frames of an
    // older stream are dropped on the way out; full means the decoder waits.
    {
        static int16_t pcm_store[3 * 4];
        PcmFifo<3, 4> q(pcm_store);
        const int16_t f1[4] = {1, 1, 1, 1}, f2[4] = {2, 2, 2, 2}, f3[4] = {3, 3, 3, 3};
        assert(q.push(5, f1) && q.push(6, f2) && q.push(6, f3) && !q.push(6, f1));   // full
        assert(q.count_for(6) == 2 && q.count_for(5) == 1);
        int16_t out[4] = {};
        assert(q.pop_for(6, out) && out[0] == 2 && q.size() == 1);    // stream 5's frame dropped first
        assert(!q.pop_for(7, out) && q.size() == 0);                  // only another stream's: dropped, nothing out
        q.push(6, f3); q.clear(); assert(q.size() == 0);
    }

    // 嘴 mode: the ear's respond=false silences the emotion clips; an older ear (no field) answers.
    assert(hearing::response_allowed(false, false));
    assert(hearing::response_allowed(true, true));
    assert(!hearing::response_allowed(true, false));

    // mouth URL from the hearing URL, like /vision.
    assert(mouth_url("ws://1.2.3.4:52732/listen") == "ws://1.2.3.4:52732/mouth");
    assert(mouth_url("wss://1.2.3.4") == "wss://1.2.3.4/mouth");
    assert(mouth_url("http://x").empty());
    std::cout << "PASS mouth stream: binary messages, ring, player decisions, respond flag\n";
}
