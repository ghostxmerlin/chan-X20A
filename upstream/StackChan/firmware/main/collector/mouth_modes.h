/* SPDX-License-Identifier: MIT */
#pragma once
#include "mode_wire.h"
#include "mouth_stream.h"
#include <ArduinoJson.hpp>
#include <cstdint>
#include <cstring>
namespace collector::mouth {
// hello modes step 1 (collector-0.4.92): the mode side of /mouth, as pure logic (no FreeRTOS, no socket), so
// the host test (tests/mouth_modes_test.cpp) drives it frame by frame. mouth_link.cpp owns one ModeSession in
// PSRAM behind its own mutex (never the audio mutex) and does the socket sends.
//
// Downlink: the ear's hello -> link_up(); {"type":"mode"} -> on_mode(); {"type":"mode_result"} -> on_result().
// Uplink: next() gives the line to send (a pending mode_applied first, then the oldest mode_input), sent()
// after the socket took it. Disconnect -> link_down().

// One text message from the ear. TooLong (over the 1600 B /mouth limit) and Bad (not complete JSON, or not an
// object) leave doc cleared: a partly filled document is never read. Other = a type the robot ignores.
enum class Text : uint8_t { TooLong, Bad, Hello, TtsStart, TtsEnd, TtsStop, Mode, ModeResult, Other };
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
        {"mode", Text::Mode}, {"mode_result", Text::ModeResult}};
    for (const auto& t : kTypes) if (!std::strcmp(type, t.name)) return t.kind;
    return Text::Other;
}

// What the screen reads (a copy, taken under the link's lock).
struct ModeView {
    bool online = false;        // /mouth connected and the ear's hello accepted: inputs are taken
    bool have = false;          // a snapshot was applied since boot (snap is valid)
    modes::Snapshot snap{};
    bool have_result = false;
    modes::Result result{};     // the latest mode_result (code + toast)
    uint32_t results = 0;       // count of mode_results: changes with every new one, even an identical toast
    uint32_t version = 0;       // changes whenever anything above changes
};

class ModeSession {
public:
    ModeSession() : queue_(slots_, modes::kQueueCap) {}
    ModeSession(const ModeSession&) = delete;
    ModeSession& operator=(const ModeSession&) = delete;

    // The ear's hello was accepted on a new /mouth link: inputs are taken from now on, and the next good
    // snapshot is applied whatever its seq.
    void link_up()
    {
        gate_.handshake();
        if (!online_) { online_ = true; ++version_; }
    }
    // /mouth is gone: queued inputs are dropped (the ear answers none of them). An unsent mode_applied stays
    // pending and goes out on the next link, unless a newer snapshot is applied first.
    void link_down()
    {
        queue_.clear();
        if (online_) { online_ = false; ++version_; }
    }
    bool online() const { return online_; }

    // {"type":"mode", ...}: Applied (state replaced, mode_applied pending), Same (equal seq, nothing to do) or
    // Bad (state untouched). Before the ear's hello, a snapshot is ignored and reported as Same.
    modes::Parse on_mode(ArduinoJson::JsonObjectConst o)
    {
        if (!online_) { ++ignored_; return modes::Parse::Same; }
        const modes::Parse p = gate_.offer(o);
        if (p == modes::Parse::Applied) {
            ack_seq_ = gate_.current().seq; ack_pending_ = true;
            ++applied_; ++version_;
        } else if (p == modes::Parse::Same) {
            ++same_;
        }
        return p;
    }
    // {"type":"mode_result", ...}: kept for the screen's toast. false = malformed (dropped) or before hello.
    bool on_result(ArduinoJson::JsonObjectConst o)
    {
        if (!online_) { ++ignored_; return false; }
        modes::Result r;
        if (!modes::parse_result(o, r)) { ++bad_results_; return false; }
        result_ = r; have_result_ = true; ++results_; ++version_;
        return true;
    }

    // Inputs from the ASR task, the screen, the mode picker. Returns the input's seq, 0 = not queued (offline,
    // or not a wire word). A full queue drops its oldest input (counted).
    uint32_t push_input(int64_t now_us, const char* source, const char* input, int value)
    {
        if (!online_) { ++offline_; return 0; }
        return queue_.push(now_us, source, input, value);
    }
    uint32_t push_select(int64_t now_us, const char* source, const char* mode, const char* dir)
    {
        if (!online_) { ++offline_; return 0; }
        return queue_.push_select(now_us, source, mode, dir);
    }

    // The next line to send, built now (age_ms counts the time queued). The pending ack goes first.
    enum class Out : uint8_t { None, Ack, Input };
    Out next(int64_t now_us, modes::Line& out, uint32_t& seq)
    {
        seq = 0;
        if (!online_) return Out::None;
        if (ack_pending_ && modes::mode_applied(out, ack_seq_)) { seq = ack_seq_; return Out::Ack; }
        while (const modes::Input* in = queue_.front()) {
            if (queue_.front_line(now_us, out)) { seq = in->seq; return Out::Input; }
            queue_.pop(); ++unsendable_;     // cannot happen (words are checked on push); never stall the queue
        }
        return Out::None;
    }
    // The socket took the line from next(). Only that one is cleared: a newer ack, or an input that took the
    // front after an overflow, stays.
    void sent(Out kind, uint32_t seq)
    {
        if (kind == Out::Ack && ack_pending_ && ack_seq_ == seq) {
            ack_pending_ = false; applied_seq_ = seq; ++acks_sent_;
        } else if (kind == Out::Input && queue_.front() && queue_.front()->seq == seq) {
            queue_.pop(); ++inputs_sent_;
        }
    }

    void view(ModeView& v) const
    {
        v.online = online_; v.have = gate_.have(); v.snap = gate_.current();
        v.have_result = have_result_; v.result = result_; v.results = results_; v.version = version_;
    }
    uint32_t version() const { return version_; }

    // Telemetry: mouth.mode.
    void json(ArduinoJson::JsonObject out) const
    {
        if (gate_.have()) { out["seq"] = gate_.current().seq; out["mode"] = gate_.current().mode; }
        else { out["seq"] = nullptr; out["mode"] = nullptr; }
        if (acks_sent_) out["applied_seq"] = applied_seq_; else out["applied_seq"] = nullptr;
        out["ack_pending"] = ack_pending_;
        out["online"] = online_;
        out["applied"] = applied_; out["same_snapshots"] = same_; out["bad_snapshots"] = gate_.bad();
        out["ignored"] = ignored_;
        out["inputs_queued"] = queue_.size(); out["inputs_sent"] = inputs_sent_;
        out["inputs_dropped"] = queue_.dropped() + unsendable_; out["inputs_offline"] = offline_;
        out["last_input_seq"] = queue_.last_seq();
        out["results"] = results_; out["bad_results"] = bad_results_;
        if (have_result_) {
            auto r = out["last_result"].to<ArduinoJson::JsonObject>();
            r["seq"] = result_.seq; r["code"] = result_.code; r["toast"] = result_.toast;
        }
    }

private:
    modes::SnapshotGate gate_;
    modes::Input slots_[modes::kQueueCap] = {};
    modes::InputQueue queue_;               // after slots_: built on them
    modes::Result result_{};
    bool online_ = false, have_result_ = false, ack_pending_ = false;
    uint32_t ack_seq_ = 0, applied_seq_ = 0, version_ = 0;
    uint32_t applied_ = 0, same_ = 0, ignored_ = 0, results_ = 0, bad_results_ = 0;
    uint32_t inputs_sent_ = 0, offline_ = 0, unsendable_ = 0, acks_sent_ = 0;
};
}
