/* SPDX-License-Identifier: MIT */
#pragma once
#include "mode_wire.h"
#include <ArduinoJson.hpp>
#include <cstdint>
#include <cstdio>
#include <cstring>
namespace collector::modes::rules {
// hello modes step 1b (collector-0.4.92): hello owns the mode. These are the transition rules of
// server/mind/modes.py and the mode table of server/mind/mode_table.py, ported as one pure function. The contract
// is tests/data/mode_vectors.json (tests/gen_mode_vectors.py): tests/mode_rules_test.cpp replays every vector and
// requires the same new state, code, toast and effects in order. Change modes.py, regenerate, and this must follow.
//
// Since collector-0.5.19 (owner 2026-11-20, Providence) Docking (crowd) is the only mode, as in mode_table.py: select
// of a removed mode answers removed_mode (DOCKING ONLY), Docking again already, the old voice words no_action, boot /
// long land in Docking with stop_speech, the stick's left (Docking's own direction) already, digit 5 already and the
// other digits 1-8 no_action. 0 and 9 (all paid off / on) are mind's: the caller sends them as mode_input and never
// asks here (here they answer unknown_input). billing is mind's too.

enum class Mode : uint8_t { Crowd, None = 255 };
constexpr size_t kModeCount = 1;
enum class Dir : uint8_t { None, ZhEn, EnZh };      // kept for the wire (dir is always None now)
enum class Input : uint8_t {
    Forward, Backward, Play, Pause, Open, Close, Start, Stop, Next, Previous,
    Digit, Tap, DoubleTap, Long, Direction, Select, Boot, Timeout, Unknown
};
enum class Code : uint8_t { Ok, Already, NotInMode, OnlyTalk, NotImplemented, NoAction, UnknownInput, RemovedMode };
enum class Effect : uint8_t { StopSpeech, TalkLeave, TalkEnter, SayRound, PrefetchOn, PrefetchOff };

inline constexpr const char* kModeIds[kModeCount] = {"crowd"};
// The modes removed on 2026-11-20 (mode_table.REMOVED_MODES): a select names one -> removed_mode.
inline constexpr const char* kRemovedModeIds[] = {"talk", "chat", "settings", "event", "subtitle", "companion", "idle"};
inline constexpr const char* kDirIds[] = {"", "zh_en", "en_zh"};
inline constexpr const char* kInputIds[] = {"forward", "backward", "play", "pause", "open", "close", "start", "stop",
                                            "next", "previous", "digit", "tap", "double_tap", "long", "direction",
                                            "select", "boot", "timeout"};
inline constexpr const char* kCodeIds[] = {"ok", "already", "not_in_mode", "only_talk", "not_implemented", "no_action",
                                           "unknown_input", "removed_mode"};
inline constexpr const char* kEffectIds[] = {"stop_speech", "talk_leave", "talk_enter", "say_round", "prefetch_on", "prefetch_off"};

struct ModeDef {
    const char* label;
    uint32_t color;
    Eye eye;
    Pool pool;
    TouchAct tap, dbl;
};
// mode_table.MODES (checked against the vectors' table): crowd, shown as Docking (owner 2026-11-18).
inline constexpr ModeDef kModes[kModeCount] = {
    {"DOCK", 0x30D060, Eye::V14, Pool::Lively, TouchAct::Server, TouchAct::Server},
};

// {mode, dir, host, return_to}. hosted = host "event" (a guest mode run by event-driven; never set in step 1).
// The default is the boot mode: crowd, shown as Docking (owner 2026-11-18; was talk).
struct State {
    Mode mode = Mode::Crowd;
    Dir dir = Dir::None;
    bool hosted = false;
    Mode return_to = Mode::None;
    bool operator==(const State& o) const { return mode == o.mode && dir == o.dir && hosted == o.hosted && return_to == o.return_to; }
    bool operator!=(const State& o) const { return !(*this == o); }
};

// The input's value, already read: digit 0..9 (else -1); direction 0..3 = up/down/left/right (else -1);
// select: target (None = missing or unknown), removed = it names a mode removed on 2026-11-20.
struct Value {
    int digit = -1;
    int direction = -1;
    Mode target = Mode::None;
    Dir dir = Dir::None;
    bool bad_dir = false;
    bool removed = false;
};

constexpr size_t kMaxEffects = 6;
constexpr size_t kToastMax = 32;
struct Outcome {
    State state;
    Code code = Code::Ok;
    char toast[kToastMax + 1] = {};
    Effect effects[kMaxEffects] = {};
    uint8_t n_effects = 0;
    bool has(Effect e) const { for (uint8_t i = 0; i < n_effects; ++i) if (effects[i] == e) return true; return false; }
};

inline const ModeDef& def(Mode m) { return kModes[static_cast<size_t>(m)]; }
inline const char* mode_id(Mode m) { return m == Mode::None ? nullptr : kModeIds[static_cast<size_t>(m)]; }
inline const char* dir_id(Dir d) { return d == Dir::None ? nullptr : kDirIds[static_cast<size_t>(d)]; }
inline const char* code_id(Code c) { return kCodeIds[static_cast<size_t>(c)]; }
inline const char* effect_id(Effect e) { return kEffectIds[static_cast<size_t>(e)]; }
inline const char* input_id(Input i) { return i == Input::Unknown ? "unknown" : kInputIds[static_cast<size_t>(i)]; }
inline Mode mode_from(const char* s)
{
    if (s) for (size_t i = 0; i < kModeCount; ++i) if (!std::strcmp(s, kModeIds[i])) return Mode(i);
    return Mode::None;
}
inline Input input_from(const char* s)
{
    if (s) for (size_t i = 0; i < sizeof kInputIds / sizeof *kInputIds; ++i) if (!std::strcmp(s, kInputIds[i])) return Input(i);
    return Input::Unknown;
}
inline bool removed_mode(const char* s)
{
    if (s) for (const char* r : kRemovedModeIds) if (!std::strcmp(s, r)) return true;
    return false;
}
// The corner label.
inline const char* label(const State& s) { return def(s.mode).label; }

// Reads a JSON value for `in` (the page's /v1/control {target:"mode", input, value}, the vectors). Anything
// unexpected leaves the field "missing", which the rules answer with unknown_input, as modes.py does.
inline Value value_from(Input in, ArduinoJson::JsonVariantConst v)
{
    Value out;
    if (in == Input::Digit && v.is<int>()) {
        const int d = v.as<int>();
        if (d >= 0 && d <= 9) out.digit = d;
    } else if (in == Input::Direction && v.is<const char*>()) {
        static constexpr const char* kDirections[] = {"up", "down", "left", "right"};
        for (int i = 0; i < 4; ++i) if (!std::strcmp(v.as<const char*>(), kDirections[i])) out.direction = i;
    } else if (in == Input::Select && v.is<ArduinoJson::JsonObjectConst>()) {
        const auto o = v.as<ArduinoJson::JsonObjectConst>();
        if (o["mode"].is<const char*>()) {
            out.target = mode_from(o["mode"].as<const char*>());
            out.removed = removed_mode(o["mode"].as<const char*>());
        }
        const auto d = o["dir"];
        if (d.isNull()) out.dir = Dir::None;
        else if (d.is<const char*>() && !std::strcmp(d.as<const char*>(), "zh_en")) out.dir = Dir::ZhEn;
        else if (d.is<const char*>() && !std::strcmp(d.as<const char*>(), "en_zh")) out.dir = Dir::EnZh;
        else out.bad_dir = true;
    }
    return out;
}

namespace detail {
inline void put_toast(Outcome& o, const char* a)
{
    std::snprintf(o.toast, sizeof o.toast, "%s", a);    // every toast here is ASCII <= 32
}
inline void add(Outcome& o, Effect e)
{
    if (!o.has(e) && o.n_effects < kMaxEffects) o.effects[o.n_effects++] = e;
}
inline const char* code_toast(Code c)
{
    static constexpr const char* kToasts[] = {"OK", "ALREADY", "NOT IN", "ONLY IN TALK", "NOT YET", "NO ACTION",
                                              "NO EFFECT", "DOCKING ONLY"};
    return kToasts[static_cast<size_t>(c)];
}
// modes._same: nothing changes, answer `c`.
inline Outcome same(const State& st, Code c)
{
    Outcome o;
    o.state = st; o.code = c;
    put_toast(o, code_toast(c));
    return o;
}
// boot / long (modes._done with Docking as the target): Docking with stop_speech.
inline Outcome to_docking(const State& old)
{
    Outcome o;
    o.state = State{};
    add(o, Effect::StopSpeech);
    put_toast(o, o.state.mode != old.mode ? def(o.state.mode).label : "STOP");
    return o;
}
}

// modes.transition(state, input, value, speaking=...) plus the digit table. Pure.
inline Outcome transition(const State& st, Input in, const Value& v, bool speaking)
{
    using namespace detail;
    // 1. the same in every mode: a reboot and the DualKey long press land in Docking and stop speech
    if (in == Input::Boot || in == Input::Long) return to_docking(st);
    // digit (2026-11-20): 5 = enter Docking (already), the other 1-8 do nothing; 0 / 9 belong to mind.
    if (in == Input::Digit) {
        if (v.digit == 5) return same(st, Code::Already);
        if (v.digit >= 1 && v.digit <= 8) return same(st, Code::NoAction);
        return same(st, Code::UnknownInput);
    }
    // 2. Docking's touch (0.5.15): tap = listen (stop while speaking), double tap = quiet
    if (in == Input::Tap || in == Input::DoubleTap) {
        Outcome o; o.state = st; o.code = Code::Ok;
        if (speaking) add(o, Effect::StopSpeech);
        put_toast(o, in == Input::DoubleTap ? "QUIET" : speaking ? "STOP" : "LISTEN");
        return o;
    }
    if (in == Input::Next || in == Input::Previous) return same(st, Code::OnlyTalk);
    // 3. select {mode}: Docking is already on; a removed mode is refused by name
    if (in == Input::Select) {
        if (v.removed) return same(st, Code::RemovedMode);
        if (v.target == Mode::None) return same(st, Code::UnknownInput);
        return same(st, Code::Already);
    }
    // 4. DualKey stick: left was Docking's own direction (already there); the others belonged to removed modes
    if (in == Input::Direction) return same(st, v.direction == 2 ? Code::Already : Code::UnknownInput);
    // 5. the old voice words
    if (in == Input::Forward || in == Input::Backward || in == Input::Play || in == Input::Pause ||
        in == Input::Open || in == Input::Close || in == Input::Start || in == Input::Stop)
        return same(st, Code::NoAction);
    return same(st, Code::UnknownInput);
}

// The look for the screen, in the C1 Snapshot shape (seq is the caller's).
inline Snapshot look(const State& s, uint32_t seq)
{
    const ModeDef& m = def(s.mode);
    Snapshot out{};
    out.seq = seq;
    std::snprintf(out.mode, sizeof out.mode, "%s", mode_id(s.mode));
    std::snprintf(out.dir, sizeof out.dir, "%s", s.dir == Dir::None ? "" : dir_id(s.dir));
    out.hosted = s.hosted; out.billing = false;
    out.eye = m.eye; out.pool = m.pool;
    std::snprintf(out.label, sizeof out.label, "%s", label(s));
    out.color = m.color; out.tap = m.tap; out.dbl = m.dbl;
    return out;
}
}
