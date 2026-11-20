/* SPDX-License-Identifier: MIT */
#pragma once
#include "chat_stage.h"
#include "mode_rules.h"
#include "big_mode.h"
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
//   first time after a real boot (input "boot"), else input null and by = what made the last change (mind only
//   takes its own source words: asr, touch, page, timeout, boot). Its effects are the mode-change
//   effects since the last report mind actually got, so a switch made while offline still turns talk's
//   prefetch off / on. Reports not sent when the link drops are dropped: that sync report replaces them.
// - Digits 0 and 9 (all paid off / on) are mind's: they go up as {"type":"mode_input",...} (C1 wire) and mind's
//   {"type":"mode_result"} brings the toast.
// - 0.4.98 (owner 2026-11-15, 能让hello在围观模式中闭嘴): digit 1 is mind's too (preset 1 = toggle the 围观 realtime
//   switch). The shared rules table is unchanged there (1 = no_action): the session routes it before the rules.
//   0.5.15: crowd's double tap no longer sends digit 1 (it is 安静一下, owner 2026-11-19).
//   Digit 1 is a toggle: offline it is not kept for later (toast OFFLINE), and it never runs the robot-half senses
//   hook (that is 0 / 9 only, mouth_link.cpp). No other input is sent to mind as an input.
// - 0.4.98 (owner 2026-11-15): crowd's tap (look) is held up to look_hold (set_look_hold, 0.8 s on the robot) so the
//   M12 can ask who / refresh attributes first (m12_look); release_look() lets it go earlier. Any later input lets it
//   go at once (never delayed behind a look), and the digit mode_inputs are never held.
// - Settings closes itself after 5 minutes without any input (the robot's clock).
// - 0.4.93: {"type":"chat_stage","stage":"listen|think|skip","at_ms":..} (<= 200 B) is kept for the chat dots
//   (chat_stage.h) only while the mode is chat; leaving chat clears it. at_ms (server clock) is not used: the
//   timeouts run on the robot's clock from arrival.

// One text message from the ear. TooLong (over the 1600 B /mouth limit) and Bad (not complete JSON, or not an
// object) leave doc cleared: a partly filled document is never read. Other = a type the robot ignores (an old
// ear's {"type":"mode"} snapshot is one of them now).
// 0.4.99: Snap = the ear's {"type":"snap","id"} (vision burst, vision_snap.h; its own 200 B limit is checked there).
// 0.5.00: Burst = the ear's {"type":"burst","on",..} (the burst indicator, burst_indicator.h; its 200 B limit there).
// 0.5.10: Cmd = the ear's {"type":"cmd","id","do","args"} (robot command line, robot_cmd.h; its 512 B limit there).
enum class Text : uint8_t { TooLong, Bad, Hello, TtsStart, TtsEnd, TtsStop, ModeResult, ChatStage, Snap, Burst, Cmd, Other };
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
        {"mode_result", Text::ModeResult}, {"chat_stage", Text::ChatStage}, {"snap", Text::Snap},
        {"burst", Text::Burst}, {"cmd", Text::Cmd}};
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
    int8_t result_digit = -1;       // 0.4.94: the digit that result answers (-1 = not one this link sent)
    int64_t result_at_us = 0;       // when it arrived (the robot's clock)
    chat::StageState chat{};        // the ear's latest chat stage (chat mode only; cleared on leaving chat)
    uint32_t version = 0;           // changes whenever anything above changes
};

// What one input did: the rules' outcome (state unchanged for a digit 0 / 9) and where it went.
struct Applied {
    modes::rules::Outcome out;
    bool to_mind = false;           // a digit 0 / 9: sent to mind as mode_input
    uint32_t input_seq = 0;         // its mode_input seq, 0 = not queued (offline)
    bool changed = false;           // the mode state changed
    bool reported = false;          // a mode_report was queued for mind
    bool look = false;              // 0.4.98: crowd's tap, its report held (look_token) until release_look or timeout
    uint32_t look_token = 0;        // the held report's seq
};

// The modes that show the stage dots: crowd (0.4.97, owner 2026-11-15; chat, the other one, is gone since 0.5.19).
inline bool dots_mode(modes::rules::Mode m) { return m == modes::rules::Mode::Crowd; }

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

    // Once, at power-on: the boot input on the default state. Its report goes out with the first link.
    // collector-0.5.08 (owner 2026-11-18): the rules' boot input itself lands in crowd (shown as Docking, the main
    // mode), as mind's modes.py does, so `start` defaults to crowd too and no longer overrides anything.
    // collector-0.4.96 (owner 2026-11-15, field data collection): `start` is the mode hello powers up in, and
    // `preset` (0 / 9, -1 = none) is the first pending digit (see pending_): sent to mind behind the boot report of
    // the first link (source quick, as the quick page's 全开) and resent until mind answers ok, so a power-on is 全开
    // on the server too and Docking works without a computer.
    void boot(int64_t now_us, modes::rules::Mode start = modes::rules::Mode::Crowd, int preset = -1)
    {
        const Outcome o = modes::rules::transition(state_, Input::Boot, Value{}, false);
        state_ = o.state; since_us_ = last_input_us_ = now_us;
        (void)start;                                   // Docking is the only mode since 0.5.19: boot lands there
        std::strcpy(by_, "boot");
        // 0.5.21: the stored big mode's digit (0 / 2 / 9, big_mode.h), sent with source boot
        if (big::is_digit(preset)) { pend(int8_t(preset), now_us); pending_.power_on = true; }
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
        if (hold_seq_) { hold_seq_ = 0; ++look_overtaken_; }   // a held look goes now: no input waits behind it
        // 0.4.98: digit 1 (preset 1, the 围观 realtime toggle) is mind's digit 1 (crowd's double tap was too, until
        // 0.5.15: it is 安静一下 now, through the rules and reported like the tap).
        if (in == Input::Digit && v.digit == kToggleDigit) {
            a.to_mind = true;
            // review 2026-11-15: the owner's latest action wins over the power-on preset still owed to mind (a 0 / 9
            // said offline by the owner stays owed: a toggle says nothing about paid senses on / off).
            if (pending_.power_on) pending_ = Pending{};
            if (online_) {
                a.input_seq = queue_.push(now_us, source, "digit", kToggleDigit);
                if (a.input_seq) { digits_[digits_at_] = {a.input_seq, int8_t(kToggleDigit)}; digits_at_ = (digits_at_ + 1) % kDigitsKept; }
                toast("PRESET 1");
            } else {
                ++offline_inputs_;                   // a toggle: replaying it later could switch it the wrong way
                toast("OFFLINE");
            }
            std::snprintf(a.out.toast, sizeof a.out.toast, "%s", toast_);
            return a;
        }
        if (in == Input::Digit && big::is_digit(v.digit)) {   // 0 / 2 / 9: the big modes (0.5.21; 2 = 收集)
            a.to_mind = true;
            a.out.state = state_;
            pending_ = Pending{};                    // the newest 0 / 9 wins over a pending one (review 2026-11-15)
            if (online_) {
                a.input_seq = queue_.push(now_us, source, "digit", v.digit);
                if (a.input_seq) { digits_[digits_at_] = {a.input_seq, int8_t(v.digit)}; digits_at_ = (digits_at_ + 1) % kDigitsKept; }
                char t[16];
                std::snprintf(t, sizeof t, "PRESET %d", v.digit);
                toast(t);
            } else {
                ++offline_inputs_;
                pend(int8_t(v.digit), now_us);       // 0.4.96: sent when the link is up (robot half is done now)
                char t[28];
                std::snprintf(t, sizeof t, "OFFLINE: %d WHEN ONLINE", v.digit);
                toast(t);
            }
            std::snprintf(a.out.toast, sizeof a.out.toast, "%s", toast_);
            return a;
        }
        a.out = modes::rules::transition(state_, in, v, speaking);
        a.changed = a.out.state != state_;
        if (a.changed) {
            if (state_.mode != a.out.state.mode) chat_.clear();   // entering or leaving chat: no stage carried over
            state_ = a.out.state; since_us_ = now_us;
            std::strcpy(by_, source);
            ++version_;
        }
        // Reported: a change, an input with effects, or any other accepted (ok) input. The last is only crowd's tap
        // ("look", 0.4.93.1): no change, no effect, but the ear's realtime session takes it from the report.
        const bool accepted = a.out.code == modes::rules::Code::Ok;
        if (a.changed || a.out.n_effects || accepted) {
            if (online_) {
                ++seq_;
                push(report(seq_, source, in, true, a.out.effects, a.out.n_effects), a.out);
                a.reported = true;
                // 0.4.98 crowd's tap: held so the greeting gets fresh faces (released by release_look or the clock).
                const bool look = false;   // 0.5.15: crowd's tap is listen / stop now, its report never held
                if (look && look_hold_us_ > 0 && reports_n_ && reports_[reports_n_ - 1].seq == seq_) {
                    hold_seq_ = seq_; hold_until_us_ = now_us + look_hold_us_;
                    a.look = true; a.look_token = seq_;
                    ++look_holds_;
                }
            } else {
                if (a.changed) ++seq_;          // the link report sends the state; effect-only inputs are lost
                ++offline_reports_;
            }
        }
        // Offline, an input that only asks mind for something (say a round, prefetch) does nothing: say so.
        toast(!online_ && !a.changed && (a.out.n_effects || accepted) ? "OFFLINE" : a.out.toast);
        return a;
    }

    // Main loop: settings used to close itself after 5 minutes; there is no settings mode since 0.5.19 (Docking only).
    bool tick(int64_t, bool) { return false; }

    // The ear's hello was accepted: reports and digits go up from now on, the current state first.
    void link_up(int64_t now_us = 0)
    {
        reports_n_ = 0; inflight_ok_ = false; hold_seq_ = 0;
        queue_.clear();
        clear_chat();                   // no chat dot survives a reconnect (review Minor 1)
        if (!online_) { online_ = true; ++version_; }
        Effect fx[modes::rules::kMaxEffects];
        const uint8_t n = mode_change(boot_sent_ ? sent_state_ : State{modes::rules::Mode::None}, fx);
        ++seq_;
        push(report(seq_, boot_sent_ ? by_ : "boot", Input::Boot, !boot_sent_, fx, n), Outcome{});
        // The pending digit (power-on 9, or a 0 / 9 said offline) goes right behind the boot report.
        if (pending_.digit >= 0) { pending_.seq = 0; pending_.due_us = now_us; queue_pending(now_us); }
    }
    // /mouth is gone: unsent reports and queued digits are dropped (the next link sends the state again).
    void link_down()
    {
        reports_n_ = 0; inflight_ok_ = false; hold_seq_ = 0;
        queue_.clear();
        clear_chat();                   // the badge says OFF: no chat dot stays lit (review Minor 1)
        if (online_) { online_ = false; ++version_; }
    }
    bool online() const { return online_; }

    // 0.4.98: how long crowd's tap report may wait for the look (0 = not held; the robot sets 0.8 s).
    // 0.5.16: the ear is running a skill / tool (stage tool, within kToolUs): the touch rules count hello as speaking.
    bool tool_busy(int64_t now_us) const
    {
        return dots_mode(state_.mode) && chat_.stage == chat::Stage::Tool && now_us - chat_.at_us < chat::kToolUs;
    }
    void set_look_hold(int64_t us) { look_hold_us_ = us > 0 ? us : 0; }
    // The look for `token` is done: its report may go now. false when it already went (time, a later input, link).
    bool release_look(uint32_t token)
    {
        if (!hold_seq_ || hold_seq_ != token) return false;
        hold_seq_ = 0; ++look_released_;
        return true;
    }
    bool holding_look() const { return hold_seq_ != 0; }

    // {"type":"mode_result",...} for a digit: its toast. false = malformed (dropped) or before hello.
    // 0.4.94: also which digit (0 / 9) it answers, from the last few sent, for the quick page's "last:" line.
    bool on_result(ArduinoJson::JsonObjectConst o, int64_t now_us = 0)
    {
        if (!online_) { ++ignored_; return false; }
        modes::Result r;
        if (!modes::parse_result(o, r)) { ++bad_results_; return false; }
        result_ = r; have_result_ = true; ++results_; ++version_;
        result_digit_ = -1; result_at_us_ = now_us;
        for (const SentDigit& d : digits_) if (d.seq && d.seq == r.seq) result_digit_ = d.digit;
        if (pending_.seq && r.seq == pending_.seq) {
            if (!std::strcmp(r.code, "ok")) pending_ = Pending{};                   // mind did it: done
            else { pending_.seq = 0; pending_.due_us = now_us + kPendingRetryUs; }  // busy / stale / mind down: again
        }
        if (r.toast[0]) toast(r.toast);
        return true;
    }

    // {"type":"chat_stage",...} (0.4.93): kept only in chat; anything but listen / think / skip / aside (0.5.04) is ignored.
    bool on_chat_stage(ArduinoJson::JsonObjectConst o, size_t bytes, int64_t now_us)
    {
        chat::Stage st;
        if (bytes > chat::kMessageMax || !o["stage"].is<const char*>() || !chat::stage_from(o["stage"].as<const char*>(), st)) {
            ++chat_bad_; return false;
        }
        if (!dots_mode(state_.mode)) { ++chat_dropped_; return false; }
        chat_.on_stage(st, now_us);
        ++chat_stages_; ++version_;
        return true;
    }

    // tts.start on /mouth (0.4.93 chat dots): when the speech really began, in chat only (review Minor 4).
    void on_speech_start(int64_t now_us)
    {
        if (!dots_mode(state_.mode)) return;
        chat_.on_speech_start(now_us);
        ++version_;
    }

    // The next line to send, built now: reports first (in order), then digits (age_ms counts the time queued).
    enum class Out : uint8_t { None, Report, Input };
    Out next(int64_t now_us, OutLine& out, uint32_t& seq)
    {
        seq = 0;
        if (!online_) return Out::None;
        last_now_us_ = now_us;
        // The pending digit: no answer 10 s after it went, or a non-ok answer (on_result set due): send it again.
        if (pending_.digit >= 0 && pending_.seq && pending_.sent_us && now_us - pending_.sent_us > kPendingRetryUs)
            pending_.seq = 0;
        if (pending_.digit >= 0 && !pending_.seq && now_us >= pending_.due_us) queue_pending(now_us);
        // 0.4.98: crowd's held tap report (and anything behind it) waits until release_look or the hold ends.
        if (hold_seq_) {
            bool queued = false;
            for (size_t i = 0; i < reports_n_; ++i) queued = queued || reports_[i].seq == hold_seq_;
            if (!queued) hold_seq_ = 0;
            else if (now_us >= hold_until_us_) { hold_seq_ = 0; ++look_timeouts_; }
        }
        while (reports_n_ && !(hold_seq_ && reports_[0].seq == hold_seq_)) {   // reports before the held one go
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
            if (pending_.seq && seq == pending_.seq) pending_.sent_us = last_now_us_;
        }
    }

    void view(ModeView& v) const
    {
        v.online = online_; v.state = state_; v.look = modes::rules::look(state_, seq_); v.seq = seq_;
        std::memcpy(v.by, by_, sizeof v.by); v.since_us = since_us_;
        std::memcpy(v.toast, toast_, sizeof v.toast); v.toasts = toasts_;
        v.have_result = have_result_; v.result = result_; v.chat = chat_; v.version = version_;
        v.result_digit = result_digit_; v.result_at_us = result_at_us_;
    }
    uint32_t version() const { return version_; }
    const State& state() const { return state_; }
    // The 0 / 9 still owed to mind (-1 = none): the power-on 9 or one said offline, until mind answers ok.
    int pending_digit() const { return pending_.digit; }

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
        auto lk = out["look_hold"].to<ArduinoJson::JsonObject>();
        lk["ms"] = look_hold_us_ / 1000; lk["held"] = look_holds_; lk["released"] = look_released_;
        lk["timeouts"] = look_timeouts_; lk["overtaken"] = look_overtaken_; lk["holding"] = hold_seq_ != 0;
        if (have_result_) {
            auto r = out["last_result"].to<ArduinoJson::JsonObject>();
            r["seq"] = result_.seq; r["code"] = static_cast<const char*>(result_.code); r["toast"] = static_cast<const char*>(result_.toast);
        }
        out["toast"] = static_cast<const char*>(toast_);
        auto c = out["chat_stage"].to<ArduinoJson::JsonObject>();
        c["stage"] = chat::stage_id(chat_.stage);
        if (chat_.at_us) c["age_ms"] = (now_us - chat_.at_us) / 1000; else c["age_ms"] = nullptr;
        c["received"] = chat_stages_; c["dropped"] = chat_dropped_; c["bad"] = chat_bad_;
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
        hold_seq_ = 0;                          // a collapsed queue sends at once
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
    void clear_chat()
    {
        if (chat_.stage == chat::Stage::Idle && !chat_.skip_until_us && !chat_.aside_until_us && !chat_.speak_from_us) return;
        chat_.clear(); ++version_;
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
        fx[n++] = Effect::StopSpeech;                    // no talk any more (0.5.19): no talk_leave / talk_enter
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
    // 0.4.96 (review 2026-11-15): the ear posts a mode_input to mind once, so the digit hello owes mind is kept here
    // until mind answers ok for its seq; no answer in 10 s or another code (busy, stale, mind down) sends it again
    // with a new seq, at most kPendingTries times (a repeated 9 / 0 is harmless). Any new 0 / 9 replaces it.
    static constexpr int64_t kPendingRetryUs = 10 * 1000000LL;
    static constexpr uint8_t kPendingTries = 30;
    struct Pending { int8_t digit = -1; uint32_t seq = 0; int64_t due_us = 0, sent_us = 0; uint8_t tries = 0; bool power_on = false; };
    Pending pending_{};
    int64_t last_now_us_ = 0;
    void pend(int8_t digit, int64_t now_us) { pending_ = Pending{}; pending_.digit = digit; pending_.due_us = now_us; }
    void queue_pending(int64_t now_us)
    {
        if (pending_.tries >= kPendingTries) { pending_ = Pending{}; return; }   // give up: say the digit again
        pending_.seq = queue_.push(now_us, pending_.power_on ? "boot" : "quick", "digit", pending_.digit);
        pending_.sent_us = 0;
        if (!pending_.seq) return;
        ++pending_.tries;
        digits_[digits_at_] = {pending_.seq, pending_.digit}; digits_at_ = (digits_at_ + 1) % kDigitsKept;
    }
    static constexpr int kToggleDigit = 1;
    int64_t look_hold_us_ = 0, hold_until_us_ = 0;
    uint32_t hold_seq_ = 0;                 // the held look report's seq, 0 = none
    uint32_t look_holds_ = 0, look_released_ = 0, look_timeouts_ = 0, look_overtaken_ = 0;
    State sent_state_{};
    Report reports_[kReportCap];
    size_t reports_n_ = 0;
    Report inflight_{};
    bool inflight_ok_ = false;
    modes::Input slots_[modes::kQueueCap] = {};
    modes::InputQueue queue_;               // after slots_: built on them
    modes::Result result_{};
    bool have_result_ = false;
    // The last digits sent (input seq -> 0 / 9); input seqs are unique per boot, so a late answer still matches.
    struct SentDigit { uint32_t seq; int8_t digit; };
    static constexpr size_t kDigitsKept = 8;
    SentDigit digits_[kDigitsKept] = {};
    size_t digits_at_ = 0;
    int8_t result_digit_ = -1;
    int64_t result_at_us_ = 0;
    char toast_[modes::rules::kToastMax + 1] = {};
    uint32_t toasts_ = 0, results_ = 0, bad_results_ = 0, ignored_ = 0;
    uint32_t reports_sent_ = 0, reports_dropped_ = 0, offline_reports_ = 0;
    uint32_t inputs_sent_ = 0, offline_inputs_ = 0, unsendable_ = 0;
    chat::StageState chat_{};
    uint32_t chat_stages_ = 0, chat_dropped_ = 0, chat_bad_ = 0;
};
}
