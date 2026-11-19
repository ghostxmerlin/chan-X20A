// hello modes step 1b (collector-0.4.92): hello owns the mode. The /mouth side of it (fw/mouth_modes.h), frame by
// frame: text messages are length-checked and fully parsed before anything reads them; every input runs through
// mode_rules.h on the robot; a change (or an input with effects) becomes a mode_report for mind; after each
// handshake the current state goes first (by boot once after a real boot, else input null and by = the last
// change's source, with the mode-change
// effects since the last report mind got); digits 0 and 9 go to mind as mode_input and mode_result brings their
// toast; settings closes after 5 minutes without input; a report stays pending until the socket took it.
#include "mouth_modes.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
using namespace collector::mouth;
using collector::modes::rules::Dir;
using collector::modes::rules::Input;
using collector::modes::rules::Mode;
using collector::modes::rules::Value;
using namespace ArduinoJson;

namespace {
constexpr int64_t kS = 1000000;
const char* kHello = R"({"type":"hello","codec":"opus","frame_ms":60,"sample_rate":24000})";

// What mouth_link.cpp does with text frames and the send loop, minus the locks and the audio.
struct Robot {
    ModeSession s;
    JsonDocument doc;
    uint32_t bad = 0;
    // Booted in talk: these tests were written for the old talk boot and drive talk's inputs from there. The real
    // boot mode (crowd = Docking, owner 2026-11-18) is pinned in default_boot_is_docking().
    explicit Robot(bool booted = true, Mode start = Mode::Talk) { if (booted) s.boot(0, start); }
    Text feed(const std::string& text)
    {
        const Text k = read_text(text.data(), text.size(), doc);
        if (k == Text::TooLong || k == Text::Bad) ++bad;
        else if (k == Text::Hello) s.link_up();
        else if (k == Text::ModeResult) s.on_result(doc.as<JsonObjectConst>());
        return k;
    }
    // One pass of the send loop with a socket that takes every line.
    std::string drain(int64_t now_us = 0)
    {
        std::string out;
        OutLine l;
        uint32_t seq;
        for (ModeSession::Out k; (k = s.next(now_us, l, seq)) != ModeSession::Out::None;) {
            out += l.c_str(); out += '\n';
            s.sent(k, seq);
        }
        return out;
    }
    Applied in(Input i, const char* source = "asr", Value v = Value{}, bool speaking = false, int64_t now = 0)
    {
        return s.apply(now, source, i, v, speaking);
    }
    Applied digit(int d, const char* source = "asr", int64_t now = 0) { Value v; v.digit = d; return in(Input::Digit, source, v, false, now); }
    ModeView view() const { ModeView v; s.view(v); return v; }
};
std::string report(uint32_t seq, const char* mode, const char* by, const char* input, const char* effects,
                   const char* dir = "null", const char* ret = "null")
{
    char b[400];
    std::snprintf(b, sizeof b, "{\"type\":\"mode_report\",\"seq\":%u,\"mode\":\"%s\",\"dir\":%s,\"host\":null,\"return_to\":%s,"
                  "\"by\":\"%s\",\"input\":%s,\"effects\":[%s]}\n", seq, mode, dir, ret, by, input, effects);
    return b;
}

void text_frames()
{
    JsonDocument doc;
    const char* ok = R"({"type":"mode_result","seq":1,"code":"ok","toast":"ALL OFF"})";
    assert(read_text(ok, std::strlen(ok), doc) == Text::ModeResult && doc["seq"] == 1);
    // Length first: 1601 B is refused even if it is valid JSON; 1600 B is parsed.
    std::string big = R"({"type":"tts.end","pad":")";
    big += std::string(kMaxMessage - big.size() - 2, 'x') + "\"}";
    assert(big.size() == kMaxMessage && read_text(big.data(), big.size(), doc) == Text::TtsEnd);
    std::string bigger = big; bigger.insert(bigger.size() - 2, "x");
    assert(read_text(bigger.data(), bigger.size(), doc) == Text::TooLong && doc.isNull());
    // A cut-off message is Bad and leaves nothing in the document (never a partly filled one).
    const std::string cut = std::string(ok).substr(0, 30);
    assert(read_text(cut.data(), cut.size(), doc) == Text::Bad && doc.isNull());
    for (const char* t : {"", "[1,2]", "\"mode\"", "42", "null", "{\"type\":\"mode\""}) {
        assert(read_text(t, std::strlen(t), doc) == Text::Bad && doc.isNull());
    }
    assert(read_text(nullptr, 0, doc) == Text::Bad);
    assert(read_text(ok, std::strlen(ok) - 1, doc) == Text::Bad);          // only n bytes count
    struct { const char* text; Text kind; } types[] = {
        {kHello, Text::Hello}, {R"({"type":"tts.start","stream":3})", Text::TtsStart},
        {R"({"type":"tts.end","stream":3})", Text::TtsEnd}, {R"({"type":"tts.stop","stream":3})", Text::TtsStop},
        {R"({"type":"mode","seq":3,"mode":"chat"})", Text::Other},          // mind no longer pushes snapshots
        {R"({"type":"mode_report","seq":1})", Text::Other}, {R"({"type":7})", Text::Other}, {R"({})", Text::Other},
        {R"({"type":"snap","id":7})", Text::Snap}, {R"({"type":"place","current":null})", Text::Other},
        {R"({"type":"cmd","id":1,"do":"status"})", Text::Cmd}, {R"({"type":"cmd_result","id":1})", Text::Other},
        {R"({"type":"burst","on":false})", Text::Burst}};
    for (const auto& t : types) assert(read_text(t.text, std::strlen(t.text), doc) == t.kind);
}

void boot_and_first_link()
{
    Robot r;
    ModeView v = r.view();
    assert(v.state.mode == Mode::Talk && v.seq == 1 && !std::strcmp(v.by, "boot") && !v.online);
    assert(!std::strcmp(v.look.label, "TALK") && v.look.eye == collector::modes::Eye::LaughingMan);
    assert(r.drain().empty());                                               // nothing goes out before the hello
    r.feed(kHello);
    assert(r.view().online);
    assert(r.drain() == report(2, "talk", "boot", "\"boot\"", "\"stop_speech\",\"talk_enter\""));
    assert(r.drain().empty());
    // The next link, nothing changed since boot: the state again, input null, by still "boot" (the last change).
    r.s.link_down(); r.feed(kHello);
    assert(r.drain() == report(3, "talk", "boot", "null", ""));
    // After a touch change, a re-link names touch.
    r.in(Input::Pause, "touch");                                              // outside chat: not_in_mode, no report
    r.in(Input::Backward, "touch");                                           // talk -> idle
    r.drain();
    r.s.link_down(); r.feed(kHello);
    assert(r.drain() == report(5, "idle", "touch", "null", ""));

    // A switch made before the first link: the boot report carries the state now and what mind must do.
    Robot o;
    assert(o.in(Input::Play).changed && o.view().state.mode == Mode::Chat && !std::strcmp(o.view().toast, "CHAT"));
    o.feed(kHello);
    assert(o.drain() == report(3, "chat", "boot", "\"boot\"", "\"stop_speech\""));
}

// 0.4.96 power-on (owner 2026-11-15): hello starts in crowd (DOCK) and owes mind a 9: it goes behind the boot report
// of every link until mind answers ok for it (review 2026-11-15: the ear posts an input once, never again); no answer
// in 10 s or a non-ok answer sends it again with a new seq; any newer 0 / 9 replaces it (offline: sent on the link).
std::string digit_line(uint32_t seq, uint32_t age, int value, const char* source = "quick")
{
    char b[160];
    std::snprintf(b, sizeof b, "{\"type\":\"mode_input\",\"seq\":%u,\"age_ms\":%u,\"source\":\"%s\",\"input\":\"digit\",\"value\":%d}\n",
                  seq, age, source, value);
    return b;
}
bool answer(Robot& r, uint32_t seq, const char* code, int64_t now)
{
    JsonDocument d;
    char t[120];
    std::snprintf(t, sizeof t, R"({"type":"mode_result","seq":%u,"code":"%s","toast":"PRESET 9"})", seq, code);
    deserializeJson(d, t);
    return r.s.on_result(d.as<JsonObjectConst>(), now);
}
void power_on_crowd_and_nine()
{
    Robot r(false);
    r.s.boot(0, Mode::Crowd, 9);
    ModeView v = r.view();
    assert(v.state.mode == Mode::Crowd && !std::strcmp(v.look.label, "DOCK") && !std::strcmp(v.by, "boot"));
    assert(r.s.pending_digit() == 9 && r.drain().empty());
    // First link lost before the send loop ran: still owed.
    r.s.link_up(10 * kS);
    r.s.link_down();
    assert(r.s.pending_digit() == 9);
    // Next link: boot report (crowd, only stop_speech: never talk_enter), then the 9 (source quick, fresh age).
    r.s.link_up(20 * kS);
    std::string out = r.drain(20 * kS + 300000);
    std::string want = report(3, "crowd", "boot", "\"boot\"", "\"stop_speech\"") + digit_line(2, 300, 9);
    if (out != want) std::printf("got:\n%swant:\n%s", out.c_str(), want.c_str());
    assert(out == want && r.s.pending_digit() == 9);          // sent, not yet answered
    // mind busy: again 10 s later (not before), with a new seq.
    assert(answer(r, 2, "busy", 21 * kS) && r.s.pending_digit() == 9);
    assert(r.drain(30 * kS).empty());
    assert(r.drain(31 * kS + 1) == digit_line(3, 0, 9));
    // No answer at all for 10 s: again.
    assert(r.drain(41 * kS).empty());
    assert(r.drain(41 * kS + 2) == digit_line(4, 0, 9));
    // ok: done, and the answer still names the digit for the quick page.
    assert(answer(r, 4, "ok", 42 * kS) && r.s.pending_digit() == -1 && r.view().result_digit == 9);
    assert(r.drain(60 * kS).empty());
    // Re-link: only the state, no second 9.
    r.s.link_down(); r.s.link_up(70 * kS);
    assert(r.drain(70 * kS) == report(4, "crowd", "boot", "null", ""));
    // A digit 5 (crowd) at once: already there.
    assert(r.digit(5).out.code == collector::modes::rules::Code::Already);

    // The owner's 0 before the first link replaces the power-on 9 and goes up on the link instead.
    Robot z(false);
    z.s.boot(0, Mode::Crowd, 9);
    const Applied a0 = z.digit(0, "asr", 1 * kS);
    assert(a0.to_mind && !a0.input_seq && z.s.pending_digit() == 0 && !std::strcmp(z.view().toast, "OFFLINE: 0 WHEN ONLINE"));
    z.s.link_up(5 * kS);
    assert(z.drain(5 * kS) == report(2, "crowd", "boot", "\"boot\"", "\"stop_speech\"") + digit_line(1, 0, 0));
    // Online, a 9 said while that 0 is unanswered replaces it: the late answer for the 0 changes nothing.
    const Applied a9 = z.digit(9, "asr", 6 * kS);
    assert(a9.input_seq == 2 && z.s.pending_digit() == -1);
    assert(z.drain(6 * kS) == digit_line(2, 0, 9, "asr"));
    assert(answer(z, 1, "busy", 7 * kS) && z.drain(30 * kS).empty());

    // The default boot (no preset): crowd (Docking, owner 2026-11-18), nothing owed.
    Robot t(false);
    t.s.boot(0);
    assert(t.view().state.mode == Mode::Crowd && t.s.pending_digit() == -1);
}

// owner 2026-11-18: hello boots into crowd, shown as Docking (badge DOCK), its main mode; the first link's boot
// report is crowd with stop_speech only (never talk_enter: nothing paid starts at boot).
void default_boot_is_docking()
{
    Robot r(false);
    r.s.boot(0);
    ModeView v = r.view();
    assert(v.state.mode == Mode::Crowd && v.seq == 1 && !std::strcmp(v.by, "boot") && !v.online);
    assert(!std::strcmp(v.look.label, "DOCK") && v.look.eye == collector::modes::Eye::V14 && v.look.color == 0x30D060);
    r.feed(kHello);
    assert(r.drain() == report(2, "crowd", "boot", "\"boot\"", "\"stop_speech\""));
    assert(r.drain().empty());
}

void inputs_become_reports()
{
    Robot r;
    r.feed(kHello);
    r.drain();
    // Inside talk: tap = one round (no change, an effect); speaking: tap = stop.
    Applied a = r.in(Input::Tap, "touch");
    assert(!a.changed && a.reported && !std::strcmp(r.view().toast, "SAYING"));
    a = r.in(Input::Tap, "touch", Value{}, true);
    assert(a.reported && !std::strcmp(r.view().toast, "STOP"));
    // play -> chat; in chat, tap is not implemented: nothing for mind.
    a = r.in(Input::Play);
    assert(a.changed && a.reported && r.view().state.mode == Mode::Chat && !std::strcmp(r.view().by, "asr"));
    a = r.in(Input::Tap, "touch");
    assert(!a.changed && !a.reported && !std::strcmp(r.view().toast, "NOT YET"));
    // next outside talk: only_talk, no report.
    assert(!r.in(Input::Next).reported && !std::strcmp(r.view().toast, "ONLY IN TALK"));
    assert(r.drain() ==
           report(3, "talk", "touch", "\"tap\"", "\"say_round\"") +
           report(4, "talk", "touch", "\"tap\"", "\"stop_speech\"") +
           report(5, "chat", "asr", "\"play\"", "\"stop_speech\",\"talk_leave\""));
    // The page's select of subtitle: dir in the report; the source is whatever the caller says.
    Value sel; sel.target = Mode::Subtitle; sel.dir = Dir::EnZh;
    assert(r.in(Input::Select, "page", sel).changed);
    assert(r.drain() == report(6, "subtitle", "page", "\"select\"", "\"stop_speech\"", "\"en_zh\""));
    // A source that is not a wire word is refused and changes nothing.
    const uint32_t seq = r.view().seq;
    a = r.in(Input::Forward, "Bad Src");
    assert(a.out.code == collector::modes::rules::Code::UnknownInput && !a.reported && r.view().seq == seq);
}

void links()
{
    Robot r;
    r.feed(kHello);
    r.drain();
    r.in(Input::Play);
    r.drain();                                                               // mind knows: chat
    // Offline: changes still apply on the robot; an effect-only input says OFFLINE.
    r.s.link_down();
    assert(!r.view().online);
    assert(r.in(Input::Forward).changed);                                   // chat -> talk
    assert(!r.in(Input::Tap, "touch").reported && !std::strcmp(r.view().toast, "OFFLINE"));
    assert(r.drain().empty());
    // Back: one report (input null, by = the last change's source) with what mind needs to get from chat to talk.
    r.feed(kHello);
    assert(r.drain() == report(5, "talk", "asr", "null", "\"stop_speech\",\"talk_enter\""));
    // Reconnect with nothing changed: the state again, no effects.
    r.s.link_down(); r.feed(kHello);
    assert(r.drain() == report(6, "talk", "asr", "null", ""));
    // Reports not sent when the link drops are replaced by that report (from the last one mind got).
    r.in(Input::Play);
    r.in(Input::Pause);                                                      // chat -> idle
    r.s.link_down(); r.feed(kHello);
    assert(r.drain() == report(9, "idle", "asr", "null", "\"stop_speech\",\"talk_leave\""));
    // A send that fails keeps the report at the front.
    r.in(Input::Forward);
    OutLine l; uint32_t seq;
    assert(r.s.next(0, l, seq) == ModeSession::Out::Report && seq == 10);
    assert(r.s.next(0, l, seq) == ModeSession::Out::Report && seq == 10);
    r.s.sent(ModeSession::Out::Report, 10);
    assert(r.s.next(0, l, seq) == ModeSession::Out::None);
}

void digits()
{
    // Offline, 0 / 9 cannot reach mind now: it is owed and goes up with the next link (0.4.96).
    {
        Robot o;
        const Applied off = o.digit(9);
        assert(off.to_mind && !off.input_seq && !std::strcmp(o.view().toast, "OFFLINE: 9 WHEN ONLINE") && o.s.pending_digit() == 9);
    }
    Robot r;
    Applied a;
    r.feed(kHello);
    r.drain();
    // 0 goes to mind as mode_input; the state does not change; mind's toast replaces the local one.
    a = r.digit(0, "asr", 2 * kS);
    assert(a.to_mind && a.input_seq == 1 && !a.changed && !std::strcmp(r.view().toast, "PRESET 0"));
    assert(r.drain(2 * kS + 30000) ==
           "{\"type\":\"mode_input\",\"seq\":1,\"age_ms\":30,\"source\":\"asr\",\"input\":\"digit\",\"value\":0}\n");
    const uint32_t toasts = r.view().toasts;
    r.feed(R"({"type":"mode_result","seq":1,"code":"ok","toast":"ALL PAID OFF"})");
    ModeView v = r.view();
    assert(v.have_result && v.result.seq == 1 && !std::strcmp(v.toast, "ALL PAID OFF") && v.toasts == toasts + 1);
    assert(v.result_digit == 0);                    // 0.4.94: the quick page's "last:" line knows which digit
    // An empty toast (stale) shows nothing new; a bad result is dropped. seq 2 was never sent: digit unknown.
    r.feed(R"({"type":"mode_result","seq":2,"code":"stale","toast":""})");
    assert(r.view().toasts == toasts + 1 && r.view().result.seq == 2 && r.view().result_digit == -1);
    r.feed(R"({"type":"mode_result","code":"ok"})");
    assert(r.view().result.seq == 2);
    {
        // The quick page's 全开 sends 9 with source quick, like the ASR digit word; its answer names 9 even after
        // another digit went up, and carries the robot's arrival time.
        Applied q = r.digit(9, "quick", 3 * kS);
        assert(q.to_mind && q.input_seq == 2 && !q.changed);
        r.digit(0, "asr", 3 * kS);
        assert(r.drain(3 * kS) ==
               "{\"type\":\"mode_input\",\"seq\":2,\"age_ms\":0,\"source\":\"quick\",\"input\":\"digit\",\"value\":9}\n"
               "{\"type\":\"mode_input\",\"seq\":3,\"age_ms\":0,\"source\":\"asr\",\"input\":\"digit\",\"value\":0}\n");
        JsonDocument d;
        deserializeJson(d, R"({"type":"mode_result","seq":2,"code":"ok","toast":"PRESET 9"})");
        assert(r.s.on_result(d.as<JsonObjectConst>(), 7 * kS));
        const ModeView w = r.view();
        assert(w.result_digit == 9 && w.result_at_us == 7 * kS && !std::strcmp(w.result.toast, "PRESET 9"));
        deserializeJson(d, R"({"type":"mode_result","seq":3,"code":"ok","toast":"PRESET 0"})");
        assert(r.s.on_result(d.as<JsonObjectConst>(), 8 * kS) && r.view().result_digit == 0);
    }
    // 4-8 are the robot's own: select through the rules, reported with input digit.
    a = r.digit(7);
    assert(!a.to_mind && a.changed && r.view().state.mode == Mode::Subtitle && r.view().state.dir == Dir::ZhEn);
    assert(!std::strcmp(r.view().toast, "SUB ZH>EN"));
    a = r.digit(2);
    assert(!a.changed && !a.reported && !std::strcmp(r.view().toast, "NO ACTION"));
    assert(r.drain() == report(3, "subtitle", "asr", "\"digit\"", "\"stop_speech\",\"talk_leave\"", "\"zh_en\""));
    // A digit queue overflow while the link is slow drops the oldest (C1 queue).
    for (int i = 0; i < 9; ++i) r.digit(9);
    JsonDocument t;
    r.s.json(t.to<JsonObject>(), 0);
    assert(t["inputs_queued"] == 8 && t["inputs_dropped"] == 1 && t["inputs_offline"] == 0);   // the offline 9 was robot o
}

void settings_timeout()
{
    Robot r;
    r.feed(kHello);
    r.drain();
    r.in(Input::Play, "asr", Value{}, false, 10 * kS);                   // chat
    r.in(Input::Open, "asr", Value{}, false, 20 * kS);                   // settings, return_to chat
    assert(r.view().state.mode == Mode::Settings && r.view().state.return_to == Mode::Chat);
    assert(!r.s.tick(20 * kS + kSettingsTimeoutUs - 1, false));
    r.in(Input::Tap, "touch", Value{}, false, 200 * kS);                 // any input restarts the 5 minutes
    assert(!r.s.tick(20 * kS + kSettingsTimeoutUs, false));
    assert(r.s.tick(200 * kS + kSettingsTimeoutUs, false));
    assert(r.view().state.mode == Mode::Chat && !std::strcmp(r.view().by, "timeout") && !std::strcmp(r.view().toast, "CHAT"));
    assert(!r.s.tick(900 * kS, false));                                    // only in settings
    const std::string out = r.drain();
    assert(out == report(3, "chat", "asr", "\"play\"", "\"stop_speech\",\"talk_leave\"") +
                  report(4, "settings", "asr", "\"open\"", "\"stop_speech\"", "null", "\"chat\"") +
                  report(5, "chat", "timeout", "\"timeout\"", "\"stop_speech\""));
}

void collapse()
{
    // Nine changes before the first report could go: the queue (boot report + 7) collapses into one, still the
    // boot report, from "unknown"; the next change queues behind it.
    Robot r;
    r.feed(kHello);
    for (int i = 0; i < 4; ++i) { r.in(Input::Play); r.in(Input::Pause); }   // chat, idle, chat, idle ...
    assert(r.view().state.mode == Mode::Idle);
    r.in(Input::Forward);                                                     // talk: the 9th report
    assert(r.drain() == report(10, "idle", "boot", "\"pause\"", "\"stop_speech\"") +
                        report(11, "talk", "asr", "\"forward\"", "\"stop_speech\",\"talk_enter\""));
    // After the boot report: collapse from the last sent state, plus the newest input's own effects.
    for (int i = 0; i < 8; ++i) r.in(Input::Tap, "touch");                   // 8 say_round reports
    r.in(Input::DoubleTap, "touch");                                          // the 9th: prefetch_on
    assert(r.drain() == report(20, "talk", "touch", "\"double_tap\"", "\"prefetch_on\""));
    JsonDocument t;
    r.s.json(t.to<JsonObject>(), 0);
    assert(t["reports_collapsed"] == 16 && t["reports_sent"] == 3 && t["reports_pending"] == 0);

    // The report in flight survives a collapse: sent() still records it, and does not pop the new one.
    Robot f;
    f.feed(kHello);
    f.drain();
    f.in(Input::Play);
    OutLine l; uint32_t seq;
    assert(f.s.next(0, l, seq) == ModeSession::Out::Report && seq == 3);
    for (int i = 0; i < 8; ++i) f.in(i % 2 ? Input::Play : Input::Pause);    // overflow while seq 3 is in flight
    f.s.sent(ModeSession::Out::Report, 3);
    // The collapse was computed from the last confirmed report (talk), so it repeats seq 3's effects: harmless.
    assert(f.drain() == report(11, "chat", "asr", "\"play\"", "\"stop_speech\",\"talk_leave\""));
    JsonDocument t2;
    f.s.json(t2.to<JsonObject>(), 0);
    assert(t2["reports_sent"] == 3 && t2["reports_collapsed"] == 8);
}

void telemetry_and_versions()
{
    Robot r;
    uint32_t v = r.view().version;
    auto bumped = [&] { const uint32_t n = r.view().version; const bool b = n != v; v = n; return b; };
    r.feed(kHello);                      assert(bumped());
    r.drain();                           assert(!bumped());   // sending changes nothing on the screen
    r.in(Input::Forward);                assert(bumped());    // a toast (SAYING)
    r.in(Input::Play);                   assert(bumped());
    r.s.link_down();                     assert(bumped());
    r.s.link_down();                     assert(!bumped());

    JsonDocument t;
    r.s.json(t.to<JsonObject>(), 5 * kS);
    r.in(Input::Open, "page");                                  // change the session after json(): the doc keeps its copy
    std::string text;
    serializeJson(t, text);
    assert(t["mode"] == "chat" && t["dir"].isNull() && t["host"].isNull() && t["return_to"].isNull());
    assert(t["by"] == "asr" && t["label"] == "CHAT" && t["online"] == false && t["toast"] == "CHAT" && t["seq"] == 4);
    assert(t["since_ms"] == 5000 && text.find("\"by\":\"asr\"") != std::string::npos);
    t.clear();
    r.s.json(t.to<JsonObject>(), 0);
    assert(t["mode"] == "settings" && t["return_to"] == "chat" && t["by"] == "page" && t["label"] == "SET");
}
}

void chat_stages()
{
    // 0.4.93: the ear's chat stage is kept only in chat mode and cleared on leaving it.
    Robot r;
    JsonDocument& doc = r.doc;
    auto stage = [&](const std::string& text, int64_t now) {
        assert(read_text(text.data(), text.size(), doc) == Text::ChatStage);
        return r.s.on_chat_stage(doc.as<JsonObjectConst>(), text.size(), now);
    };
    const std::string listen = R"({"type":"chat_stage","stage":"listen","at_ms":1763100000000})";
    assert(!stage(listen, 1 * kS));                                          // talk: dropped
    assert(r.view().chat.stage == collector::chat::Stage::Idle);
    r.in(Input::Play);                                                       // chat
    const uint32_t v0 = r.view().version;
    assert(stage(listen, 2 * kS) && r.view().chat.stage == collector::chat::Stage::Listen && r.view().chat.at_us == 2 * kS);
    assert(r.view().version != v0);
    assert(stage(R"({"type":"chat_stage","stage":"think","at_ms":1})", 3 * kS) && r.view().chat.stage == collector::chat::Stage::Think);
    assert(stage(R"({"type":"chat_stage","stage":"skip"})", 4 * kS));
    assert(r.view().chat.stage == collector::chat::Stage::Idle && r.view().chat.skip_until_us == 4 * kS + collector::chat::kSkipUs);
    // Unknown or malformed stages are ignored; so is a message over 200 B.
    for (const char* bad : {R"({"type":"chat_stage","stage":"speak"})", R"({"type":"chat_stage","stage":7})", R"({"type":"chat_stage"})"})
        assert(!stage(bad, 5 * kS));
    std::string big = R"({"type":"chat_stage","stage":"listen","pad":")" + std::string(200, 'x') + "\"}";
    assert(!stage(big, 5 * kS) && r.view().chat.stage == collector::chat::Stage::Idle);
    assert(stage(listen, 6 * kS));
    // Leaving chat clears it; a stage outside chat is dropped; back in chat it starts idle.
    const uint32_t seq = r.view().chat.seq;
    r.in(Input::Pause);                                                      // idle
    assert(r.view().chat.stage == collector::chat::Stage::Idle && r.view().chat.seq != seq);
    assert(!stage(listen, 7 * kS));
    r.in(Input::Play);
    assert(r.view().chat.stage == collector::chat::Stage::Idle);
    // A chat -> chat no-op (tap: not implemented) keeps the stage.
    assert(stage(listen, 8 * kS));
    r.in(Input::Tap, "touch");
    assert(r.view().chat.stage == collector::chat::Stage::Listen);
    JsonDocument t;
    r.s.json(t.to<JsonObject>(), 9 * kS);
    assert(t["chat_stage"]["stage"] == "listen" && t["chat_stage"]["age_ms"] == 1000);
    assert(t["chat_stage"]["received"] == 5 && t["chat_stage"]["dropped"] == 2 && t["chat_stage"]["bad"] == 4);
    // Review Minor 1: /mouth dropping (badge OFF) or coming back clears the stage, so no dot stays lit.
    uint32_t seq2 = r.view().chat.seq;
    r.s.link_down();
    assert(r.view().chat.stage == collector::chat::Stage::Idle && r.view().chat.seq != seq2);
    r.feed(kHello);
    assert(stage(R"({"type":"chat_stage","stage":"think"})", 10 * kS));
    seq2 = r.view().chat.seq;
    r.s.link_up();                                                           // a new handshake without a drop seen
    assert(r.view().chat.stage == collector::chat::Stage::Idle && r.view().chat.seq != seq2);
    // tts.start in chat records when the speech began (Minor 4); outside chat it does nothing.
    r.s.on_speech_start(11 * kS);
    assert(r.view().chat.speak_from_us == 11 * kS);
    r.in(Input::Pause);
    r.s.on_speech_start(12 * kS);
    assert(r.view().chat.speak_from_us == 0);
    // 0.4.97 (owner 2026-11-15): crowd shows the dots too; switching chat <-> crowd starts them idle.
    Robot c;
    c.digit(5);                                                              // crowd
    assert(c.view().state.mode == Mode::Crowd && collector::mouth::dots_mode(Mode::Crowd));
    auto cstage = [&](const std::string& text, int64_t now) {
        assert(read_text(text.data(), text.size(), c.doc) == Text::ChatStage);
        return c.s.on_chat_stage(c.doc.as<JsonObjectConst>(), text.size(), now);
    };
    assert(cstage(listen, 13 * kS) && c.view().chat.stage == collector::chat::Stage::Listen);
    assert(cstage(R"({"type":"chat_stage","stage":"think"})", 14 * kS) && c.view().chat.stage == collector::chat::Stage::Think);
    c.s.on_speech_start(15 * kS);
    assert(c.view().chat.speak_from_us == 15 * kS);
    c.in(Input::Play);                                                       // crowd -> chat: cleared
    assert(c.view().chat.stage == collector::chat::Stage::Idle && c.view().chat.speak_from_us == 0);
    assert(!collector::mouth::dots_mode(Mode::Talk) && !collector::mouth::dots_mode(Mode::Idle));
}

// 0.5.15 (owner 2026-11-19): crowd's tap = listen (toast LISTEN, reported with no effect: the ear opens its
// must-answer window) or, while hello speaks, stop (toast STOP, stop_speech); its double tap = 安静一下 (toast QUIET,
// stop_speech while speaking), reported too (no longer mind's digit 1). Nothing changes the mode.
void crowd_tap()
{
    Robot r;
    r.feed(kHello);
    r.drain();
    Value v; v.digit = 5;
    assert(r.in(Input::Digit, "asr", v).changed && r.view().state.mode == Mode::Crowd);
    r.drain();
    Applied a = r.in(Input::Tap, "touch");
    assert(!a.changed && a.reported && !a.out.n_effects && !a.look && !std::strcmp(r.view().toast, "LISTEN"));
    a = r.in(Input::Tap, "touch", Value{}, true);                             // while speaking: a stop
    assert(a.reported && a.out.has(collector::modes::rules::Effect::StopSpeech) && !std::strcmp(r.view().toast, "STOP"));
    assert(r.drain() == report(4, "crowd", "touch", "\"tap\"", "") + report(5, "crowd", "touch", "\"tap\"", "\"stop_speech\""));
    Applied dt = r.in(Input::DoubleTap, "touch");
    assert(!dt.to_mind && dt.reported && !dt.changed && !dt.out.n_effects && !std::strcmp(r.view().toast, "QUIET"));
    dt = r.in(Input::DoubleTap, "touch", Value{}, true);
    assert(dt.reported && dt.out.has(collector::modes::rules::Effect::StopSpeech) && !std::strcmp(r.view().toast, "QUIET"));
    assert(r.drain() == report(6, "crowd", "touch", "\"double_tap\"", "") + report(7, "crowd", "touch", "\"double_tap\"", "\"stop_speech\""));
    // Offline the tap cannot reach the ear: OFFLINE, no report.
    r.s.link_down();
    assert(!r.in(Input::Tap, "touch").reported && !std::strcmp(r.view().toast, "OFFLINE"));
}

// 0.4.98 (owner 2026-11-15, 能让hello在围观模式中闭嘴): digit 1 goes to mind like 0 / 9, but it is a toggle: offline it
// is not owed for later (toast OFFLINE, nothing on the next link), and it never replaces a pending 0 / 9. Outside
// crowd a double tap keeps its table meaning; digits 2 / 3 stay no_action.
void digit_one()
{
    Robot r;
    r.feed(kHello);
    r.drain();
    Applied a = r.digit(1, "asr", 1 * kS);
    assert(a.to_mind && a.input_seq == 1 && !a.changed && !a.reported && !std::strcmp(r.view().toast, "PRESET 1"));
    assert(r.drain(1 * kS) == digit_line(1, 0, 1, "asr"));
    JsonDocument d;
    deserializeJson(d, R"({"type":"mode_result","seq":1,"code":"ok","toast":"PRESET 1: DOCK QUIET"})");
    assert(r.s.on_result(d.as<JsonObjectConst>(), 2 * kS));
    assert(r.view().result_digit == 1 && !std::strcmp(r.view().toast, "PRESET 1: DOCK QUIET"));
    // talk: a double tap is the table's prefetch_on, not a digit
    Applied t = r.in(Input::DoubleTap, "touch");
    assert(!t.to_mind && t.reported && t.out.has(collector::modes::rules::Effect::PrefetchOn));
    assert(!r.digit(2).to_mind && !std::strcmp(r.view().toast, "NO ACTION"));
    // offline: nothing owed; the owner's 1 drops the power-on 9 still owed (review 2026-11-15: latest action wins)
    Robot o(false);
    o.s.boot(0, Mode::Crowd, 9);
    Applied off = o.digit(1, "asr", 1 * kS);
    assert(off.to_mind && !off.input_seq && !std::strcmp(o.view().toast, "OFFLINE") && o.s.pending_digit() == -1);
    Applied offd = o.digit(1, "asr", 1 * kS);
    assert(offd.to_mind && !offd.input_seq && !std::strcmp(o.view().toast, "OFFLINE"));
    o.feed(kHello);
    assert(o.drain(0) == report(2, "crowd", "boot", "\"boot\"", "\"stop_speech\""));    // no 9, no 1
    JsonDocument t2;
    o.s.json(t2.to<JsonObject>(), 0);
    assert(t2["inputs_offline"] == 2);
    // online too: the power-on 9 not yet answered stops being retried once the owner says 1
    Robot p(false);
    p.s.boot(0, Mode::Crowd, 9);
    p.feed(kHello);
    assert(p.drain(0) == report(2, "crowd", "boot", "\"boot\"", "\"stop_speech\"") + digit_line(1, 0, 9));
    p.digit(1, "touch", 1 * kS);
    assert(p.s.pending_digit() == -1 && p.drain(1 * kS) == digit_line(2, 0, 1, "touch"));
    assert(p.drain(30 * kS).empty());                                        // no 9 retry
    // an owner's 0 said offline is not a power-on preset: a 1 leaves it owed
    Robot z(false);
    z.s.boot(0, Mode::Crowd, 9);
    z.digit(0, "asr", 1 * kS);
    z.digit(1, "asr", 2 * kS);
    assert(z.s.pending_digit() == 0);
}

// 0.4.98 held crowd's tap report for the M12 look; 0.5.15 (owner 2026-11-19: tap = listen / stop) never holds it,
// even with a hold set: the ear's must-answer window must open at once.
void look_hold()
{
    Robot r;
    r.s.set_look_hold(800000);
    r.feed(kHello);
    r.drain();
    r.digit(5, "asr", 0);                                                   // crowd
    assert(r.drain() == report(3, "crowd", "asr", "\"digit\"", "\"stop_speech\",\"talk_leave\""));
    Applied a = r.in(Input::Tap, "touch", Value{}, false, 10 * kS);
    assert(!a.look && a.reported && !r.s.holding_look());
    assert(r.drain(10 * kS) == report(4, "crowd", "touch", "\"tap\"", ""));
}

int main()
{
    crowd_tap();
    digit_one();
    look_hold();
    chat_stages();
    text_frames();
    boot_and_first_link();
    power_on_crowd_and_nine();
    default_boot_is_docking();
    inputs_become_reports();
    links();
    digits();
    settings_timeout();
    collapse();
    telemetry_and_versions();
    std::puts("mouth modes ok");
}
