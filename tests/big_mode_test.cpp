// The three big modes (owner 2026-11-20): 关 / 收集 / 智能, stored on the robot (collector-0.5.21).
#include "../upstream/StackChan/firmware/main/collector/big_mode.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
using namespace collector::big;

int main()
{
    assert(digit(Mode::Off) == 0 && digit(Mode::Collect) == 2 && digit(Mode::Smart) == 9);
    for (Mode m : {Mode::Off, Mode::Collect, Mode::Smart}) {
        Mode back = Mode::Smart;
        assert(from_digit(digit(m), back) && back == m);
        assert(parse(name(m), back) && back == m);
    }
    assert(!std::strcmp(name(Mode::Off), "off") && !std::strcmp(name(Mode::Collect), "collect") && !std::strcmp(name(Mode::Smart), "smart"));
    assert(senses_on(Mode::Collect) && senses_on(Mode::Smart) && !senses_on(Mode::Off));
    for (int d : {0, 2, 9}) assert(is_digit(d));
    for (int d : {-1, 1, 3, 5, 8, 10}) assert(!is_digit(d));
    Mode m = Mode::Off;
    assert(!parse("on", m) && !parse("", m) && !parse(nullptr, m) && m == Mode::Off);
    assert(!from_digit(5, m) && m == Mode::Off);
    assert(from_stored(1) == Mode::Collect && from_stored(0) == Mode::Off && from_stored(2) == Mode::Smart);
    assert(from_stored(7) == kDefault && kDefault == Mode::Collect);
    assert(stored(Mode::Smart) == 2);
    std::puts("PASS: big modes (digits, names, senses, stored values)");
}
