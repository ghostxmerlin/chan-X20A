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
// digit (owner table 2026-11-14): 4-8 run as select (idle, crowd, companion, subtitle zh_en, subtitle en_zh), 1-3 =
// no_action. 0 and 9 (all paid off / on) are mind's: the caller sends them as mode_input and never asks here
// (here they answer unknown_input). billing is mind's too.

enum class Mode : uint8_t { Talk, Chat, Settings, Event, Subtitle, Crowd, Companion, Idle, None = 255 };
constexpr size_t kModeCount = 8;
enum class Dir : uint8_t { None, ZhEn, EnZh };
enum class Input : uint8_t {
    Forward, Backward, Play, Pause, Open, Close, Start, Stop, Next, Previous,
    Digit, Tap, DoubleTap, Long, Direction, Select, Boot, Timeout, Unknown
};
enum class Code : uint8_t { Ok, Already, NotInMode, OnlyTalk, NotImplemented, NoAction, UnknownInput };
enum class Effect : uint8_t { StopSpeech, TalkLeave, TalkEnter, SayRound, PrefetchOn, PrefetchOff };

inline constexpr const char* kModeIds[kModeCount] = {"talk", "chat", "settings", "event", "subtitle", "crowd", "companion", "idle"};
inline constexpr const char* kDirIds[] = {"", "zh_en", "en_zh"};
inline constexpr const char* kInputIds[] = {"forward", "backward", "play", "pause", "open", "close", "start", "stop",
                                            "next", "previous", "digit", "tap", "double_tap", "long", "direction",
                                            "select", "boot", "timeout"};
inline constexpr const char* kCodeIds[] = {"ok", "already", "not_in_mode", "only_talk", "not_implemented", "no_action", "unknown_input"};
inline constexpr const char* kEffectIds[] = {"stop_speech", "talk_leave", "talk_enter", "say_round", "prefetch_on", "prefetch_off"};

// What a tap / double_tap / next / previous / forward means inside a mode (mode_table.py `inputs`).
struct Meaning {
    bool has = false;           // false: the input is not in this mode's table
    bool code_only = false;     // true: answer `code`, nothing changes
    Code code = Code::Ok;
    Effect effect = Effect::SayRound;   // when !code_only: one effect (every table entry has exactly one)
};
constexpr Meaning kNone{};
constexpr Meaning code_(Code c) { return Meaning{true, true, c, Effect::SayRound}; }
constexpr Meaning fx(Effect e) { return Meaning{true, false, Code::Ok, e}; }

struct ModeDef {
    const char* label;
    uint32_t color;
    Eye eye;
    Pool pool;
    TouchAct tap, dbl;
    Meaning forward, tap_in, double_in, next, previous;
};
// mode_table.MODES, in kModeIds order (checked against the vectors' table).
inline constexpr ModeDef kModes[kModeCount] = {
    {"TALK", 0xE040A0, Eye::LaughingMan, Pool::Default, TouchAct::Server, TouchAct::Server,
     fx(Effect::SayRound), fx(Effect::SayRound), fx(Effect::PrefetchOn), fx(Effect::PrefetchOn), fx(Effect::PrefetchOff)},
    {"CHAT", 0xF0B000, Eye::V05, Pool::Default, TouchAct::Server, TouchAct::Server,
     kNone, code_(Code::NotImplemented), code_(Code::NotImplemented), kNone, kNone},
    {"SET", 0xD8D8CC, Eye::PageSettings, Pool::Default, TouchAct::None, TouchAct::None,
     kNone, code_(Code::NoAction), code_(Code::NoAction), kNone, kNone},
    {"AUTO", 0x20B8C8, Eye::PageIdle, Pool::Default, TouchAct::None, TouchAct::None,
     kNone, code_(Code::NoAction), code_(Code::NoAction), kNone, kNone},
    {"SUB", 0x7C6CF0, Eye::PageSubtitle, Pool::Default, TouchAct::None, TouchAct::None,
     kNone, code_(Code::NoAction), code_(Code::NoAction), kNone, kNone},
    {"MEET", 0x30D060, Eye::V14, Pool::Lively, TouchAct::Server, TouchAct::Server,
     kNone, code_(Code::NotImplemented), code_(Code::NotImplemented), kNone, kNone},
    {"PAL", 0xF2E6C8, Eye::V18, Pool::Default, TouchAct::Server, TouchAct::None,
     kNone, code_(Code::NotImplemented), code_(Code::NoAction), kNone, kNone},
    {"IDLE", 0x505050, Eye::PageIdle, Pool::Default, TouchAct::Badge, TouchAct::Server,
     kNone, code_(Code::NoAction), code_(Code::NotImplemented), kNone, kNone},
};
inline constexpr const char* kSubtitleLabels[] = {"SUB", "SUB ZH>EN", "SUB EN>ZH"};   // by Dir

// {mode, dir, host, return_to}. hosted = host "event" (a guest mode run by event-driven; never set in step 1).
struct State {
    Mode mode = Mode::Talk;
    Dir dir = Dir::None;
    bool hosted = false;
    Mode return_to = Mode::None;
    bool operator==(const State& o) const { return mode == o.mode && dir == o.dir && hosted == o.hosted && return_to == o.return_to; }
    bool operator!=(const State& o) const { return !(*this == o); }
};

// The input's value, already read: digit 0..9 (else -1); direction 0..3 = up/down/left/right (else -1);
// select: target (None = missing or unknown) and dir (bad_dir = present but not zh_en / en_zh).
struct Value {
    int digit = -1;
    int direction = -1;
    Mode target = Mode::None;
    Dir dir = Dir::None;
    bool bad_dir = false;
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
// The corner label: subtitle shows its direction.
inline const char* label(const State& s)
{
    return s.mode == Mode::Subtitle ? kSubtitleLabels[static_cast<size_t>(s.dir)] : def(s.mode).label;
}

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
        if (o["mode"].is<const char*>()) out.target = mode_from(o["mode"].as<const char*>());
        const auto d = o["dir"];
        if (d.isNull()) out.dir = Dir::None;
        else if (d.is<const char*>() && !std::strcmp(d.as<const char*>(), "zh_en")) out.dir = Dir::ZhEn;
        else if (d.is<const char*>() && !std::strcmp(d.as<const char*>(), "en_zh")) out.dir = Dir::EnZh;
        else out.bad_dir = true;
    }
    return out;
}

namespace detail {
inline void put_toast(Outcome& o, const char* a, const char* b = "")
{
    // result(): the code's toast with '{mode}' filled in, then strip(); every toast here is ASCII <= 32.
    std::snprintf(o.toast, sizeof o.toast, "%s%s", a, b);
}
inline void add(Outcome& o, Effect e)
{
    if (!o.has(e) && o.n_effects < kMaxEffects) o.effects[o.n_effects++] = e;
}
inline const char* code_toast(Code c)
{
    static constexpr const char* kToasts[] = {"OK", "ALREADY", "NOT IN ", "ONLY IN TALK", "NOT YET", "NO ACTION", "NO EFFECT"};
    return kToasts[static_cast<size_t>(c)];
}
// modes._same: nothing changes, answer `c` (not_in_mode names the mode the word belongs to).
inline Outcome same(const State& st, Code c, Mode named = Mode::None)
{
    Outcome o;
    o.state = st; o.code = c;
    if (c == Code::NotInMode) put_toast(o, code_toast(c), def(named).label);
    else put_toast(o, code_toast(c));
    return o;
}
// modes._done: tidy the new state, mode-change effects first, then the toast.
inline Outcome done(const State& old, State nw, const Effect* fx = nullptr, size_t n = 0)
{
    if (nw.mode != Mode::Settings) nw.return_to = Mode::None;
    if (nw.mode != Mode::Subtitle && !(nw.mode == Mode::Settings && nw.return_to == Mode::Subtitle)) nw.dir = Dir::None;
    Outcome o;
    o.state = nw;
    if (nw.mode != old.mode) {
        add(o, Effect::StopSpeech);
        if (old.mode == Mode::Talk) add(o, Effect::TalkLeave);
        if (nw.mode == Mode::Talk) add(o, Effect::TalkEnter);
    }
    for (size_t i = 0; i < n; ++i) add(o, fx[i]);
    if (nw.mode != old.mode || nw.dir != old.dir) put_toast(o, label(nw));
    else if (o.n_effects) {
        switch (o.effects[o.n_effects - 1]) {
        case Effect::SayRound: put_toast(o, "SAYING"); break;
        case Effect::PrefetchOn: put_toast(o, "PREFETCH ON"); break;
        case Effect::PrefetchOff: put_toast(o, "PREFETCH OFF"); break;
        case Effect::StopSpeech: put_toast(o, "STOP"); break;
        case Effect::TalkEnter: put_toast(o, "TALK"); break;
        default: put_toast(o, "OK"); break;
        }
    } else put_toast(o, "OK");
    return o;
}
inline State plain(Mode m, Dir d = Dir::None) { State s; s.mode = m; s.dir = m == Mode::Subtitle ? d : Dir::None; return s; }
inline Outcome enter(const State& st, Mode target, Dir d)
{
    State nw = plain(target, d);
    if (target == Mode::Settings) {
        nw.return_to = st.mode == Mode::Event ? Mode::Idle : st.mode;
        nw.dir = st.mode == Mode::Subtitle ? st.dir : Dir::None;
    }
    return done(st, nw);
}
inline Outcome leave(const State& st)
{
    State nw;
    if (st.mode == Mode::Settings) {
        const Mode target = st.return_to == Mode::None ? Mode::Idle : st.return_to;
        nw = plain(target, st.dir);
    } else if (st.hosted) {
        nw = plain(Mode::Event);
    } else {
        nw = plain(Mode::Idle);
    }
    if (nw.mode == st.mode && nw.dir == st.dir) return same(st, Code::Already);
    return done(st, nw);
}
inline Outcome leave_host(const State& st) { return done(st, plain(Mode::Idle)); }
inline Outcome switch_dir(const State& st, Dir d) { State nw = st; nw.dir = d; return done(st, nw); }
inline const Meaning* meaning(const ModeDef& m, Input in)
{
    const Meaning* p = in == Input::Forward ? &m.forward : in == Input::Tap ? &m.tap_in : in == Input::DoubleTap ? &m.double_in :
                       in == Input::Next ? &m.next : in == Input::Previous ? &m.previous : nullptr;
    return p && p->has ? p : nullptr;
}
}

// modes.transition(state, input, value, speaking=...) plus the digit table. Pure.
inline Outcome transition(const State& st, Input in, const Value& v, bool speaking)
{
    using namespace detail;
    const ModeDef& m = def(st.mode);
    // 1. the same in every mode
    if (in == Input::Boot) {
        const Effect fx[] = {Effect::StopSpeech, Effect::TalkEnter};
        return done(st, plain(Mode::Talk), fx, 2);
    }
    if (in == Input::Long) {
        const Effect fx[] = {Effect::StopSpeech};
        return done(st, plain(Mode::Talk), fx, 1);
    }
    // digit (owner 2026-11-14): 1-3 empty, 4-8 = select; 0 / 9 belong to mind.
    if (in == Input::Digit) {
        if (v.digit >= 1 && v.digit <= 3) return same(st, Code::NoAction);
        if (v.digit < 4 || v.digit > 8) return same(st, Code::UnknownInput);
        static constexpr Mode kTargets[] = {Mode::Idle, Mode::Crowd, Mode::Companion, Mode::Subtitle, Mode::Subtitle};
        static constexpr Dir kDirs[] = {Dir::None, Dir::None, Dir::None, Dir::ZhEn, Dir::EnZh};
        Value sel;
        sel.target = kTargets[v.digit - 4]; sel.dir = kDirs[v.digit - 4];
        return transition(st, Input::Select, sel, speaking);
    }
    // 2. what this mode gives the input
    if (const Meaning* mean = meaning(m, in)) {
        if (in == Input::Tap && speaking && m.tap == TouchAct::Server) {
            const Effect fx[] = {Effect::StopSpeech};
            return done(st, st, fx, 1);              // hello is speaking: a tap stops it (owner 9)
        }
        if (mean->code_only) return same(st, mean->code);
        return done(st, st, &mean->effect, 1);
    }
    if (in == Input::Next || in == Input::Previous) return same(st, Code::OnlyTalk);
    // 3. select {mode, dir?}: picking the current mode leaves it
    if (in == Input::Select) {
        if (v.target == Mode::None || (v.target == Mode::Subtitle && v.bad_dir)) return same(st, Code::UnknownInput);
        if (st.hosted && v.target == Mode::Event) return leave_host(st);
        if (v.target == st.mode) {
            if (v.target == Mode::Subtitle && v.dir != Dir::None && v.dir != st.dir) return switch_dir(st, v.dir);
            return leave(st);
        }
        return enter(st, v.target, v.dir == Dir::None ? Dir::ZhEn : v.dir);
    }
    // 4. DualKey stick: up / down = subtitle zh_en / en_zh, left = crowd, right = companion; again = leave
    if (in == Input::Direction) {
        static constexpr Mode kOwner[] = {Mode::Subtitle, Mode::Subtitle, Mode::Crowd, Mode::Companion};
        static constexpr Dir kDir[] = {Dir::ZhEn, Dir::EnZh, Dir::None, Dir::None};
        if (v.direction < 0 || v.direction > 3) return same(st, Code::UnknownInput);
        const Mode owner = kOwner[v.direction];
        const Dir d = kDir[v.direction];
        if (owner == st.mode) {
            if (d == Dir::None || d == st.dir) return leave(st);
            return switch_dir(st, d);
        }
        return enter(st, owner, d);
    }
    // 5. the voice word pairs (and timeout)
    struct Word { Input in; Mode owner; bool enter; };
    static constexpr Word kWords[] = {
        {Input::Forward, Mode::Talk, true}, {Input::Backward, Mode::Talk, false},
        {Input::Play, Mode::Chat, true}, {Input::Pause, Mode::Chat, false},
        {Input::Open, Mode::Settings, true}, {Input::Close, Mode::Settings, false}, {Input::Timeout, Mode::Settings, false},
        {Input::Start, Mode::Event, true}, {Input::Stop, Mode::Event, false}};
    for (const Word& w : kWords) {
        if (w.in != in) continue;
        const bool host_is_owner = st.hosted && w.owner == Mode::Event;
        if (w.enter) {
            if (w.owner == st.mode || host_is_owner) return same(st, Code::Already);
            return enter(st, w.owner, Dir::None);
        }
        if (w.owner == st.mode) return leave(st);
        if (host_is_owner) return leave_host(st);
        return same(st, Code::NotInMode, w.owner);
    }
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
