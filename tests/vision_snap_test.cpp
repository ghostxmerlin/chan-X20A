// 视觉爆发 snap (collector-0.4.99): parsing, single flight, the 2 s deadline, late completions and result lines.
#include "mouth_modes.h"
#include "vision_snap.h"
#include <cassert>
#include <cstring>
#include <string>
using namespace collector;
using namespace collector::snap;
using ArduinoJson::JsonDocument;
using ArduinoJson::JsonObjectConst;

static bool parse_text(const char* t, uint32_t& id)
{
    JsonDocument doc;
    if (mouth::read_text(t, std::strlen(t), doc) != mouth::Text::Snap) return false;
    return parse(doc.as<JsonObjectConst>(), std::strlen(t), id);
}

static std::string line(const Result& r)
{
    char b[kLineMax];
    const size_t n = result_line(r, b, sizeof b);
    return std::string(b, n);
}

static void parsing()
{
    uint32_t id = 0;
    assert(parse_text(R"({"type":"snap","id":7})", id) && id == 7);
    assert(parse_text(R"({"type":"snap","id":0})", id) && id == 0);
    assert(parse_text(R"({"type":"snap","id":4294967295})", id) && id == 4294967295u);
    assert(parse_text(R"({"type":"snap","id":12,"extra":"ignored"})", id) && id == 12);
    for (const char* bad : {R"({"type":"snap"})", R"({"type":"snap","id":-1})", R"({"type":"snap","id":4294967296})",
                            R"({"type":"snap","id":1.5})", R"({"type":"snap","id":"7"})", R"({"type":"snap","id":true})",
                            R"({"type":"snap","id":null})"}) {
        id = 99;
        assert(!parse_text(bad, id));
    }
    // 200 B is the limit: 200 parses, 201 is refused although it is valid JSON
    std::string t = R"({"type":"snap","id":5,"pad":")";
    t += std::string(kTextMax - t.size() - 2, 'x') + "\"}";
    assert(t.size() == kTextMax && parse_text(t.c_str(), id) && id == 5);
    t.insert(t.size() - 2, "x");
    assert(!parse_text(t.c_str(), id));
    // not a snap
    JsonDocument doc;
    assert(mouth::read_text(R"({"type":"snap_result","id":1})", 29, doc) == mouth::Text::Other);
}

static void lines()
{
    Result ok; ok.id = 7; ok.ok = true; ok.seq = 1234; ok.bytes = 18000; ok.ms = 180;
    assert(line(ok) == R"({"type":"snap_result","id":7,"ok":true,"seq":1234,"bytes":18000,"ms":180})");
    Result busy; busy.id = 8; busy.why = Why::Busy;
    assert(line(busy) == R"({"type":"snap_result","id":8,"ok":false,"why":"busy"})");
    for (Why w : {Why::CameraOff, Why::NoUplink, Why::Timeout}) {
        Result r; r.id = 4294967295u; r.why = w;
        const std::string s = line(r);
        JsonDocument doc;
        assert(!ArduinoJson::deserializeJson(doc, s) && doc["why"] == why_name(w) && doc["ok"] == false && doc["id"] == 4294967295u);
    }
    Result big; big.id = big.seq = big.bytes = big.ms = 4294967295u; big.ok = true;
    const std::string s = line(big);
    assert(!s.empty() && s.size() < kLineMax);
    JsonDocument doc;
    assert(!ArduinoJson::deserializeJson(doc, s) && doc["seq"] == 4294967295u);
    char tiny[20];
    assert(result_line(ok, tiny, sizeof tiny) == 0);       // never a cut line
}

static void single_flight()
{
    Slot s;
    Result r;
    Ticket t;
    int64_t now = 1000000;
    assert(!s.waiting(now, t) && !s.take(now, r));
    // refusals queue their result at once, nothing is in flight
    assert(s.request(1, now, false, true) == Why::CameraOff && !s.busy());
    assert(s.request(2, now, true, false) == Why::NoUplink && !s.busy());
    assert(s.take(now, r) && r.id == 1 && !r.ok && r.why == Why::CameraOff);
    assert(s.take(now, r) && r.id == 2 && r.why == Why::NoUplink && !s.take(now, r));
    // accepted: the vision task sees it, a second one is busy (busy wins over camera_off)
    assert(s.request(3, now, true, true) == Why::None && s.busy());
    assert(s.request(4, now + 10, false, false) == Why::Busy);
    assert(s.take(now, r) && r.id == 4 && r.why == Why::Busy && !s.take(now, r));
    assert(s.waiting(now + 100000, t) && t.id == 3 && t.since_us == now);
    assert(!fresh(now - 1, t.since_us) && fresh(now, t.since_us) && fresh(now + 50000, t.since_us));
    assert(!s.take(now + 100000, r));                      // no result before it is sent
    assert(s.sent(t, now + 180000, 1234, 18000) && !s.busy());
    assert(s.take(now + 190000, r) && r.id == 3 && r.ok && r.seq == 1234 && r.bytes == 18000 && r.ms == 180);
    assert(!s.sent(t, now + 200000, 1235, 1) && s.counters().late == 1);   // a second completion: late, ignored
    assert(!s.take(now + 200000, r));
    // next one right after the result: accepted
    assert(s.request(5, now + 300000, true, true) == Why::None);
    assert(s.waiting(now + 300000, t) && t.id == 5);
    assert(s.failed(t, Why::NoUplink) && !s.busy());
    assert(s.take(now + 300000, r) && r.id == 5 && !r.ok && r.why == Why::NoUplink);
    const Counters& c = s.counters();
    assert(c.accepted == 2 && c.ok == 1 && c.failed == 1 && c.refused == 3 && c.timeouts == 0);
}

static void deadline()
{
    Slot s;
    Result r;
    Ticket t;
    const int64_t at = 5000000;
    assert(s.request(9, at, true, true) == Why::None);
    assert(s.waiting(at + kDeadlineUs - 1, t) && t.id == 9);
    assert(!s.take(at + kDeadlineUs - 1, r));
    // the /mouth pass runs the deadline: exactly 2 s after accept, timeout
    assert(s.take(at + kDeadlineUs, r) && r.id == 9 && !r.ok && r.why == Why::Timeout && !s.busy());
    // the frame that was being sent finishes later: late, no second result
    assert(!s.sent(t, at + kDeadlineUs + 500000, 77, 100) && !s.take(at + kDeadlineUs + 500000, r));
    // a new snap after the timeout gets a new ticket; the old one cannot complete it
    assert(s.request(10, at + 3000000, true, true) == Why::None);
    Ticket t2;
    assert(s.waiting(at + 3000000, t2) && t2.id == 10 && t2.gen != t.gen);
    assert(!s.sent(t, at + 3100000, 78, 100));
    assert(s.sent(t2, at + 3200000, 79, 100) && s.take(at + 3200000, r) && r.id == 10 && r.ok && r.seq == 79);
    // the deadline also runs from the vision side and from a new request
    assert(s.request(11, at + 4000000, true, true) == Why::None);
    assert(!s.waiting(at + 4000000 + kDeadlineUs, t) && !s.busy());
    assert(s.request(12, at + 4000000 + kDeadlineUs, true, true) == Why::None);
    assert(s.take(at + 4000000 + kDeadlineUs, r) && r.id == 11 && r.why == Why::Timeout);
    assert(s.busy() && s.counters().timeouts == 2);
}

static void link_down_and_overflow()
{
    Slot s;
    Result r;
    Ticket t;
    assert(s.request(1, 0, true, true) == Why::None && s.waiting(0, t));
    s.reset();                                             // /mouth dropped: nothing in flight, nothing queued
    assert(!s.busy() && !s.take(0, r) && !s.sent(t, 1000, 1, 1) && !s.take(1000, r));
    // results queue holds 4; more drop the oldest
    assert(s.request(2, 0, true, true) == Why::None);
    for (uint32_t id = 10; id < 15; ++id) assert(s.request(id, 0, true, true) == Why::Busy);
    assert(s.queued() == kResultCap && s.counters().dropped == 1);
    assert(s.take(0, r) && r.id == 11);
    s.reset();
    assert(!s.take(0, r) && s.queued() == 0);
}

int main()
{
    parsing();
    lines();
    single_flight();
    deadline();
    link_down_and_overflow();
    return 0;
}
