/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>

namespace collector {
// M5Stack Unit ASR (CI-03T) factory firmware, UART 115200 8N1, no initialisation.
// Every recognised command is sent as AA 55 <id> 55 AA. A few replies (e.g. the
// firmware version, 0x45) carry one value byte: AA 55 <id> <value> 55 AA.
// IDs follow M5Stack's unit_asr library (factory English vocabulary).
struct AsrFrame {
    uint8_t id = 0;
    bool has_value = false;
    uint8_t value = 0;
};

class AsrParser {
public:
    // Feed one received byte. Returns true when this byte completes a frame.
    bool push(uint8_t byte, AsrFrame& out)
    {
        if (count_ == sizeof(window_)) {
            for (size_t i = 1; i < count_; ++i) window_[i - 1] = window_[i];
            --count_;
            ++discarded_;
        }
        window_[count_++] = byte;
        if (count_ >= 5) {
            const uint8_t* w = window_ + count_ - 5;
            if (w[0] == 0xAA && w[1] == 0x55 && w[3] == 0x55 && w[4] == 0xAA) {
                out = AsrFrame{w[2], false, 0};
                discarded_ += count_ - 5;
                count_ = 0;
                return true;
            }
        }
        if (count_ == 6 && window_[0] == 0xAA && window_[1] == 0x55 && window_[4] == 0x55 && window_[5] == 0xAA) {
            out = AsrFrame{window_[2], true, window_[3]};
            count_ = 0;
            return true;
        }
        return false;
    }
    // Bytes that were dropped because they never became part of a frame.
    uint32_t discarded() const { return discarded_; }

private:
    uint8_t window_[6]{};
    size_t count_ = 0;
    uint32_t discarded_ = 0;
};

inline const char* asr_word(uint8_t id)
{
    switch (id) {
    case 0x01: return "up";
    case 0x02: return "down";
    case 0x03: return "left";
    case 0x04: return "turn left";
    case 0x05: return "right";
    case 0x06: return "turn right";
    case 0x07: return "forward";
    case 0x08: return "front";
    case 0x09: return "backward";
    case 0x0A: return "back";
    case 0x10: return "open";
    case 0x11: return "close";
    case 0x12: return "start";
    case 0x13: return "stop";
    case 0x14: return "turn on";
    case 0x15: return "turn off";
    case 0x16: return "play";
    case 0x17: return "pause";
    case 0x18: return "turn on the lights";
    case 0x19: return "turn off the lights";
    case 0x1A: return "previous";
    case 0x1B: return "next";
    case 0x20: return "zero";
    case 0x21: return "one";
    case 0x22: return "two";
    case 0x23: return "three";
    case 0x24: return "four";
    case 0x25: return "five";
    case 0x26: return "six";
    case 0x27: return "seven";
    case 0x28: return "eight";
    case 0x29: return "nine";
    case 0x30: return "ok";
    case 0x31: return "hi A S R";
    case 0x32: return "hello";
    case 0x40: return "increase volume";
    case 0x41: return "decrease volume";
    case 0x42: return "maximum volume";
    case 0x43: return "medium volume";
    case 0x44: return "minimum volume";
    case 0x45: return "check firmware version";
    case 0xFE: return "announce";
    case 0xFF: return "Hi M Five";
    default: return nullptr;
    }
}

// "forward" / "backward" showed the heart / reject X from 2026-11-07 until 2026-11-13. Since 0.4.92 they
// are mode words (enter / leave the talk show, asr_mode_words.h); the screen shows no reaction for them.
enum class AsrReaction : uint8_t { none, heart, reject };
inline AsrReaction asr_reaction(uint8_t)
{
    return AsrReaction::none;
}

// Second soul test (user, 2026-11-07): "turn on" starts the M12 face view,
// "turn off" stops it and returns home. +1 on, -1 off, 0 not a face command.
inline int asr_face_switch(uint8_t id)
{
    if (id == 0x14) return 1;
    if (id == 0x15) return -1;
    return 0;
}
}
