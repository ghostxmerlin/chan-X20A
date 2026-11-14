// hello modes step 1 (collector-0.4.92): the mode side of the robot's /mouth link (mouth_modes.h), frame by
// frame: text messages are length-checked and fully parsed before any parser sees them; snapshots apply on
// seq != current and always first after each handshake; mode_result and snapshot in either order; every
// applied snapshot is acked with mode_applied, and the ack stays pending until the socket took it; inputs are
// taken only while online, carry age_ms at send time, and are dropped on disconnect.
#include "mouth_modes.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
using namespace collector::mouth;
using namespace collector::modes;
using namespace ArduinoJson;

namespace {
std::string snap(uint32_t seq, const char* mode = "talk", const char* label = "TALK")
{
    return "{\"type\":\"mode\",\"seq\":" + std::to_string(seq) + ",\"mode\":\"" + mode +
           "\",\"dir\":null,\"host\":null,\"return_to\":null,\"billing\":true,\"since\":1763100000000,\"by\":\"asr\","
           "\"look\":{\"eye\":\"laughing_man\",\"pool\":null,\"label\":\"" + label + "\",\"color\":\"E040A0\"},"
           "\"touch\":{\"tap\":\"server\",\"double\":\"server\"}}";
}

// What mouth_link.cpp's on_message does with a text frame, minus the locks and the audio types.
struct Robot {
    ModeSession s;
    JsonDocument doc;
    uint32_t bad = 0;
    Text feed(const std::string& text)
    {
        const Text k = read_text(text.data(), text.size(), doc);
        if (k == Text::TooLong || k == Text::Bad) ++bad;
        else if (k == Text::Hello) s.link_up();
        else if (k == Text::Mode) s.on_mode(doc.as<JsonObjectConst>());
        else if (k == Text::ModeResult) s.on_result(doc.as<JsonObjectConst>());
        return k;
    }
    // One pass of the send loop: every line next() gives, "sent" by a socket that takes them all.
    std::string drain(int64_t now_us)
    {
        std::string out;
        Line l;
        uint32_t seq;
        for (ModeSession::Out k; (k = s.next(now_us, l, seq)) != ModeSession::Out::None;) {
            out += l.c_str(); out += '\n';
            s.sent(k, seq);
        }
        return out;
    }
    ModeView view() const { ModeView v; s.view(v); return v; }
};
const char* kHello = R"({"type":"hello","codec":"opus","frame_ms":60,"sample_rate":24000})";

void text_frames()
{
    JsonDocument doc;
    const std::string ok = snap(4);
    assert(read_text(ok.data(), ok.size(), doc) == Text::Mode && doc["seq"] == 4);

    // Length is checked first: 1601 B is refused even if it is valid JSON; 1600 B is parsed.
    std::string big = R"({"type":"mode","pad":")";
    big += std::string(kMaxMessage - big.size() - 2, 'x') + "\"}";
    assert(big.size() == kMaxMessage);
    assert(read_text(big.data(), big.size(), doc) == Text::Mode);
    std::string bigger = big; bigger.insert(bigger.size() - 2, "x");
    assert(bigger.size() == kMaxMessage + 1);
    assert(read_text(bigger.data(), bigger.size(), doc) == Text::TooLong && doc.isNull());

    // A cut-off snapshot is Bad and leaves nothing in the document (never a partly filled one).
    const std::string cut = ok.substr(0, ok.size() / 2);
    assert(read_text(cut.data(), cut.size(), doc) == Text::Bad && doc.isNull());
    for (const char* t : {"", "[1,2]", "\"mode\"", "42", "null", "{\"type\":\"mode\""}) {
        assert(read_text(t, std::strlen(t), doc) == Text::Bad && doc.isNull());
    }
    assert(read_text(nullptr, 0, doc) == Text::Bad);
    // A length shorter than the buffer: only n bytes count.
    assert(read_text(ok.data(), ok.size() - 1, doc) == Text::Bad);

    struct { const char* text; Text kind; } types[] = {
        {kHello, Text::Hello}, {R"({"type":"tts.start","stream":3})", Text::TtsStart},
        {R"({"type":"tts.end","stream":3})", Text::TtsEnd}, {R"({"type":"tts.stop","stream":3})", Text::TtsStop},
        {R"({"type":"mode_result","seq":1,"code":"ok"})", Text::ModeResult},
        {R"({"type":"mode_input","seq":1})", Text::Other}, {R"({"type":"mode_applied","seq":1})", Text::Other},
        {R"({"type":7})", Text::Other}, {R"({})", Text::Other}, {R"({"type":"MODE"})", Text::Other}};
    for (const auto& t : types) assert(read_text(t.text, std::strlen(t.text), doc) == t.kind);
}

void offline_until_hello()
{
    Robot r;
    assert(!r.s.online() && r.view().version == 0);
    // Before the ear's hello: snapshots and results are ignored, inputs refused, nothing is sent.
    r.feed(snap(5));
    r.feed(R"({"type":"mode_result","seq":1,"code":"ok","toast":"OK"})");
    assert(!r.view().have && !r.view().have_result);
    assert(r.s.push_input(0, "asr", "forward", -1) == 0 && r.s.push_select(0, "select", "chat", nullptr) == 0);
    assert(r.drain(0).empty());
    r.feed(kHello);
    assert(r.s.online() && r.view().online && r.view().version == 1);
    // The snapshot that came too early is not remembered: the next one is the first.
    r.feed(snap(5));
    assert(r.view().have && r.view().snap.seq == 5);
    assert(r.drain(0) == "{\"type\":\"mode_applied\",\"seq\":5}\n");
    JsonDocument t;
    r.s.json(t.to<JsonObject>());
    assert(t["ignored"] == 2 && t["inputs_offline"] == 2 && t["applied_seq"] == 5 && t["applied"] == 1);
}

void snapshots_and_acks()
{
    Robot r;
    r.feed(kHello);
    r.feed(snap(12));
    ModeView v = r.view();
    assert(v.have && v.snap.seq == 12 && !std::strcmp(v.snap.mode, "talk") && v.snap.color == 0xE040A0);
    const uint32_t ver = v.version;
    // The ack waits for the send loop and stays pending while the socket has not taken it.
    Line l; uint32_t seq;
    assert(r.s.next(0, l, seq) == ModeSession::Out::Ack && seq == 12);
    assert(r.s.next(0, l, seq) == ModeSession::Out::Ack && seq == 12);   // a failed send: still there
    r.s.sent(ModeSession::Out::Ack, 12);
    assert(r.s.next(0, l, seq) == ModeSession::Out::None);

    // Equal seq (the 60 s refresh, or the double push after the handshake): no-op, not bad, no ack.
    r.feed(snap(12, "chat", "CHAT"));
    assert(r.view().version == ver && !std::strcmp(r.view().snap.mode, "talk") && r.drain(0).empty());
    // A bad snapshot changes nothing and is counted.
    std::string bad = snap(13); bad.replace(bad.find("E040A0"), 6, "E040AZ");
    r.feed(bad);
    assert(r.view().version == ver && r.view().snap.seq == 12 && r.drain(0).empty());
    // seq != current applies, also backwards (mind's seq can go back after a restart).
    r.feed(snap(13, "chat", "CHAT"));
    assert(r.view().snap.seq == 13 && r.view().version != ver);
    r.feed(snap(9, "idle", "IDLE"));
    assert(r.view().snap.seq == 9 && !std::strcmp(r.view().snap.mode, "idle"));
    // Two applies before the send loop runs: only the latest is acked.
    assert(r.drain(0) == "{\"type\":\"mode_applied\",\"seq\":9}\n");
    // A cut-off frame never reaches the parser.
    const std::string s14 = snap(14);
    r.feed(s14.substr(0, 40));
    assert(r.bad == 1 && r.view().snap.seq == 9);

    JsonDocument t;
    r.s.json(t.to<JsonObject>());
    assert(t["seq"] == 9 && t["mode"] == "idle" && t["applied_seq"] == 9 && t["same_snapshots"] == 1 &&
           t["bad_snapshots"] == 1 && t["applied"] == 3 && t["ack_pending"] == false);
}

void reconnects()
{
    Robot r;
    r.feed(kHello);
    r.feed(snap(20));
    r.drain(0);
    // Link lost, then back: the first snapshot after the new handshake is applied and acked again, even with the
    // same seq; the one after it is a Same.
    r.s.link_down();
    assert(!r.view().online && r.view().have && r.view().snap.seq == 20);   // the screen keeps the last look
    r.feed(kHello);
    r.feed(snap(20));
    assert(r.drain(0) == "{\"type\":\"mode_applied\",\"seq\":20}\n");
    r.feed(snap(20));
    assert(r.drain(0).empty());

    // A bad first snapshot does not use up "first after handshake".
    r.s.link_down(); r.feed(kHello);
    std::string bad = snap(20); bad.replace(bad.find("\"billing\":true"), 14, "\"billing\":1");
    r.feed(bad);
    r.feed(snap(20));
    assert(r.drain(0) == "{\"type\":\"mode_applied\",\"seq\":20}\n");

    // An ack the socket never took survives the disconnect and goes out on the next link...
    r.feed(snap(21));
    r.s.link_down();
    Line l; uint32_t seq;
    assert(r.s.next(0, l, seq) == ModeSession::Out::None);                  // nothing goes out while offline
    r.feed(kHello);
    assert(r.drain(0) == "{\"type\":\"mode_applied\",\"seq\":21}\n");
    // ...unless a newer snapshot was applied first.
    r.feed(snap(22));
    r.s.link_down(); r.feed(kHello);
    r.feed(snap(23));
    assert(r.drain(0) == "{\"type\":\"mode_applied\",\"seq\":23}\n");
}

void results_either_order()
{
    // The robot's input 1 changes the mode: result first, then the snapshot...
    Robot a;
    a.feed(kHello);
    a.feed(snap(1));
    assert(a.s.push_input(1000000, "asr", "play", -1) == 1);
    a.drain(1000000);
    a.feed(R"({"type":"mode_result","seq":1,"code":"ok","toast":"CHAT"})");
    a.feed(snap(2, "chat", "CHAT"));
    // ...or the snapshot first, then the result. Both end the same.
    Robot b;
    b.feed(kHello);
    b.feed(snap(1));
    assert(b.s.push_input(1000000, "asr", "play", -1) == 1);
    b.drain(1000000);
    b.feed(snap(2, "chat", "CHAT"));
    b.feed(R"({"type":"mode_result","seq":1,"code":"ok","toast":"CHAT"})");
    const ModeView va = a.view(), vb = b.view();
    assert(va.snap.seq == 2 && vb.snap.seq == 2 && !std::memcmp(&va.snap, &vb.snap, sizeof va.snap));
    assert(va.have_result && vb.have_result && va.results == 1 && vb.results == 1);
    assert(!std::strcmp(va.result.toast, "CHAT") && !std::strcmp(vb.result.code, "ok") && vb.result.seq == 1);
    assert(a.drain(0) == "{\"type\":\"mode_applied\",\"seq\":2}\n");
    assert(b.drain(0) == "{\"type\":\"mode_applied\",\"seq\":2}\n");

    // The same toast twice still counts as a new result (the screen shows it again); a bad one is dropped.
    const uint32_t ver = b.view().version;
    b.feed(R"({"type":"mode_result","seq":2,"code":"busy","toast":"BUSY"})");
    b.feed(R"({"type":"mode_result","seq":3,"code":"busy","toast":"BUSY"})");
    assert(b.view().results == 3 && b.view().result.seq == 3 && b.view().version == ver + 2);
    b.feed(R"({"type":"mode_result","seq":4,"code":"ok","toast":"bad\ttoast"})");
    b.feed(R"({"type":"mode_result","code":"ok"})");
    assert(b.view().results == 3 && b.view().result.seq == 3 && b.view().version == ver + 2);
    // A stale input gets an empty toast.
    b.feed(R"({"type":"mode_result","seq":5,"code":"stale","toast":""})");
    assert(b.view().result.seq == 5 && !std::strcmp(b.view().result.toast, ""));
    JsonDocument t;
    b.s.json(t.to<JsonObject>());
    assert(t["results"] == 4 && t["bad_results"] == 2 && t["last_result"]["code"] == "stale");
}

void inputs()
{
    Robot r;
    r.feed(kHello);
    const int64_t t0 = 5000000;
    assert(r.s.push_input(t0, "asr", "forward", -1) == 1);
    assert(r.s.push_input(t0 + 10000, "asr", "digit", 7) == 2);
    assert(r.s.push_select(t0 + 20000, "select", "subtitle", "en_zh") == 3);
    assert(r.s.push_input(t0, "asr", "Forward", -1) == 0);                  // not a wire word: refused
    r.feed(snap(30));
    // The ack goes first; age_ms is counted when the line is built.
    assert(r.drain(t0 + 40000) ==
           "{\"type\":\"mode_applied\",\"seq\":30}\n"
           "{\"type\":\"mode_input\",\"seq\":1,\"age_ms\":40,\"source\":\"asr\",\"input\":\"forward\",\"value\":null}\n"
           "{\"type\":\"mode_input\",\"seq\":2,\"age_ms\":30,\"source\":\"asr\",\"input\":\"digit\",\"value\":7}\n"
           "{\"type\":\"mode_input\",\"seq\":3,\"age_ms\":20,\"source\":\"select\",\"input\":\"select\",\"value\":{\"mode\":\"subtitle\",\"dir\":\"en_zh\"}}\n");

    // A failed send keeps the input at the front.
    assert(r.s.push_input(t0, "touch", "tap", -1) == 4);
    Line l; uint32_t seq;
    assert(r.s.next(t0, l, seq) == ModeSession::Out::Input && seq == 4);
    assert(r.s.next(t0 + 1000, l, seq) == ModeSession::Out::Input && seq == 4);
    // While a line is in flight (lock released), the queue overflows and drops it: sent() must not pop the
    // input that took the front.
    for (int i = 0; i < 8; ++i) assert(r.s.push_input(t0, "touch", "double_tap", -1) == uint32_t(5 + i));
    r.s.sent(ModeSession::Out::Input, 4);
    assert(r.s.next(t0, l, seq) == ModeSession::Out::Input && seq == 5);
    JsonDocument t;
    r.s.json(t.to<JsonObject>());
    assert(t["inputs_queued"] == 8 && t["inputs_dropped"] == 1 && t["inputs_sent"] == 3 && t["last_input_seq"] == 12);

    // Disconnect drops the queue; seq keeps counting on the next link (mind dedups on boot + seq).
    r.s.link_down();
    assert(r.s.push_input(t0, "asr", "next", -1) == 0);
    r.feed(kHello);
    assert(r.drain(t0).empty());
    assert(r.s.push_input(t0, "asr", "next", -1) == 13);
    assert(r.drain(t0) == "{\"type\":\"mode_input\",\"seq\":13,\"age_ms\":0,\"source\":\"asr\",\"input\":\"next\",\"value\":null}\n");
    t.clear();
    r.s.json(t.to<JsonObject>());
    assert(t["inputs_queued"] == 0 && t["inputs_sent"] == 4 && t["inputs_offline"] == 1);
}

void versions()
{
    Robot r;
    uint32_t v = r.view().version;
    auto bumped = [&] { const uint32_t n = r.view().version; const bool b = n != v; v = n; return b; };
    r.feed(kHello);              assert(bumped());
    r.feed(kHello);              assert(!bumped());   // a second hello: still online, a new handshake
    r.feed(snap(1));             assert(bumped());
    r.feed(snap(1));             assert(!bumped());
    r.drain(0);                  assert(!bumped());   // sending changes nothing on the screen
    r.s.push_input(0, "asr", "play", -1); assert(!bumped());
    r.feed(R"({"type":"mode_result","seq":1,"code":"ok"})"); assert(bumped());
    r.s.link_down();             assert(bumped());
    r.s.link_down();             assert(!bumped());
    r.feed(kHello);              assert(bumped());
    r.feed(snap(1));             assert(bumped());    // first after the handshake: applied again
}
}

int main()
{
    text_frames();
    offline_until_hello();
    snapshots_and_acks();
    reconnects();
    results_either_order();
    inputs();
    versions();
    std::puts("mouth modes ok");
}
