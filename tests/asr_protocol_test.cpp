#include "../upstream/StackChan/firmware/main/collector/asr_protocol.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <random>
#include <vector>
using namespace collector;

static std::vector<AsrFrame> feed(AsrParser& parser, const std::vector<uint8_t>& bytes)
{
    std::vector<AsrFrame> frames;
    AsrFrame frame;
    for (auto b : bytes) if (parser.push(b, frame)) frames.push_back(frame);
    return frames;
}

int main()
{
    {   // Factory frames: forward, backward, Hi M Five, back to back.
        AsrParser p;
        auto f = feed(p, {0xAA, 0x55, 0x07, 0x55, 0xAA, 0xAA, 0x55, 0x09, 0x55, 0xAA, 0xAA, 0x55, 0xFF, 0x55, 0xAA});
        assert(f.size() == 3 && f[0].id == 0x07 && f[1].id == 0x09 && f[2].id == 0xFF);
        assert(!f[0].has_value && p.discarded() == 0);
        assert(asr_reaction(f[0].id) == AsrReaction::heart && asr_reaction(f[1].id) == AsrReaction::reject);
        assert(asr_reaction(f[2].id) == AsrReaction::none);
        assert(std::strcmp(asr_word(0x07), "forward") == 0 && std::strcmp(asr_word(0x09), "backward") == 0);
        assert(asr_reaction(0x08) == AsrReaction::none && asr_reaction(0x0A) == AsrReaction::none); // front/back
        assert(asr_face_switch(0x14) == 1 && asr_face_switch(0x15) == -1);                     // turn on / off
        assert(asr_face_switch(0x07) == 0 && asr_face_switch(0x10) == 0 && asr_face_switch(0x11) == 0);
        assert(std::strcmp(asr_word(0x14), "turn on") == 0 && std::strcmp(asr_word(0x15), "turn off") == 0);
    }
    {   // Six-byte reply with a value (firmware version).
        AsrParser p;
        auto f = feed(p, {0xAA, 0x55, 0x45, 0x01, 0x55, 0xAA});
        assert(f.size() == 1 && f[0].id == 0x45 && f[0].has_value && f[0].value == 0x01);
    }
    {   // Leading noise and a truncated frame are dropped, the next frame still decodes.
        AsrParser p;
        auto f = feed(p, {0x00, 0x13, 0xAA, 0x55, 0x07, 0xAA, 0x55, 0x09, 0x55, 0xAA});
        assert(f.size() == 1 && f[0].id == 0x09 && p.discarded() == 5);
    }
    {   // IDs equal to the marker bytes.
        AsrParser p;
        auto f = feed(p, {0xAA, 0x55, 0x55, 0x55, 0xAA, 0xAA, 0x55, 0xAA, 0x55, 0xAA});
        assert(f.size() == 2 && f[0].id == 0x55 && f[1].id == 0xAA);
    }
    {   // Unknown IDs decode but have no word and no reaction.
        AsrParser p;
        auto f = feed(p, {0xAA, 0x55, 0x60, 0x55, 0xAA});
        assert(f.size() == 1 && asr_word(0x60) == nullptr && asr_reaction(0x60) == AsrReaction::none);
    }
    {   // Random noise never crashes and a frame after noise is always found.
        std::mt19937 random(20261107);
        for (int round = 0; round < 20000; ++round) {
            AsrParser p;
            std::vector<uint8_t> bytes(random() % 40);
            for (auto& b : bytes) b = random();
            AsrFrame frame;
            for (auto b : bytes) p.push(b, frame);
            auto f = feed(p, {0xAA, 0x55, 0x07, 0x55, 0xAA});
            assert(!f.empty() && f.back().id == 0x07 && !f.back().has_value);
        }
    }
    std::cout << "PASS: Unit ASR frames, value replies, noise/truncation recovery, marker IDs, 20000 random prefixes\n";
}
