/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>

namespace collector {
// The voice module's wire protocol, UART 115200 8N1, no initialisation: every recognised command is sent as
// AA 55 <id> 55 AA; a frame may carry one value byte: AA 55 <id> <value> 55 AA. Since 2026-11-20 the module is the
// owner's StickS3 (~/exia/stick/docs/voice-protocol.md), which keeps the M5Stack Unit ASR's frame and sends only its
// own eight words (ids 0x50-0x5F); the factory vocabulary (motion words, digits, volume, wake words) is gone.
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

// The StickS3's words (ids from its main/voice_words.h); nullptr for every other id.
inline const char* asr_word(uint8_t id)
{
    switch (id) {
    case 0x50: return "docking sequence";
    case 0x51: return "o raiser";
    case 0x52: return "inevitable";
    case 0x53: return "providence";
    case 0x55: return "say what again";
    case 0x5A: return "inception";
    case 0x5E: return "expelliarmus";
    case 0x5F: return "expecto patronum";
    default: return nullptr;
    }
}

// What a frame means to hello: Call = PROVIDENCE, the robot's name (owner 2026-11-20: being called, as the Unit ASR's
// hello was); Heard = one of the Stick's other words (reported, no action yet); Ignore = anything else, a frame
// with a value included.
enum class AsrAction : uint8_t { Ignore, Call, Heard };
inline AsrAction asr_action(const AsrFrame& f)
{
    if (f.has_value || asr_word(f.id) == nullptr) return AsrAction::Ignore;
    return f.id == 0x53 ? AsrAction::Call : AsrAction::Heard;
}
}
