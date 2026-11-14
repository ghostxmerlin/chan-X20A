/* SPDX-License-Identifier: MIT */
#pragma once
#include "mode_rules.h"
#include "mode_wire.h"
#include "mouth_stream.h"
#include <ArduinoJson.hpp>
#include <cstdint>
#include <cstdio>
#include <cstring>
namespace collector::mouth {
// hello modes step 1b (collector-0.4.92): hello owns the mode (plan 2026-11-14-hello-modes-step1b-robot-owned).
// Pure logic (no FreeRTOS, no socket), driven frame by frame by tests/mouth_modes_test.cpp. mouth_link.cpp owns
// one ModeSession in PSRAM behind its own mutex (never the audio mutex) and does the socket sends.
//
// - Every input (ASR word, home tap / double tap, the page's /v1/control {target:"mode"}, the settings timeout)
//   runs through mode_rules.h here, on the robot. A change, or an input with effects, becomes a mode_report
//   {"type":"mode_report","seq","mode","dir","host","return_to","by","input","effects"} for mind, which only
//   mirrors the mode and runs the effects (say a round, prefetch, stop speaking, talk enter / leave).
// - After each /mouth handshake (the ear's hello) one report of the current state goes first: by "boot" the
//   first time after a real boot (input "boot"), else by "link" (input null). Its effects are the mode-change
//   effects since the last report mind actually got, so a switch made while offline still turns talk's
//   prefetch off / on. Reports not sent when the link drops are dropped: that sync report replaces them.
// - Digits 0 and 9 (all paid off / on) are mind's: they go up as {"type":"mode_input",...} (C1 wire) and mind's
//   {"type":"mode_result"} brings the toast. No other input is sent to mind as an input.
// - Settings closes itself after 5 minutes without any input (the robot's clock).

// One text message from the ear. TooLong (over the 1600 B /mouth limit) and Bad (not complete JSON, or not an
// object) leave doc cleared: a partly filled document is never read. Other = a type the robot ignores (an old
// ear's {"type":"mode"} snapshot is one of them now).
enum class Text : uint8_t { TooLong, Bad, Hello, TtsStart, TtsEnd, TtsStop, ModeResult, Other };
inline Text read_text(const char* data, size_t n, ArduinoJson::JsonDocument& doc)
{
    doc.clear();
    if (n > kMaxMessage) return Text::TooLong;
    if (!data || ArduinoJson::deserializeJson(doc, data, n) || !doc.is<ArduinoJson::JsonObjectConst>()) {
        doc.clear();
        return Text::Bad;
    }
    const char* type = doc["type"] | "";
    static constexpr struct { const char* name; Text kind; } kTypes[] = {
        {"hello", Text::Hello}, {"tts.start", Text::TtsStart}, {"tts.end", Text::TtsEnd}, {"tts.stop", Text::TtsStop},
        {"mode_result", Text::ModeResult}};
    for (const auto& t : kTypes) if (!std::strcmp(type, t.name)) return t.kind;
    return Text::Other;
}

constexpr int64_t kSettingsTimeoutUs = 300LL * 1000000;   // settings with no input for 5 minutes closes
constexpr size_t kReportCap = 8;
constexpr size_t kOutMax = 320;                            // the longest mode_report is about 240 B

struct OutLine {
    char text[kOutMax] = {};
    size_t n = 0;
    const char* c_str() const { return text; }
    size_t size() const { return n; }
};

// What the screen and the main loop read (a copy, taken under the link's lock).
struct ModeView {
    bool online = false;            // /mouth connected and the ear's hello accepted: reports and digits go up
    modes::rules::State state{};
    modes::Snapshot look{};         // eye / pool / label / color / touch for state (look.seq = seq)
    uint32_t seq = 0;               // the robot's mode seq: +1 for each change and each report
    char by[8] = "boot";            // what made the last change: asr, touch, page, timeout, boot
    int64_t since_us = 0;           // when the last change happened
    char toast[modes::rules::kToastMax + 1] = {};
    uint32_t toasts = 0;            // +1 for every toast to show, even an identical text
    bool have_result = false;
    modes::Result result{};         // mind's latest mode_result (digits 0 / 9)
    uint32_t version = 0;           // changes whenever anything above changes
};

// What one input did: the rules' outcome (state unchanged for a digit 0 / 9) and where it went.
struct Applied {
    modes::rules::Outcome out;
    bool to_mind = false;           // a digit 0 / 9: sent to mind as mode_input
    uint32_t input_seq = 0;         // its mode_input seq, 0 = not queued (offline)
    bool changed = false;           // the mode state changed
    bool reported = false;          // a mode_report was queued for mind
};

class ModeSession {
public:
    using State = modes::rules::State;
    using Input = modes::rules::Input;
    using Value = modes::rules::Value;
    using Effect = modes::rules::Effect;
    using Outcome = modes::rules::Outcome;

    ModeSession() : queue_(slots_, modes::kQueueCap) {}
    ModeSession(const ModeSession&) = delete;
    ModeSession& operator=(const ModeSession&) = delete;

    // Once, at power-on: the boot input on the default state (talk). Its report goes out with the first link.
    void boot(int64_t now_us)
    {
        const Outcome o = modes::rules::transition(state_, Input::Boot, Value{}, false);
        state_ = o.state; since_us_ = last_input_us_ = now_us;
        std::strcpy(by_, "boot");
        ++seq_; ++version_;
    }

    // One input from `source` (asr, touch, page; timeout from tick()). Never blocks; the caller holds the lock.
    Applied apply(int64_t now_us, const char* source, Input in, const Value& v, bool speaking)
    {
        Applied a;
        a.out.state = state_;
        if (!modes::detail::word(source, sizeof by_)) {
            a.out = modes::rules::transition(state_, Input::Unknown, v, speaking);
            return a;
        }
        last_input_us_ = now_us;
        if (in == Input::Digit && (v.digit == 0 || v.digit == 9)) {
            a.to_mind = true;
            a.out.state = state_;
            if (online_) {
                a.input_seq = queue_.push(now_us, source, "digit", v.digit);
                char t[16];
                std::snprintf(t, sizeof t, "PRESET %d", v.digit);
                toast(t);
            } else {
                ++offline_inputs_;
                toast("OFFLINE");
            }
            std::snprintf(a.out.toast, sizeof a.out.toast, "%s", toast_);
            return a;
        }
        a.out = modes::rules::transition(state_, in, v, speaking);
        a.changed = a.out.state != state_;
        if (a.changed) {
            state_ = a.out.state; since_us_ = now_us;
            std::strcpy(by_, source);
            ++version_;
        }
        if (a.changed || a.out.n_effects) {
            if (online_) {
                ++seq_;
                push(report(seq_, source, in, true, a.out.effects, a.out.n_effects), a.out);
                a.reported = true;
            } else {
                if (a.changed) ++seq_;          // the link report sends the state; effect-only inputs are lost
                ++offline_reports_;
            }
        }
        // Offline, an input that only asks mind for something (say a round, prefetch) does nothing: say so.
        toast(!online_ && !a.changed && a.out.n_effects ? "OFFLINE" : a.out.toast);
        return a;
    }

    // Main loop, a few times a second: settings with no input for 5 minutes goes back (input timeout).
    bool tick(int64_t now_us, bool speaking)
    {
        if (state_.mode != modes::rules::Mode::Settings || now_us - last_input_us_ < kSettingsTimeoutUs) return false;
        apply(now_us, "timeout", Input::Timeout, Value{}, speaking);
        return true;
    }

    // The ear's hello was accepted: reports and digits go up from now on, the current state first.
    void link_up()
    {
        reports_n_ = 0; inflight_ok_ = false;
        queue_.clear();
        if (!online_) { online_ = true; ++version_; }
        Effect fx[modes::rules::kMaxEffects];
        const uint8_t n = mode_change(boot_sent_ ? sent_state_ : State{modes::rules::Mode::None}, fx);
        ++seq_;
        push(report(seq_, boot_sent_ ? "link" : "boot", Input::Boot, !boot_sent_, fx, n), Outcome{});
    }
    // /mouth is gone: unsent reports and queued digits are dropped (the next link sends the state again).
    void link_down()
    {
        reports_n_ = 0; inflight_ok_ = false;
        queue_.clear();
        if (online_) { online_ = false; ++version_; }
    }
    bool online() const { return online_; }

    // {"type":"mode_result",...} for a digit: its toast. false = malformed (dropped) or before hello.
    bool on_result(ArduinoJson::JsonObjectConst o)
    {
        if (!online_) { ++ignored_; return false; }
        modes::Result r;
        if (!modes::parse_result(o, r)) { ++bad_results_; return false; }
        result_ = r; have_result_ = true; ++results_; ++version_;
        if (r.toast[0]) toast(r.toast);
        return true;
    }

    // The next line to send, built now: reports first (in order), then digits (age_ms counts the time queued).
    enum class Out : uint8_t { None, Report, Input };
    Out next(int64_t now_us, OutLine& out, uint32_t& seq)
    {
        seq = 0;
        if (!online_) return Out::None;
        while (reports_n_) {
            if (report_line(reports_[0], out)) { inflight_ = reports_[0]; inflight_ok_ = true; seq = inflight_.seq; return Out::Report; }
            pop_report(); ++reports_dropped_;      // cannot happen (bounded words); never stall the queue
        }
        modes::Line line;
        while (const modes::Input* in = queue_.front()) {
            if (queue_.front_line(now_us, line)) {
                std::memcpy(out.text, line.c_str(), line.size() + 1); out.n = line.size();
                seq = in->seq;
                return Out::Input;
            }
            queue_.pop(); ++unsendable_;
        }
        return Out::None;
    }
    // The socket took the line from next(). Only that one is cleared: a collapsed queue or an input that took the
    // front after an overflow stays.
    void sent(Out kind, uint32_t seq)
    {
        if (kind == Out::Report && inflight_ok_ && inflight_.seq == seq) {
            sent_state_ = inflight_.state;
            if (inflight_.boot) boot_sent_ = true;
            inflight_ok_ = false; ++reports_sent_;
            if (reports_n_ && reports_[0].seq == seq) pop_report();
        } else if (kind == Out::Input && queue_.front() && queue_.front()->seq == seq) {
            queue_.pop(); ++inputs_sent_;
        }
    }

    void view(ModeView& v) const
    {
        v.online = online_; v.state = state_; v.look = modes::rules::look(state_, seq_); v.seq = seq_;
        std::memcpy(v.by, by_, sizeof v.by); v.since_us = since_us_;
        std::memcpy(v.toast, toast_, sizeof v.toast); v.toasts = toasts_;
        v.have_result = have_result_; v.result = result_; v.version = version_;
    }
    uint32_t version() const { return version_; }
    const State& state() const { return state_; }

    // Telemetry / info: mode (the web page's mode bar reads it).
    void json(ArduinoJson::JsonObject out, int64_t now_us) const
    {
        using namespace modes::rules;
        out["mode"] = mode_id(state_.mode);
        if (state_.dir == Dir::None) out["dir"] = nullptr; else out["dir"] = dir_id(state_.dir);
        if (state_.hosted) out["host"] = "event"; else out["host"] = nullptr;
        if (state_.return_to == Mode::None) out["return_to"] = nullptr; else out["return_to"] = mode_id(state_.return_to);
        // Char arrays go in as const char*: ArduinoJson keeps only a pointer to a const char[N] (taken for a literal).
        out["seq"] = seq_; out["label"] = label(state_); out["by"] = static_cast<const char*>(by_);
        out["since_ms"] = (now_us - since_us_) / 1000;
        out["online"] = online_;
        out["reports_sent"] = reports_sent_; out["reports_pending"] = reports_n_;
        out["reports_offline"] = offline_reports_; out["reports_collapsed"] = reports_dropped_;
        out["inputs_sent"] = inputs_sent_; out["inputs_queued"] = queue_.size();
        out["inputs_dropped"] = queue_.dropped() + unsendable_; out["inputs_offline"] = offline_inputs_;
        out["results"] = results_; out["bad_results"] = bad_results_; out["ignored"] = ignored_;
        if (have_result_) {
            auto r = out["last_result"].to<ArduinoJson::JsonObject>();
            r["seq"] = result_.seq; r["code"] = static_cast<const char*>(result_.code); r["toast"] = static_cast<const char*>(result_.toast);
        }
        out["toast"] = static_cast<const char*>(toast_);
    }

private:
    struct Report {
        uint32_t seq = 0;
        State state{};
        char by[8] = {};
        Input input = Input::Unknown;
        bool has_input = false;
        bool boot = false;
        Effect effects[modes::rules::kMaxEffects] = {};
        uint8_t n = 0;
    };
    Report report(uint32_t seq, const char* by, Input in, bool has_input, const Effect* fx, uint8_t n) const
    {
        Report r;
        r.seq = seq; r.state = state_; r.input = in; r.has_input = has_input;
        std::snprintf(r.by, sizeof r.by, "%s", by);
        r.boot = !std::strcmp(by, "boot");
        for (uint8_t i = 0; i < n && i < modes::rules::kMaxEffects; ++i) r.effects[r.n++] = fx[i];
        return r;
    }
    // A full queue (8 changes faster than the link sends) collapses into one report: the current state with the
    // mode-change effects since the last sent report, plus this input's own effects.
    void push(const Report& r, const Outcome& o)
    {
        if (reports_n_ < kReportCap) { reports_[reports_n_++] = r; return; }
        reports_dropped_ += uint32_t(reports_n_);
        Report c = r;
        // The boot report (always first after a link_up) may be among the dropped ones: then this one is it.
        const bool boot_done = boot_sent_ || (inflight_ok_ && inflight_.boot);
        if (!boot_done) { std::strcpy(c.by, "boot"); c.boot = true; }
        Effect fx[modes::rules::kMaxEffects];
        c.n = 0;
        const uint8_t n = mode_change(boot_done ? sent_state_ : State{modes::rules::Mode::None}, fx);
        for (uint8_t i = 0; i < n; ++i) c.effects[c.n++] = fx[i];
        for (uint8_t i = 0; i < o.n_effects; ++i) {
            bool dup = false;
            for (uint8_t k = 0; k < c.n; ++k) dup = dup || c.effects[k] == o.effects[i];
            if (!dup && c.n < modes::rules::kMaxEffects) c.effects[c.n++] = o.effects[i];
        }
        reports_[0] = c; reports_n_ = 1;
    }
    void pop_report()
    {
        for (size_t i = 1; i < reports_n_; ++i) reports_[i - 1] = reports_[i];
        if (reports_n_) --reports_n_;
    }
    // What mind must do to get from `from` (Mode::None = unknown, after a boot) to the current state.
    uint8_t mode_change(const State& from, Effect* fx) const
    {
        using modes::rules::Mode;
        uint8_t n = 0;
        if (from.mode == state_.mode && from.mode != Mode::None) return 0;
        fx[n++] = Effect::StopSpeech;
        if (from.mode == Mode::Talk) fx[n++] = Effect::TalkLeave;
        if (state_.mode == Mode::Talk) fx[n++] = Effect::TalkEnter;
        return n;
    }
    static bool report_line(const Report& r, OutLine& out)
    {
        using namespace modes::rules;
        auto q = [](const char* s, char* buf, size_t cap) {
            if (s) std::snprintf(buf, cap, "\"%s\"", s); else std::snprintf(buf, cap, "null");
            return buf;
        };
        char dir[12], host[12], ret[16], in[16], fx[96] = "";
        size_t at = 0;
        for (uint8_t i = 0; i < r.n; ++i) {
            const int w = std::snprintf(fx + at, sizeof fx - at, "%s\"%s\"", i ? "," : "", effect_id(r.effects[i]));
            if (w < 0 || size_t(w) >= sizeof fx - at) return false;
            at += size_t(w);
        }
        const int w = std::snprintf(out.text, sizeof out.text,
            "{\"type\":\"mode_report\",\"seq\":%lu,\"mode\":\"%s\",\"dir\":%s,\"host\":%s,\"return_to\":%s,"
            "\"by\":\"%s\",\"input\":%s,\"effects\":[%s]}",
            static_cast<unsigned long>(r.seq), mode_id(r.state.mode), q(dir_id(r.state.dir), dir, sizeof dir),
            q(r.state.hosted ? "event" : nullptr, host, sizeof host), q(mode_id(r.state.return_to), ret, sizeof ret),
            r.by, q(r.has_input ? input_id(r.input) : nullptr, in, sizeof in), fx);
        if (w < 0 || size_t(w) >= sizeof out.text) { out.text[0] = 0; out.n = 0; return false; }
        out.n = size_t(w);
        return true;
    }
    void toast(const char* t)
    {
        std::snprintf(toast_, sizeof toast_, "%s", t);
        ++toasts_; ++version_;
    }

    State state_{};
    char by_[8] = "boot";
    int64_t since_us_ = 0, last_input_us_ = 0;
    uint32_t seq_ = 0, version_ = 0;
    bool online_ = false, boot_sent_ = false;
    State sent_state_{};
    Report reports_[kReportCap];
    size_t reports_n_ = 0;
    Report inflight_{};
    bool inflight_ok_ = false;
    modes::Input slots_[modes::kQueueCap] = {};
    modes::InputQueue queue_;               // after slots_: built on them
    modes::Result result_{};
    bool have_result_ = false;
    char toast_[modes::rules::kToastMax + 1] = {};
    uint32_t toasts_ = 0, results_ = 0, bad_results_ = 0, ignored_ = 0;
    uint32_t reports_sent_ = 0, reports_dropped_ = 0, offline_reports_ = 0;
    uint32_t inputs_sent_ = 0, offline_inputs_ = 0, unsendable_ = 0;
};
}
