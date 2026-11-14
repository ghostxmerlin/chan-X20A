// hello modes step 1b (collector-0.4.92): hello owns the mode. The /mouth side of it (fw/mouth_modes.h), frame by
// frame: text messages are length-checked and fully parsed before anything reads them; every input runs through
// mode_rules.h on the robot; a change (or an input with effects) becomes a mode_report for mind; after each
// handshake the current state goes first (by boot once after a real boot, else by link, with the mode-change
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
    explicit Robot(bool booted = true) { if (booted) s.boot(0); }
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
        {R"({"type":"mode_report","seq":1})", Text::Other}, {R"({"type":7})", Text::Other}, {R"({})", Text::Other}};
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

    // A switch made before the first link: the boot report carries the state now and what mind must do.
    Robot o;
    assert(o.in(Input::Play).changed && o.view().state.mode == Mode::Chat && !std::strcmp(o.view().toast, "CHAT"));
    o.feed(kHello);
    assert(o.drain() == report(3, "chat", "boot", "\"boot\"", "\"stop_speech\""));
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
    // Back: one "link" report with what mind needs to get from chat to talk.
    r.feed(kHello);
    assert(r.drain() == report(5, "talk", "link", "null", "\"stop_speech\",\"talk_enter\""));
    // Reconnect with nothing changed: the state again, no effects.
    r.s.link_down(); r.feed(kHello);
    assert(r.drain() == report(6, "talk", "link", "null", ""));
    // Reports not sent when the link drops are replaced by the link report (from the last one mind got).
    r.in(Input::Play);
    r.in(Input::Pause);                                                      // chat -> idle
    r.s.link_down(); r.feed(kHello);
    assert(r.drain() == report(9, "idle", "link", "null", "\"stop_speech\",\"talk_leave\""));
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
    Robot r;
    // Offline, 0 / 9 cannot reach mind.
    Applied a = r.digit(9);
    assert(a.to_mind && !a.input_seq && !std::strcmp(r.view().toast, "OFFLINE"));
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
    // An empty toast (stale) shows nothing new; a bad result is dropped.
    r.feed(R"({"type":"mode_result","seq":2,"code":"stale","toast":""})");
    assert(r.view().toasts == toasts + 1 && r.view().result.seq == 2);
    r.feed(R"({"type":"mode_result","code":"ok"})");
    assert(r.view().result.seq == 2);
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
    assert(t["inputs_queued"] == 8 && t["inputs_dropped"] == 1 && t["inputs_offline"] == 1);
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

int main()
{
    text_frames();
    boot_and_first_link();
    inputs_become_reports();
    links();
    digits();
    settings_timeout();
    collapse();
    telemetry_and_versions();
    std::puts("mouth modes ok");
}
