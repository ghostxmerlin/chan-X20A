/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
#include <cstring>
#include <initializer_list>
// The three big modes (owner 2026-11-20, collector-0.5.21), owned and stored by the robot (NVS collector/big_mode):
//   关 off       nothing uploads, nothing paid: the robot's senses off (hearing, camera, M12), mind preset 0
//   收集 collect  senses on (sound and changed pictures go up), only free processing: mind preset 2 (paid off)
//   智能 smart    senses on, everything paid on: mind preset 9
// The small switches on the page are temporary: every power-on the robot applies its mode again and owes mind the
// mode's digit. Pure: tests/big_mode_test.cpp.
namespace collector::big {
enum class Mode : uint8_t { Off, Collect, Smart };
constexpr Mode kDefault = Mode::Collect;

inline int digit(Mode m) { return m == Mode::Off ? 0 : m == Mode::Collect ? 2 : 9; }
inline bool is_digit(int d) { return d == 0 || d == 2 || d == 9; }
inline bool senses_on(Mode m) { return m != Mode::Off; }
inline const char* name(Mode m) { return m == Mode::Off ? "off" : m == Mode::Collect ? "collect" : "smart"; }

inline bool from_digit(int d, Mode& out)
{
    if (!is_digit(d)) return false;
    out = d == 0 ? Mode::Off : d == 2 ? Mode::Collect : Mode::Smart;
    return true;
}
inline bool parse(const char* s, Mode& out)
{
    if (!s) return false;
    for (Mode m : {Mode::Off, Mode::Collect, Mode::Smart})
        if (!std::strcmp(s, name(m))) { out = m; return true; }
    return false;
}
// NVS value: the enum's number; anything else (or nothing) = kDefault
inline uint8_t stored(Mode m) { return static_cast<uint8_t>(m); }
inline Mode from_stored(uint8_t v) { return v <= 2 ? static_cast<Mode>(v) : kDefault; }
}
