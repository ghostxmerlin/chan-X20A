// Host tests for the m12d protocol layer (collector/m12_protocol.h). Example lines are the
// ones in m12d docs/protocol.md; names are placeholders.
#include "../upstream/StackChan/firmware/main/collector/m12_protocol.h"
#include "../upstream/StackChan/firmware/main/collector/vision_uplink.h"
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

using namespace collector;

static ArduinoJson::JsonDocument doc;
static M12Message parse(const std::string& line)
{
    M12Message m;
    if (!parse_m12_line(line.data(), line.size(), m, doc)) m.kind = M12Kind::invalid;
    return m;
}
// Feed a byte stream in uneven chunks; collect parsed messages in arrival order.
static std::vector<M12Message> stream(const std::string& bytes, size_t capacity = kM12MaxLine)
{
    std::vector<char> storage(capacity);
    M12LineBuffer buffer(storage.data(), storage.size());
    std::vector<M12Message> out;
    for (char c : bytes)
        if (buffer.push(c)) out.push_back(parse(std::string(buffer.line(), buffer.size())));
    return out;
}

static const char* kDetection =
    R"({"f":8542,"num":1,)"
    R"("face":[{"x":278.3,"y":143.9,"w":100.9,"h":140.6,"prob":0.972,)"
    R"("mark":[{"x":296,"y":184},{"x":316,"y":186},{"x":286,"y":208},{"x":296,"y":242},{"x":312,"y":242}],)"
    R"("id":1,"gender":"m","age":38,"age_group":"adult","name":"me"}],)"
    R"("running":"Face Detector",)"
    R"("person":[{"x":87.5,"y":121.6,"w":525.8,"h":389.4,"prob":0.991,"id":18,"face":1}]})";
static const char* kHeartbeat = R"({"hb":{"t":77,"fps":15.4,"tier":0,"up":2107,"mem":71284,"frames":894,"reopens":0,"drop":0,"mhz":1400}})";

int main()
{
    // --- detection line (§3.1) ---
    {
        auto m = parse(kDetection);
        assert(m.kind == M12Kind::detection);
        assert(m.det.f == 8542 && m.det.num == 1 && m.det.count == 1);
        const auto& f = m.det.faces[0];
        assert(f.x == 278 && f.y == 144 && f.w == 101 && f.h == 141);
        assert(std::fabs(f.prob - 0.972f) < 1e-4f && f.id == 1 && f.gender == 'm' && f.age == 38);
        assert(f.age_group == M12AgeGroup::adult && !std::strcmp(f.name, "me"));
        assert(m.det.persons == 1 && m.det.person[0].id == 18 && m.det.person[0].face == 1 && m.det.person[0].w == 526);
        // empty frame, new face without attributes yet, unknown extra fields
        auto e = parse(R"({"f":9,"num":0,"face":[],"running":"Face Detector","new_field":[1,2]})");
        assert(e.kind == M12Kind::detection && e.det.count == 0 && e.det.persons == 0);
        auto n = parse(R"({"f":10,"num":1,"face":[{"x":10,"y":20,"w":30,"h":40,"prob":0.9,"id":7}]})");
        assert(n.det.faces[0].gender == 0 && n.det.faces[0].age == -1 && !n.det.faces[0].name[0]);
        // boxes are clamped to the 640x480 frame
        auto c = parse(R"({"f":11,"num":1,"face":[{"x":630,"y":470,"w":100,"h":100,"prob":0.9,"id":2}]})");
        assert(c.det.faces[0].x + c.det.faces[0].w <= kM12FrameWidth && c.det.faces[0].y + c.det.faces[0].h <= kM12FrameHeight);
        // more faces than kept
        std::string many = R"({"f":12,"num":10,"face":[)";
        for (int i = 0; i < 10; ++i) many += std::string(i ? "," : "") + R"({"x":1,"y":1,"w":10,"h":10,"prob":0.9,"id":)" + std::to_string(i) + "}";
        many += "]}";
        auto mm = parse(many);
        assert(mm.det.num == 10 && mm.det.count == kM12MaxFaces);
    }
    // --- heartbeat (§3.2) ---
    {
        auto m = parse(kHeartbeat);
        assert(m.kind == M12Kind::heartbeat && m.hb.valid);
        assert(m.hb.t == 77 && std::fabs(m.hb.fps - 15.4f) < 1e-4f && m.hb.tier == 0 && m.hb.up == 2107);
        assert(m.hb.mem == 71284 && m.hb.frames == 894 && m.hb.mhz == 1400);
        auto hot = parse(R"({"hb":{"t":91,"fps":0,"tier":3,"up":5000}})");
        assert(hot.hb.tier == 3 && hot.hb.t == 91);
    }
    // --- replies (§4) ---
    {
        auto p = parse(R"({"id":1,"ok":true})");
        assert(p.kind == M12Kind::reply && p.reply.id == 1 && p.reply.ok && !p.reply.err[0]);
        auto v = parse(R"({"id":2,"ok":true,"ver":"0.1.0","git":"abc","models":["mnet.25-opt","genderage","yolo-fastest","mobilefacenet"]})");
        assert(!std::strcmp(v.reply.ver, "0.1.0"));
        auto s = parse(R"({"id":3,"ok":true,"hb":{"t":70,"fps":15,"tier":0,"up":12}})");
        assert(s.kind == M12Kind::reply && s.reply.hb.valid && s.reply.hb.t == 70);
        auto c = parse(R"({"id":4,"ok":true,"cfg":{"det":false,"lm":true,"attr":true,"person":true,"id_th":0.50,"max_faces":10,"fps_cap":0.0,"sub":true}})");
        assert(c.reply.cfg.valid && !c.reply.cfg.det && c.reply.cfg.max_faces == 10 && std::fabs(c.reply.cfg.id_th - .5f) < 1e-6f);
        auto l = parse(R"({"id":5,"ok":true,"name":"person-a","samples":5,"f":1234})");
        assert(l.reply.has_name && !std::strcmp(l.reply.name, "person-a") && l.reply.samples == 5 && l.reply.f == 1234);
        auto w = parse(R"({"id":6,"ok":true,"f":1240,"face":1,"name":"person-a","sim":0.768})");
        assert(w.reply.face == 1 && std::fabs(w.reply.sim - .768f) < 1e-4f && !std::strcmp(w.reply.name, "person-a"));
        auto wn = parse(R"({"id":6,"ok":true,"f":1241,"face":3,"name":null,"sim":0.31})");
        assert(wn.reply.ok && !wn.reply.has_name && !wn.reply.name[0] && wn.reply.face == 3);
        auto f = parse(R"({"id":7,"ok":true,"name":"person-a"})");
        assert(f.reply.has_name);
        auto lib = parse(R"({"id":8,"ok":true,"faces":[{"name":"person-a","samples":5},{"name":"person-b","samples":20}]})");
        assert(lib.reply.has_library && lib.reply.library_count == 2 && lib.reply.library[1].samples == 20);
        auto empty = parse(R"({"id":9,"ok":true,"faces":[]})");
        assert(empty.reply.has_library && empty.reply.library_count == 0);
    }
    // --- error codes (§5) ---
    {
        for (const char* code : {"badjson", "toolong", "badarg", "unknown", "busy", "timeout", "noface", "standby", "hot", "nomodel", "full", "io"}) {
            const std::string line = std::string(R"({"id":)") + (std::strcmp(code, "badjson") && std::strcmp(code, "toolong") ? "11" : "-1") +
                                     R"(,"ok":false,"err":")" + code + "\"}";
            auto m = parse(line);
            assert(m.kind == M12Kind::reply && !m.reply.ok && !std::strcmp(m.reply.err, code));
        }
        auto conflict = parse(R"({"id":12,"ok":false,"err":"conflict","name":"person-b"})");
        assert(!conflict.reply.ok && !std::strcmp(conflict.reply.err, "conflict") && !std::strcmp(conflict.reply.name, "person-b"));
        assert(parse(R"({"id":-1,"ok":false,"err":"badjson"})").reply.id == -1);
    }
    // --- framing: half line at power-up, garbage, CRLF, overlong, interleaving ---
    {
        std::string bytes = std::string("ace\":[]}\n") + "\x01\xff garbage\n" + kHeartbeat + "\r\n" + R"({"id":21,"ok":true})" + "\n" +
                            kDetection + "\n" + R"({"id":22,"ok":true,"name":"person-a","samples":5,"f":7})" + "\n" + kHeartbeat + "\n";
        auto msgs = stream(bytes);
        assert(msgs.size() == 7);
        assert(msgs[0].kind == M12Kind::invalid && msgs[1].kind == M12Kind::invalid);
        assert(msgs[2].kind == M12Kind::heartbeat && msgs[3].kind == M12Kind::reply && msgs[3].reply.id == 21);
        assert(msgs[4].kind == M12Kind::detection && msgs[5].kind == M12Kind::reply && msgs[5].reply.id == 22);
        assert(msgs[6].kind == M12Kind::heartbeat);
        // overlong line is dropped whole, the next line still parses
        std::string longline(5000, 'x');
        auto after = stream(longline + "\n" + kHeartbeat + "\n");
        assert(after.size() == 1 && after[0].kind == M12Kind::heartbeat);
        std::vector<char> small(64);
        M12LineBuffer b(small.data(), small.size());
        for (char ch : std::string(100, 'y') + "\n") b.push(ch);
        assert(b.overflows() == 1);
        // a ready line stays readable until the next byte
        M12LineBuffer r(small.data(), small.size());
        for (char ch : std::string("{\"id\":1,\"ok\":true}\n")) if (r.push(ch)) assert(!std::strcmp(r.line(), "{\"id\":1,\"ok\":true}"));
        assert(!r.push('\n') && !r.push('\n'));        // empty lines are ignored
    }
    // --- request lines ---
    {
        char out[kM12MaxRequest + 8];
        assert(m12_build_request(out, sizeof(out), 1, M12Op::ping) && !std::strcmp(out, "{\"id\":1,\"do\":\"ping\"}\n"));
        M12RequestArgs det; det.det = 0;
        m12_build_request(out, sizeof(out), 2, M12Op::cfg, det);
        assert(!std::strcmp(out, "{\"id\":2,\"do\":\"cfg\",\"det\":false}\n"));
        det.lm = 0;
        m12_build_request(out, sizeof(out), 2, M12Op::cfg, det);
        assert(!std::strcmp(out, "{\"id\":2,\"do\":\"cfg\",\"det\":false,\"lm\":false}\n"));
        m12_build_request(out, sizeof(out), 3, M12Op::cfg);
        assert(!std::strcmp(out, "{\"id\":3,\"do\":\"cfg\"}\n"));
        M12RequestArgs who; who.face = 4;
        m12_build_request(out, sizeof(out), 4, M12Op::who, who);
        assert(!std::strcmp(out, "{\"id\":4,\"do\":\"who\",\"face\":4}\n"));
        M12RequestArgs learn; learn.name = "a\"b\\c";
        m12_build_request(out, sizeof(out), 5, M12Op::learn, learn);
        assert(!std::strcmp(out, "{\"id\":5,\"do\":\"learn\",\"name\":\"a\\\"b\\\\c\"}\n"));
        auto back = parse(std::string(out, std::strlen(out) - 1));          // what m12d would read
        (void)back;
        M12RequestArgs bad;
        for (const char* name : {"", "\x01x", "123456789012345678901234567890123"}) { bad.name = name; assert(!m12_build_request(out, sizeof(out), 6, M12Op::learn, bad)); }
        bad.name = nullptr;
        assert(!m12_build_request(out, sizeof(out), 6, M12Op::forget, bad));
        bad.name = "名字";                                                  // UTF-8 is fine
        assert(m12_build_request(out, sizeof(out), 6, M12Op::forget, bad));
        M12Op op;
        assert(m12_op_from_name("learn", op) && op == M12Op::learn && !m12_op_from_name("function", op));
    }
    // --- pending table and client timeouts ---
    {
        M12Requests q;
        const int32_t a = q.add(M12Op::who, 0), b = q.add(M12Op::ping, 0, 42);
        assert(a == 1 && b == 2 && q.outstanding() == 2 && q.outstanding(M12Op::who) == 1);
        M12Pending p;
        assert(!q.expire(999999, p));
        assert(q.expire(1000000, p) && p.id == b && p.tag == 42);              // ping: 1 s
        assert(!q.expire(3500000, p));
        assert(q.expire(kM12SlowReplyUs, p) && p.id == a && p.op == M12Op::who);   // who: >= 3.5 s
        assert(kM12SlowReplyUs >= 3500000 && kM12FastReplyUs == 1000000);
        const int32_t c = q.add(M12Op::learn, 10);
        assert(q.take(c, p) && p.op == M12Op::learn && !q.take(c, p));       // replies after a timeout are unknown
        for (size_t i = 0; i < M12Requests::kSlots; ++i) assert(q.add(M12Op::faces, 0) > 0);
        assert(q.add(M12Op::faces, 0) == -1);
    }
    // --- automatic who ---
    {
        M12WhoPolicy w;
        auto d = parse(R"({"f":1,"num":2,"face":[{"x":1,"y":1,"w":50,"h":50,"prob":0.9,"id":5},{"x":100,"y":1,"w":50,"h":50,"prob":0.9,"id":6,"name":"person-a"}]})").det;
        assert(w.next(d, 0) == 5);
        w.sent(5, 0);
        assert(w.next(d, 1) == -1);                                          // one in flight
        w.answered(5, "busy", 10);
        assert(w.next(d, 20) == -1 && w.next(d, 10 + M12WhoPolicy::kFirstBackoffUs) == 5);
        w.sent(5, 600000);
        w.answered(5, "busy", 600000);                                       // backoff doubles
        assert(w.next(d, 600000 + M12WhoPolicy::kFirstBackoffUs) == -1 && w.next(d, 600000 + 2 * M12WhoPolicy::kFirstBackoffUs) == 5);
        w.sent(5, 2000000);
        w.answered(5, nullptr, 2000000);                                     // answered null: one ask used
        assert(w.asked(5) && w.asks(5) == 1 && w.busy() == 2 && w.asks(6) == 0);
        auto d2 = parse(R"({"f":2,"num":1,"face":[{"x":1,"y":1,"w":50,"h":50,"prob":0.9,"id":7}]})").det;
        assert(w.next(d2, 2000001) == 7);                                    // a new face first
        w.sent(7, 2000001);
        w.answered(7, "standby", 2000002);
        assert(w.next(d2, 2000003) == -1 && w.next(d2, 2000002 + M12WhoPolicy::kWaitUs) == 7);
        w.sent(7, 0); w.answered(7, "client_timeout", 0);
        assert(w.asks(7) == 1);
    }
    // --- 2026-11-15 owner: 最多再问 5 次，无间隔询问 ---
    {
        auto line = [](int x, int w, int id) {
            std::string j = R"({"f":1,"num":1,"face":[{"x":)" + std::to_string(x) + R"(,"y":100,"w":)" + std::to_string(w) +
                            R"(,"h":)" + std::to_string(w) + R"(,"prob":0.9,"id":)" + std::to_string(id) + "}]}";
            return parse(j).det;
        };
        M12WhoPolicy w;
        auto f = line(200, 100, 9);
        int64_t t = 0;
        for (int ask = 1; ask <= 1 + M12WhoPolicy::kReasks; ++ask) {         // 6 asks, each right after the answer
            assert(w.next(f, t) == 9);
            w.sent(9, t);
            assert(w.next(f, t + 1) == -1);                                  // still one in flight
            t += 220000;
            w.answered(9, ask % 2 ? nullptr : "noface", t);
            assert(w.asks(9) == ask);
        }
        assert(w.next(f, t) == -1 && w.next(f, t + 60000000) == -1 && w.reasks() == 5);   // never a seventh
        // no re-ask for a face under 48 px; it still gets its first ask
        M12WhoPolicy s;
        auto tiny = line(200, 47, 4);
        assert(s.next(tiny, 0) == 4); s.sent(4, 0); s.answered(4, nullptr, 1);
        assert(s.next(tiny, 2) == -1 && s.next(line(200, 48, 4), 3) == 4);
        // a name closes a track at once
        M12WhoPolicy n;
        auto g = line(100, 120, 3);
        assert(n.next(g, 0) == 3);
        n.sent(3, 0); n.answered(3, nullptr, 0, true);
        assert(n.next(g, 1) == -1 && n.asks(3) == 1);
        // a new face's first ask goes before any re-ask; re-asks: fewest asks first, then the larger face
        M12WhoPolicy q;
        auto two = parse(R"({"f":1,"num":2,"face":[{"x":1,"y":1,"w":60,"h":60,"prob":0.9,"id":1},{"x":300,"y":1,"w":90,"h":90,"prob":0.9,"id":2}]})").det;
        assert(q.next(two, 0) == 2); q.sent(2, 0); q.answered(2, nullptr, 1);
        assert(q.next(two, 2) == 1); q.sent(1, 2); q.answered(1, nullptr, 3);    // first ask of 1 before re-ask of 2
        assert(q.next(two, 4) == 2); q.sent(2, 4); q.answered(2, nullptr, 5);    // both 1 ask: the larger
        assert(q.next(two, 6) == 1);                                            // 1 has fewer asks now
        // busy still backs off a re-ask
        q.sent(1, 6); q.answered(1, "busy", 7);
        assert(q.next(two, 8) == -1 && q.next(two, 7 + M12WhoPolicy::kFirstBackoffUs) >= 0);
        // reset in place: everything forgotten
        q.reset();
        assert(q.asks(1) == 0 && q.asks(2) == 0 && q.busy() == 0 && q.inflight() == -1 && q.next(two, 0) == 2);
    }
    // --- 2026-11-15 crowd tap look: every unnamed face now, largest first, then settled ---
    {
        constexpr int64_t S = 1000000;
        M12WhoPolicy w;
        auto three = parse(R"({"f":1,"num":3,"face":[{"x":1,"y":1,"w":50,"h":50,"prob":0.9,"id":1,"gender":"m","age_group":"adult"},)"
                           R"({"x":200,"y":1,"w":90,"h":90,"prob":0.9,"id":2,"gender":"f","age_group":"young"},)"
                           R"({"x":400,"y":1,"w":70,"h":70,"prob":0.9,"id":3,"gender":"m","age_group":"child","name":"person-a"}]})").det;
        // both unnamed were already asked to the end (6 asks): no automatic ask now
        for (int k = 0; k < 6; ++k) for (int id : {2, 1}) { assert(w.next(three, 0) == id); w.sent(id, 0); w.answered(id, nullptr, 0); }
        assert(w.next(three, 0) == -1);
        w.look(three, 1 * S - 100000, 1 * S);                                 // line 0.1 s old: in view
        assert(w.look_active(1 * S) && !w.look_settled(three, 1 * S, true));
        assert(w.next(three, 1 * S) == 2);                                    // largest unnamed first
        w.sent(2, 1 * S);
        assert(w.next(three, 1 * S + 1) == -1);                               // still one in flight
        w.answered(2, nullptr, 1 * S + 200000);
        assert(w.next(three, 1 * S + 200000) == 1);
        w.sent(1, 1 * S + 200000);
        w.answered(1, nullptr, 1 * S + 400000, true);                         // named: wait for the name on a line
        assert(!w.look_settled(three, 1 * S + 400000, true));
        auto named = parse(R"({"f":2,"num":3,"face":[{"x":1,"y":1,"w":50,"h":50,"prob":0.9,"id":1,"gender":"m","age_group":"adult","name":"person-b"},)"
                           R"({"x":200,"y":1,"w":90,"h":90,"prob":0.9,"id":2,"gender":"f","age_group":"young"},)"
                           R"({"x":400,"y":1,"w":70,"h":70,"prob":0.9,"id":3,"gender":"m","age_group":"child","name":"person-a"}]})").det;
        assert(w.look_settled(named, 1 * S + 450000, true));
        assert(w.asks(2) == 6 && w.look_extra() == 2);                       // look asks are extra
        w.look_end();
        assert(!w.look_active(1 * S + 500000) && w.look_settled(named, 1 * S + 500000, true));
        // a stale line (> 0.3 s) gives the look no targets
        M12WhoPolicy st;
        auto fresh = parse(R"({"f":6,"num":1,"face":[{"x":1,"y":1,"w":80,"h":80,"prob":0.9,"id":20,"gender":"m"}]})").det;
        st.next(fresh, 0); st.sent(20, 0); st.answered(20, nullptr, 0);
        for (int k = 0; k < 5; ++k) { st.next(fresh, 0); st.sent(20, 0); st.answered(20, nullptr, 0); }
        st.look(fresh, 0, M12WhoPolicy::kLookLineUs + 1);
        assert(st.next(fresh, M12WhoPolicy::kLookLineUs + 1) == -1 && st.look_settled(fresh, M12WhoPolicy::kLookLineUs + 1, true));
        // attributes: a face without gender keeps the look open (when attributes are on), until the deadline
        M12WhoPolicy a;
        auto bare = parse(R"({"f":3,"num":1,"face":[{"x":1,"y":1,"w":80,"h":80,"prob":0.9,"id":8,"name":"person-a"}]})").det;
        a.look(bare, 0, 0);
        assert(a.next(bare, 0) == -1);                                         // named: nothing to ask
        assert(!a.look_settled(bare, 100000, true) && a.look_settled(bare, 100000, false));
        assert(a.look_settled(bare, M12WhoPolicy::kLookUs, true));            // 0.8 s: done whatever is missing
        // a target that left the view counts as done; busy backoff is kept during a look
        M12WhoPolicy b;
        auto one = parse(R"({"f":4,"num":1,"face":[{"x":1,"y":1,"w":80,"h":80,"prob":0.9,"id":4,"gender":"f"}]})").det;
        b.look(one, 0, 0);
        assert(b.next(one, 0) == 4);
        b.sent(4, 0); b.answered(4, "busy", 10000);
        assert(b.next(one, 20000) == -1 && b.next(one, 10000 + M12WhoPolicy::kFirstBackoffUs) == 4);
        assert(b.asks(4) == 0);                                                // busy is not an ask
        auto gone = parse(R"({"f":5,"num":0,"face":[]})").det;
        assert(b.look_settled(gone, 600000, true));
    }
    // --- crowd tap: the tracker sends face_attrs again for faces seen within 0.5 s ---
    {
        M12ChangeTracker tr;
        std::vector<M12VisionEvent> ev;
        auto emit = [&](const M12VisionEvent& e) { ev.push_back(e); };
        tr.detection(parse(R"({"f":1,"num":2,"face":[{"x":10,"y":10,"w":100,"h":120,"prob":0.95,"id":4,"gender":"f","age_group":"adult"},)"
                           R"({"x":300,"y":10,"w":60,"h":60,"prob":0.9,"id":5}]})").det, 0, emit);
        tr.detection(parse(R"({"f":2,"num":1,"face":[{"x":20,"y":12,"w":110,"h":130,"prob":0.95,"id":4,"gender":"f","age_group":"adult"}]})").det, 400000, emit);
        ev.clear();
        assert(tr.refresh(800000, emit) == 1);                                 // 5 has no attributes; 4 seen 0.4 s ago
        assert(ev.size() == 1 && ev[0].kind == M12VisionKind::face_attrs && ev[0].face == 4 && ev[0].w == 110 && ev[0].gender == 'f');
        assert(ev[0].age_group == M12AgeGroup::adult);
        ev.clear();
        assert(tr.refresh(1000000, emit) == 0 && ev.empty());                  // older than 0.5 s: not "in view" now
    }
    // --- tracking target ---
    {
        auto two = parse(R"({"f":3,"num":2,"face":[{"x":10,"y":10,"w":100,"h":100,"prob":0.99,"id":1},{"x":300,"y":10,"w":40,"h":40,"prob":0.9,"id":2}]})").det;
        M12Target t = m12_track_target(two, {});
        assert(t.valid && !t.person && t.id == 1);                           // largest first
        M12Target stick; stick.valid = true; stick.id = 2;
        assert(m12_track_target(two, stick).id == 2 && m12_track_target(two, stick).x == 300);   // same person
        auto body = parse(R"({"f":4,"num":0,"face":[],"person":[{"x":200,"y":50,"w":120,"h":400,"prob":0.9,"id":18,"face":-1}]})").det;
        auto tb = m12_track_target(body, t);
        assert(tb.valid && tb.person && tb.id == 18 && tb.x == 200 && tb.w == 120 && tb.h == 60);
        assert(!m12_track_target(parse(R"({"f":5,"num":0,"face":[]})").det, tb).valid);
    }
    // --- vision change events ---
    {
        M12ChangeTracker tr;
        std::vector<M12VisionEvent> ev;
        auto emit = [&](const M12VisionEvent& e) { ev.push_back(e); };
        auto frame = [&](const char* json) { return parse(json).det; };
        tr.detection(frame(R"({"f":1,"num":1,"face":[{"x":10,"y":10,"w":100,"h":120,"prob":0.95,"id":4}],"person":[{"x":0,"y":0,"w":300,"h":400,"prob":0.9,"id":18,"face":4}]})"), 0, emit);
        assert(ev.size() == 2 && ev[0].kind == M12VisionKind::face_found && ev[0].face == 4 && ev[0].w == 100);
        assert(ev[1].kind == M12VisionKind::person_found && ev[1].person == 18 && ev[1].face == 4);
        ev.clear();
        // same ids again: nothing new; attributes and a name arrive later
        tr.detection(frame(R"({"f":2,"num":1,"face":[{"x":12,"y":10,"w":100,"h":120,"prob":0.95,"id":4,"gender":"f","age":34,"age_group":"adult"}]})"), 500000, emit);
        assert(ev.size() == 1 && ev[0].kind == M12VisionKind::face_attrs && ev[0].gender == 'f' && ev[0].age == 34);   // reported at once
        ev.clear();
        tr.detection(frame(R"({"f":3,"num":1,"face":[{"x":12,"y":10,"w":100,"h":120,"prob":0.95,"id":4,"gender":"f","age_group":"adult","name":"person-a"}]})"), 600000, emit);
        assert(ev.size() == 1 && ev[0].kind == M12VisionKind::face_named && !std::strcmp(ev[0].name, "person-a"));
        tr.detection(frame(R"({"f":4,"num":1,"face":[{"x":12,"y":10,"w":100,"h":120,"prob":0.95,"id":4,"gender":"f","age_group":"adult","name":"person-a"}]})"), 700000, emit);
        assert(ev.size() == 1);                                          // same name and attributes: not repeated
        ev.clear();
        // the person box was last seen at t=0 → lost after 1.5 s; the face keeps going
        tr.tick(1400000, emit); assert(ev.empty());
        tr.tick(1500000, emit);
        assert(ev.size() == 1 && ev[0].kind == M12VisionKind::person_lost && !std::strcmp(ev[0].reason, "timeout"));
        ev.clear();
        tr.tick(2200000, emit);                                          // face last seen at 0.7 s
        assert(ev.size() == 1 && ev[0].kind == M12VisionKind::face_lost && ev[0].face == 4);
        assert(!std::strcmp(ev[0].name, "person-a") && ev[0].gender == 'f' && ev[0].x == 12);
        assert(tr.faces_in_view() == 0 && tr.persons_in_view() == 0);
        ev.clear();
        // vision off loses everything at once
        tr.detection(frame(R"({"f":9,"num":2,"face":[{"x":1,"y":1,"w":20,"h":20,"prob":0.9,"id":7},{"x":100,"y":1,"w":20,"h":20,"prob":0.9,"id":8}]})"), 3000000, emit);
        ev.clear();
        tr.clear(3100000, "vision_off", emit);
        assert(ev.size() == 2 && ev[0].kind == M12VisionKind::face_lost && !std::strcmp(ev[1].reason, "vision_off"));
        // more ids than slots: the stalest track is evicted (lost) first
        ev.clear();
        for (int i = 0; i < 9; ++i) {
            std::string j = R"({"f":20,"num":1,"face":[{"x":1,"y":1,"w":20,"h":20,"prob":0.9,"id":)" + std::to_string(100 + i) + "}]}";
            tr.detection(frame(j.c_str()), 4000000 + i, emit);
        }
        size_t evicted = 0; for (const auto& e : ev) evicted += e.kind == M12VisionKind::face_lost && !std::strcmp(e.reason, "evicted");
        assert(evicted == 1 && tr.faces_in_view() == M12ChangeTracker::kFaces);
        assert(!std::strcmp(m12_vision_kind_name(M12VisionKind::m12_offline), "m12_offline"));
    }
    // --- vision uplink endpoint ---
    assert(collector::vision_url("ws://10.0.0.2:52732/ear") == "ws://10.0.0.2:52732/vision");
    assert(collector::vision_url("ws://10.0.0.2:52732") == "ws://10.0.0.2:52732/vision");
    assert(collector::vision_url("wss://10.0.0.2/a/b") == "wss://10.0.0.2/vision");
    assert(collector::vision_url("http://10.0.0.2/") == "");
    // --- explicit modes and screen mapping ---
    {
        uint32_t r = next_m12_request(0, M12Function::detect);
        assert(m12_requested_function(r) == M12Function::detect && m12_requested_view(r));
        r = next_m12_request(r, M12Function::standby);
        assert(m12_requested_function(r) == M12Function::standby && !m12_requested_view(r) && (r >> 2) == 2);
        M12Face f; f.x = 0; f.y = 0; f.w = 640; f.h = 480;
        auto b = m12_to_screen(f);
        assert(b.x == 0 && b.y == 0 && b.w == 320 && b.h == 240);
    }
    std::cout << "m12 protocol tests passed\n";
}
