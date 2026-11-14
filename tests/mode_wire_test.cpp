// hello modes step 1 (collector-0.4.92): the /mouth mode wire on the robot side. mind owns the mode; the robot
// only applies snapshots (`mode`), shows results (`mode_result`) and sends inputs (`mode_input`) and acks
// (`mode_applied`). Rulings: apply on seq != current (mind's seq can go backwards after a restart), always apply
// the first snapshot after each handshake, equal seq = no-op, a malformed snapshot never changes state, result
// and snapshot in either order, every input carries seq + age_ms.
#include "mode_wire.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
using namespace collector::modes;
using namespace ArduinoJson;

namespace {
JsonDocument doc;
JsonObjectConst json(const char* text)
{
    doc.clear();
    const auto err = deserializeJson(doc, text);
    assert(!err);
    return doc.as<JsonObjectConst>();
}
const char* kChat = R"({"type":"mode","seq":3,"mode":"chat","dir":null,"host":null,"return_to":null,"billing":true,
  "since":1763100000000,"by":"asr",
  "look":{"eye":"v05","pool":null,"label":"CHAT","color":"F0B000"},"touch":{"tap":"server","double":"server"}})";
const char* kIdle1 = R"({"seq":1,"mode":"idle","billing":false,
  "look":{"eye":"page_idle","pool":null,"label":"IDLE","color":"505050"},"touch":{"tap":"badge","double":"server"}})";

// A valid snapshot with one field replaced, to check that each broken field alone makes it Bad.
std::string chat_with(const char* from, const char* to)
{
    std::string s = kChat;
    const size_t at = s.find(from);
    assert(at != std::string::npos);
    s.replace(at, std::strlen(from), to);
    return s;
}

void snapshot_fields()
{
    Snapshot s{};
    assert(parse_snapshot(json(kChat), s, false) == Parse::Applied);
    assert(s.seq == 3 && !std::strcmp(s.mode, "chat") && !std::strcmp(s.dir, "") && !s.hosted && s.billing);
    assert(s.eye == Eye::V05 && s.pool == Pool::Default && !std::strcmp(s.label, "CHAT") && s.color == 0xF0B000);
    assert(s.tap == TouchAct::Server && s.dbl == TouchAct::Server);

    Snapshot t{};
    assert(parse_snapshot(json(R"({"seq":12,"mode":"subtitle","dir":"zh_en","host":"event","billing":false,
      "look":{"eye":"page_subtitle","pool":"lively","label":"SUB ZH>EN","color":"7c6cf0"},
      "touch":{"tap":"none","double":"none"}})"), t, false) == Parse::Applied);
    assert(!std::strcmp(t.dir, "zh_en") && t.hosted && !t.billing && t.eye == Eye::PageSubtitle && t.pool == Pool::Lively);
    assert(!std::strcmp(t.label, "SUB ZH>EN") && t.color == 0x7C6CF0 && t.tap == TouchAct::None && t.dbl == TouchAct::None);

    const char* eyes[] = {"laughing_man", "v05", "v14", "v18", "page_subtitle", "page_settings", "page_idle"};
    const Eye want[] = {Eye::LaughingMan, Eye::V05, Eye::V14, Eye::V18, Eye::PageSubtitle, Eye::PageSettings, Eye::PageIdle};
    for (int i = 0; i < 7; ++i) {
        Snapshot e{};
        const std::string eye = std::string("\"eye\":\"") + eyes[i] + "\"";
        assert(parse_snapshot(json(chat_with("\"eye\":\"v05\"", eye.c_str()).c_str()), e, false) == Parse::Applied);
        assert(e.eye == want[i]);
    }
    Snapshot b{};
    assert(parse_snapshot(json(chat_with("\"tap\":\"server\"", "\"tap\":\"badge\"").c_str()), b, false) == Parse::Applied);
    assert(b.tap == TouchAct::Badge);
}

void bad_snapshots_keep_state()
{
    const char* broken[][2] = {
        {"\"seq\":3", "\"seq\":\"3\""}, {"\"seq\":3", "\"seq\":-1"}, {"\"seq\":3", "\"seq\":3.5"},
        {"\"seq\":3", "\"seq\":4294967296"}, {"\"seq\":3,", ""},
        {"\"mode\":\"chat\"", "\"mode\":null"}, {"\"mode\":\"chat\"", "\"mode\":\"\""}, {"\"mode\":\"chat\"", "\"mode\":7"},
        {"\"mode\":\"chat\"", "\"mode\":\"a_very_long_mode\""}, {"\"mode\":\"chat\"", "\"mode\":\"Chat!\""},
        {"\"dir\":null", "\"dir\":3"}, {"\"dir\":null", "\"dir\":\"zh_en_long\""},
        {"\"host\":null", "\"host\":true"},
        {"\"billing\":true", "\"billing\":1"}, {"\"billing\":true,", ""}, {"\"billing\":true", "\"billing\":null"},
        {"\"eye\":\"v05\"", "\"eye\":\"v99\""}, {"\"eye\":\"v05\"", "\"eye\":null"}, {"\"eye\":\"v05\",", ""},
        {"\"pool\":null", "\"pool\":\"calm\""}, {"\"pool\":null", "\"pool\":1"},
        {"\"label\":\"CHAT\"", "\"label\":\"\""}, {"\"label\":\"CHAT\"", "\"label\":\"ELEVEN CHAR\""},
        {"\"label\":\"CHAT\"", "\"label\":\"\xE8\x81\x8A\""}, {"\"label\":\"CHAT\"", "\"label\":\"A\\nB\""},
        {"\"label\":\"CHAT\"", "\"label\":5"},
        {"\"color\":\"F0B000\"", "\"color\":\"F0B00\""}, {"\"color\":\"F0B000\"", "\"color\":\"F0B0G0\""},
        {"\"color\":\"F0B000\"", "\"color\":15773696"}, {"\"color\":\"F0B000\"", "\"color\":\"#F0B000\""},
        {"\"tap\":\"server\"", "\"tap\":\"local\""}, {"\"double\":\"server\"", "\"double\":null"},
        {"\"look\":{", "\"look\":7,\"x\":{"}, {"\"touch\":{", "\"touch\":[],\"x\":{"},
    };
    Snapshot s{};
    assert(parse_snapshot(json(kIdle1), s, false) == Parse::Applied && s.seq == 1);
    const Snapshot before = s;
    for (const auto& b : broken) {
        const std::string text = chat_with(b[0], b[1]);
        for (bool first : {false, true}) {
            if (parse_snapshot(json(text.c_str()), s, first) != Parse::Bad) {
                std::fprintf(stderr, "not bad: %s\n", text.c_str());
                assert(false);
            }
            assert(!std::memcmp(&s, &before, sizeof s));      // a bad snapshot never changes state
        }
    }
    // Not an object at all.
    doc.clear(); deserializeJson(doc, "[1,2]");
    assert(parse_snapshot(doc.as<JsonObjectConst>(), s, true) == Parse::Bad && !std::memcmp(&s, &before, sizeof s));
    assert(parse_snapshot(JsonObjectConst(), s, true) == Parse::Bad && !std::memcmp(&s, &before, sizeof s));
}

void seq_rules()
{
    Snapshot s{};
    assert(parse_snapshot(json(kChat), s, false) == Parse::Applied && s.seq == 3);
    assert(parse_snapshot(json(kChat), s, false) == Parse::Same && s.seq == 3);   // 60 s re-push: not bad
    // mind restarted after a DB failure: its seq went back to 1, the robot must follow (ruling 1: seq != current).
    assert(parse_snapshot(json(kIdle1), s, false) == Parse::Applied && s.seq == 1 && s.eye == Eye::PageIdle && !s.billing);
    assert(parse_snapshot(json(kIdle1), s, false) == Parse::Same);
    // The first snapshot after a handshake is applied even with the same seq.
    assert(parse_snapshot(json(kIdle1), s, true) == Parse::Applied && s.seq == 1);
    // A Same does not look at the rest: the stored look stays as applied.
    assert(parse_snapshot(json(kChat), s, false) == Parse::Applied && s.eye == Eye::V05);
}

void gate_per_connection()
{
    SnapshotGate g;
    assert(!g.have());
    // Before any handshake nothing is accepted as "first", but a valid snapshot still applies (seq 3 != 0).
    g.handshake();
    assert(g.offer(json(kIdle1)) == Parse::Applied && g.have() && g.current().seq == 1);
    assert(g.offer(json(kIdle1)) == Parse::Same);                // ear may send it twice right after the hello
    // Reconnect: same mind, same seq -> the first snapshot is applied again (screen re-synced, ack re-sent).
    g.handshake();
    assert(g.offer(json(kIdle1)) == Parse::Applied);
    assert(g.offer(json(kIdle1)) == Parse::Same);
    // Reconnect, first snapshot is bad: the flag stays until a good one arrives.
    g.handshake();
    assert(g.offer(json(chat_with("\"eye\":\"v05\"", "\"eye\":\"v99\"").c_str())) == Parse::Bad);
    assert(g.current().seq == 1 && g.bad() == 1);
    assert(g.offer(json(kIdle1)) == Parse::Applied);
    assert(g.offer(json(kIdle1)) == Parse::Same);
    // seq 0 can be applied as the very first snapshot (Snapshot{} also has seq 0).
    SnapshotGate z;
    z.handshake();
    assert(z.offer(json(chat_with("\"seq\":3", "\"seq\":0").c_str())) == Parse::Applied && z.have() && z.current().seq == 0);
}

void results()
{
    Result r{};
    assert(parse_result(json(R"({"type":"mode_result","seq":7,"code":"ok","toast":"SAYING"})"), r));
    assert(r.seq == 7 && !std::strcmp(r.code, "ok") && !std::strcmp(r.toast, "SAYING"));
    assert(parse_result(json(R"({"type":"mode_result","seq":8,"code":"stale","toast":""})"), r) && !std::strcmp(r.toast, ""));
    assert(parse_result(json(R"({"seq":9,"code":"busy"})"), r) && r.seq == 9 && !std::strcmp(r.toast, ""));
    assert(parse_result(json(R"({"seq":9,"code":"undefined_preset","toast":"12345678901234567890123456789012"})"), r));
    const Result keep = r;
    for (const char* bad : {R"({"code":"ok","toast":"X"})", R"({"seq":"1","code":"ok"})", R"({"seq":1})",
                            R"({"seq":1,"code":""})", R"({"seq":1,"code":"a_code_longer_than_19"})",
                            R"({"seq":1,"code":"ok","toast":"123456789012345678901234567890123"})",
                            R"({"seq":1,"code":"ok","toast":7})", R"({"seq":1,"code":"ok","toast":"é"})"}) {
        assert(!parse_result(json(bad), r));
        assert(!std::memcmp(&r, &keep, sizeof r));
    }
}

void either_order()
{
    // Result first, then the snapshot; and the snapshot first, then the result: same end state.
    const char* res = R"({"type":"mode_result","seq":5,"code":"ok","toast":"CHAT"})";
    SnapshotGate a, b; Result ra{}, rb{};
    a.handshake(); b.handshake();
    assert(a.offer(json(kIdle1)) == Parse::Applied && b.offer(json(kIdle1)) == Parse::Applied);
    assert(parse_result(json(res), ra)); assert(a.offer(json(kChat)) == Parse::Applied);
    assert(b.offer(json(kChat)) == Parse::Applied); assert(parse_result(json(res), rb));
    assert(!std::memcmp(&a.current(), &b.current(), sizeof(Snapshot)) && !std::memcmp(&ra, &rb, sizeof ra));
}

void builders()
{
    Line l;
    assert(mode_input(l, 7, 40, "asr", "play", -1));
    assert(l.view() == R"({"type":"mode_input","seq":7,"age_ms":40,"source":"asr","input":"play","value":null})");
    assert(std::strlen(l.c_str()) == l.size());
    assert(mode_input(l, 4294967295u, 4294967295u, "touch", "double_tap", -1));
    assert(l.view() == R"({"type":"mode_input","seq":4294967295,"age_ms":4294967295,"source":"touch","input":"double_tap","value":null})");
    assert(mode_input(l, 8, 0, "asr", "digit", 9));
    assert(l.view() == R"({"type":"mode_input","seq":8,"age_ms":0,"source":"asr","input":"digit","value":9})");
    assert(mode_input_select(l, 9, 12, "select", "subtitle", "en_zh"));
    assert(l.view() == R"({"type":"mode_input","seq":9,"age_ms":12,"source":"select","input":"select","value":{"mode":"subtitle","dir":"en_zh"}})");
    assert(mode_input_select(l, 10, 0, "quick", "idle", nullptr));
    assert(l.view() == R"({"type":"mode_input","seq":10,"age_ms":0,"source":"quick","input":"select","value":{"mode":"idle"}})");
    assert(mode_input_select(l, 11, 0, "select", "talk", ""));
    assert(l.view() == R"({"type":"mode_input","seq":11,"age_ms":0,"source":"select","input":"select","value":{"mode":"talk"}})");
    assert(mode_applied(l, 12));
    assert(l.view() == R"({"type":"mode_applied","seq":12})");
    // Everything goes out as JSON the ear can read.
    JsonDocument d;
    assert(mode_input_select(l, 13, 5, "select", "subtitle", "zh_en") && !deserializeJson(d, l.c_str(), l.size()));
    assert(d["value"]["mode"] == "subtitle" && d["age_ms"] == 5);
    // Tokens that would need escaping or do not fit are refused; the line is left empty.
    for (const char* bad : {"", "pl\"ay", "Play", "a b", "much_too_long_input_word", static_cast<const char*>(nullptr)}) {
        assert(!mode_input(l, 1, 0, "asr", bad, -1) && l.size() == 0 && !std::strcmp(l.c_str(), ""));
        assert(!mode_input(l, 1, 0, bad, "play", -1) && l.size() == 0);
        assert(!mode_input_select(l, 1, 0, "select", bad, nullptr) && l.size() == 0);
    }
    assert(!mode_input_select(l, 1, 0, "select", "subtitle", "zh\"en") && l.size() == 0);
}

void queue()
{
    constexpr int64_t MS = 1000;
    Input slots[kQueueCap];
    InputQueue q(slots, kQueueCap);
    assert(q.empty() && q.size() == 0 && q.capacity() == 8);
    Line l;
    assert(!q.front_line(0, l));
    // seq is the queue's own counter: 1, 2, 3 ... unique for the boot.
    for (int i = 1; i <= 10; ++i) assert(q.push(i * MS, "asr", "play") == uint32_t(i));
    assert(q.size() == 8 && q.dropped() == 2);                   // full: the oldest two went
    assert(q.front_line(10 * MS + 40 * MS, l));                  // age = now - when it was queued
    assert(l.view() == R"({"type":"mode_input","seq":3,"age_ms":47,"source":"asr","input":"play","value":null})");
    q.pop();
    assert(q.front_line(4 * MS, l) && l.view().find("\"seq\":4,\"age_ms\":0,") != std::string_view::npos);
    assert(q.front_line(3 * MS, l) && l.view().find("\"age_ms\":0,") != std::string_view::npos);   // clock never negative
    while (!q.empty()) q.pop();
    q.pop();                                                     // pop on empty is harmless
    assert(q.size() == 0 && q.dropped() == 2);
    // Values and select survive the queue.
    assert(q.push(0, "asr", "digit", 3) == 11);
    assert(q.push_select(0, "select", "subtitle", "zh_en") == 12);
    assert(q.push_select(0, "quick", "idle") == 13);
    assert(q.front_line(5 * MS, l) && l.view() == R"({"type":"mode_input","seq":11,"age_ms":5,"source":"asr","input":"digit","value":3})");
    q.pop();
    assert(q.front_line(5 * MS, l) && l.view() == R"({"type":"mode_input","seq":12,"age_ms":5,"source":"select","input":"select","value":{"mode":"subtitle","dir":"zh_en"}})");
    q.pop();
    assert(q.front_line(5 * MS, l) && l.view() == R"({"type":"mode_input","seq":13,"age_ms":5,"source":"quick","input":"select","value":{"mode":"idle"}})");
    // Bad tokens are not queued and use no seq.
    assert(q.push(0, "asr", "Pl ay") == 0 && q.push_select(0, "select", "") == 0 && q.size() == 1);
    assert(q.push(0, "asr", "play") == 14);
    // A disconnect clears the queue; seq keeps counting (mind dedups on device + boot + seq).
    q.clear();
    assert(q.empty() && q.dropped() == 2 && !q.front_line(0, l));
    assert(q.push(0, "touch", "tap") == 15);
    // Ages beyond uint32 ms are capped, not wrapped.
    q.clear(); q.push(0, "asr", "play");
    assert(q.front_line(int64_t(1) << 50, l) && l.view().find("\"age_ms\":4294967295,") != std::string_view::npos);
    // A queue built over fewer slots is capped by them; no capacity above kQueueCap.
    Input two[2]; InputQueue small(two, 2);
    small.push(0, "asr", "play"); small.push(0, "asr", "pause"); small.push(0, "asr", "stop");
    assert(small.size() == 2 && small.dropped() == 1 && small.front_line(0, l) && l.view().find("pause") != std::string_view::npos);
    Input many[20]; InputQueue big(many, 20);
    assert(big.capacity() == kQueueCap);
}
}

int main()
{
    snapshot_fields();
    bad_snapshots_keep_state();
    seq_rules();
    gate_per_connection();
    results();
    either_order();
    builders();
    queue();
    static_assert(sizeof(Line::text) <= 256, "a line is a small fixed buffer");
    std::puts("mode wire ok");
}
