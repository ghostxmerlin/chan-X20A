// 视觉爆发 indicator (collector-0.5.00): parsing the ear's burst frame, the packed word, the tracker's start / stop /
// countdown, the safety timeout, the link drop, the toasts and the time bar.
#include "burst_indicator.h"
#include "mouth_modes.h"
#include <cassert>
#include <cstring>
#include <string>
using namespace collector;
using namespace collector::burst;
using ArduinoJson::JsonDocument;
using ArduinoJson::JsonObjectConst;

static bool parse_text(const char* t, Frame& f)
{
    JsonDocument doc;
    if (mouth::read_text(t, std::strlen(t), doc) != mouth::Text::Burst) return false;
    return parse(doc.as<JsonObjectConst>(), std::strlen(t), f);
}
constexpr int64_t S = 1000000;

static void parsing()
{
    Frame f;
    assert(parse_text(R"({"type":"burst","on":true,"left_s":120,"looks":0})", f) && f.on && f.left_s == 120 && f.looks == 0);
    assert(parse_text(R"({"type":"burst","on":true,"left_s":0,"looks":7})", f) && f.on && f.left_s == 0 && f.looks == 7);
    assert(parse_text(R"({"type":"burst","on":true,"left_s":600,"looks":99999})", f) && f.left_s == 600 && f.looks == kLooksMax);
    assert(parse_text(R"({"type":"burst","on":true,"left_s":95,"looks":3,"burst_id":"b-1","extra":[1]})", f) && f.looks == 3);
    // off: every other field ignored, even bad ones
    assert(parse_text(R"({"type":"burst","on":false})", f) && !f.on && f.left_s == 0 && f.looks == 0);
    assert(parse_text(R"({"type":"burst","on":false,"left_s":"x","looks":-1})", f) && !f.on);
    for (const char* bad : {R"({"type":"burst"})", R"({"type":"burst","on":1,"left_s":10,"looks":1})",
                            R"({"type":"burst","on":"true","left_s":10,"looks":1})", R"({"type":"burst","on":null})",
                            R"({"type":"burst","on":true})", R"({"type":"burst","on":true,"left_s":10})",
                            R"({"type":"burst","on":true,"looks":1})", R"({"type":"burst","on":true,"left_s":-1,"looks":1})",
                            R"({"type":"burst","on":true,"left_s":601,"looks":1})", R"({"type":"burst","on":true,"left_s":10.5,"looks":1})",
                            R"({"type":"burst","on":true,"left_s":"10","looks":1})", R"({"type":"burst","on":true,"left_s":true,"looks":1})",
                            R"({"type":"burst","on":true,"left_s":10,"looks":-2})", R"({"type":"burst","on":true,"left_s":10,"looks":1.5})",
                            R"({"type":"burst","on":true,"left_s":10,"looks":false})", R"({"type":"burst","on":true,"left_s":10,"looks":null})"}) {
        f = Frame{}; f.left_s = 42;
        assert(!parse_text(bad, f));
        assert(f.left_s == 42);                       // a refused frame leaves the output alone
    }
    // 200 B is the limit: 200 parses, 201 is refused although it is valid JSON
    std::string t = R"({"type":"burst","on":true,"left_s":30,"looks":2,"pad":")";
    t += std::string(kTextMax - t.size() - 2, 'x') + "\"}";
    assert(t.size() == kTextMax && parse_text(t.c_str(), f) && f.left_s == 30);
    t.insert(t.size() - 2, "x");
    assert(!parse_text(t.c_str(), f));
    // not a burst
    JsonDocument doc;
    assert(mouth::read_text(R"({"type":"burst_result","on":true})", 33, doc) == mouth::Text::Other);
}

static void words()
{
    Frame f; f.on = true; f.left_s = 600; f.looks = kLooksMax;
    Word u = unpack(pack(f, 255, false));
    assert(u.seq == 255 && u.on && !u.lost && u.left_s == 600 && u.looks == kLooksMax);
    f.left_s = 37; f.looks = 12;
    u = unpack(pack(f, 1, false));
    assert(u.seq == 1 && u.on && u.left_s == 37 && u.looks == 12);
    u = unpack(pack(Frame{}, 9, true));
    assert(u.seq == 9 && !u.on && u.lost && u.left_s == 0 && u.looks == 0);
    // out of range values are clamped, never spill into the other fields
    f.left_s = 5000; f.looks = 1u << 20;
    u = unpack(pack(f, 3, false));
    assert(u.seq == 3 && u.on && !u.lost && u.left_s == kLeftMaxS && u.looks == kLooksMax);
    // seq: 1..255, never 0 (0 = nothing yet)
    assert(next_seq(0) == 1 && next_seq(1) == 2 && next_seq(254) == 255 && next_seq(255) == 1);
    uint32_t w = 0;
    for (int i = 0; i < 600; ++i) { w = publish(w, f, false); assert(unpack(w).seq != 0); }
    assert(unpack(0).seq == 0 && !unpack(0).on);
}

// A writer as mouth_link.cpp: one word, published per frame.
struct Wire {
    uint32_t w = 0;
    void on(int left, uint32_t looks) { Frame f; f.on = true; f.left_s = left; f.looks = looks; w = publish(w, f, false); }
    void off() { w = publish(w, Frame{}, false); }
    void lost() { if (unpack(w).on) w = publish(w, Frame{}, true); }
};

static void tracker_start_update_stop()
{
    Wire wire;
    Tracker t;
    int64_t now = 1000 * S;
    assert(t.tick(wire.w, now) == Event::None && !t.view().on);        // nothing yet
    wire.off();                                                        // an off while off: nothing
    assert(t.tick(wire.w, now) == Event::None && !t.view().on);
    wire.on(120, 0);
    assert(t.tick(wire.w, now) == Event::Started);
    assert(t.view().on && t.view().looks == 0 && t.view().left_s == 120 && t.view().total_s == 120 && t.starts() == 1);
    assert(t.tick(wire.w, now) == Event::None);                        // the same word again: no event
    // countdown on the robot's clock, rounded up
    t.tick(wire.w, now + S / 2);
    assert(t.view().left_s == 120);                                    // 119.5 s left shows 120
    t.tick(wire.w, now + S);
    assert(t.view().left_s == 119);
    // a look 4 s later: count and left_s from the frame
    now += 4 * S;
    wire.on(116, 1);
    assert(t.tick(wire.w, now) == Event::None);
    assert(t.view().looks == 1 && t.view().left_s == 116 && t.view().total_s == 120);
    // an identical frame (same content) still resets the timeout: it is a new word
    now += 3 * S;
    wire.on(113, 1);
    assert(t.tick(wire.w, now) == Event::None && t.view().left_s == 113);
    // stop frame
    now += 2 * S;
    wire.off();
    assert(t.tick(wire.w, now) == Event::Stopped);
    assert(!t.view().on && t.stopped_looks() == 1 && t.why() == Why::Frame);
    assert(t.tick(wire.w, now + S) == Event::None && !t.view().on);
    // a new burst after it: started again, total from its own first frame
    wire.on(45, 0);
    assert(t.tick(wire.w, now + 2 * S) == Event::Started && t.view().total_s == 45 && t.view().looks == 0 && t.starts() == 2);
    // an extension (left_s above the total) makes the bar's full length larger
    wire.on(90, 1);
    t.tick(wire.w, now + 3 * S);
    assert(t.view().total_s == 90 && t.view().left_s == 90);
}

static void tracker_timeout()
{
    // no frame for left_s + 10 s: off by itself (Why::Timeout), the count kept for the toast
    {
        Wire wire;
        Tracker t;
        const int64_t t0 = 50 * S;
        wire.on(120, 4);
        assert(t.tick(wire.w, t0) == Event::Started);
        assert(hold_us(120) == 130 * S);
        assert(t.tick(wire.w, t0 + 130 * S - 1) == Event::None && t.view().on && t.view().left_s == 0);
        assert(t.tick(wire.w, t0 + 130 * S) == Event::Stopped);
        assert(!t.view().on && t.why() == Why::Timeout && t.stopped_looks() == 4);
        assert(t.tick(wire.w, t0 + 200 * S) == Event::None);
        // the ear's next frame (late) starts it again
        wire.on(20, 5);
        assert(t.tick(wire.w, t0 + 201 * S) == Event::Started && t.view().looks == 5);
    }
    // small left_s: at least 15 s since the last frame
    {
        Wire wire;
        Tracker t;
        assert(hold_us(0) == 15 * S && hold_us(4) == 15 * S && hold_us(5) == 15 * S && hold_us(6) == 16 * S);
        wire.on(2, 9);
        assert(t.tick(wire.w, 0) == Event::Started);
        assert(t.tick(wire.w, 3 * S) == Event::None);
        assert(t.view().left_s == 0 && t.view().on);
        assert(t.tick(wire.w, 15 * S - 1) == Event::None);
        assert(t.tick(wire.w, 15 * S) == Event::Stopped && t.why() == Why::Timeout && t.stopped_looks() == 9);
    }
    // each frame restarts the hold from its own arrival
    {
        Wire wire;
        Tracker t;
        wire.on(0, 1);
        t.tick(wire.w, 0);
        for (int i = 1; i <= 10; ++i) {
            assert(t.tick(wire.w, i * 14 * S - 1) == Event::None);
            wire.on(0, uint32_t(i + 1));
            assert(t.tick(wire.w, i * 14 * S) == Event::None && t.view().on);
        }
        assert(t.tick(wire.w, 140 * S + 15 * S) == Event::Stopped);
    }
}

static void tracker_link()
{
    Wire wire;
    Tracker t;
    wire.lost();                               // link drop while off: nothing published, nothing happens
    assert(wire.w == 0 && t.tick(wire.w, 0) == Event::None);
    wire.on(100, 2);
    assert(t.tick(wire.w, S) == Event::Started);
    wire.lost();
    assert(unpack(wire.w).lost && !unpack(wire.w).on);
    assert(t.tick(wire.w, 2 * S) == Event::Stopped && t.why() == Why::Link && t.stopped_looks() == 2);
    const uint32_t before = wire.w;
    wire.lost();                               // a second drop (reconnect loop): nothing new
    assert(wire.w == before && t.tick(wire.w, 3 * S) == Event::None);
    // after the reconnect the ear's next frame shows it again
    wire.on(80, 3);
    assert(t.tick(wire.w, 10 * S) == Event::Started && t.view().looks == 3);
    // several words between two ticks: the latest wins (the screen never sees an older one)
    wire.on(70, 4); wire.on(69, 5); wire.off();
    assert(t.tick(wire.w, 11 * S) == Event::Stopped && t.stopped_looks() == 3 && t.why() == Why::Frame);
}

static void toasts_and_bar()
{
    char b[24];
    start_toast(120, b, sizeof b); assert(!std::strcmp(b, "LOOKING 2 MIN"));
    start_toast(61, b, sizeof b); assert(!std::strcmp(b, "LOOKING 2 MIN"));
    start_toast(60, b, sizeof b); assert(!std::strcmp(b, "LOOKING 1 MIN"));
    start_toast(600, b, sizeof b); assert(!std::strcmp(b, "LOOKING 10 MIN"));
    start_toast(45, b, sizeof b); assert(!std::strcmp(b, "LOOKING 45 S"));
    start_toast(0, b, sizeof b); assert(!std::strcmp(b, "LOOKING"));
    stop_toast(0, b, sizeof b); assert(!std::strcmp(b, "LOOK DONE 0"));
    stop_toast(37, b, sizeof b); assert(!std::strcmp(b, "LOOK DONE 37"));
    stop_toast(kLooksMax, b, sizeof b); assert(!std::strcmp(b, "LOOK DONE 4095"));
    count_text(0, b, sizeof b); assert(!std::strcmp(b, "0"));
    count_text(42, b, sizeof b); assert(!std::strcmp(b, "42"));
    count_text(1000, b, sizeof b); assert(!std::strcmp(b, "999"));
    View v; v.on = true; v.total_s = 120;
    v.left_s = 120; assert(bar_fill(v, 38) == 38);
    v.left_s = 60; assert(bar_fill(v, 38) == 19);
    v.left_s = 1; assert(bar_fill(v, 38) == 1);           // any time left: at least 1 px
    v.left_s = 0; assert(bar_fill(v, 38) == 0);
    v.left_s = 200; assert(bar_fill(v, 38) == 38);        // never past the group
    v.left_s = 30; v.total_s = 0; assert(bar_fill(v, 38) == 38);
    v.on = false; v.left_s = 60; v.total_s = 120; assert(bar_fill(v, 38) == 0);
    v.on = true; assert(bar_fill(v, 0) == 0);
}

int main()
{
    parsing();
    words();
    tracker_start_update_stop();
    tracker_timeout();
    tracker_link();
    toasts_and_bar();
    std::puts("burst indicator ok");
}
