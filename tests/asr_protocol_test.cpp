// The StickS3 voice module (owner 2026-11-20; ~/exia/stick/docs/voice-protocol.md): the Unit ASR frame
// (AA 55 id 55 AA, or AA 55 id value 55 AA), only the Stick's eight words. PROVIDENCE (0x53) = being called; the
// other seven are heard words (reported, no action); every other id, a factory word among them, is ignored.
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

static AsrFrame word(uint8_t id) { AsrFrame f; f.id = id; return f; }

int main()
{
    {   // PROVIDENCE calls; the other seven Stick words are heard, with the protocol's names.
        assert(asr_action(word(0x53)) == AsrAction::Call && std::strcmp(asr_word(0x53), "providence") == 0);
        const struct { uint8_t id; const char* name; } heard[] = {
            {0x50, "docking sequence"}, {0x51, "o raiser"}, {0x52, "inevitable"}, {0x55, "say what again"},
            {0x5A, "inception"}, {0x5E, "expelliarmus"}, {0x5F, "expecto patronum"}};
        for (const auto& h : heard) {
            assert(asr_action(word(h.id)) == AsrAction::Heard);
            assert(std::strcmp(asr_word(h.id), h.name) == 0);
        }
    }
    {   // Gaps in the Stick's table, factory words, wake ids and replies: no word, ignored.
        for (uint8_t id : {0x54, 0x56, 0x57, 0x5B, 0x5D, 0x60, 0x65, 0x01, 0x07, 0x14, 0x20, 0x29, 0x31, 0x32, 0xFF,
                           0x40, 0x45, 0xFE}) {
            assert(asr_action(word(id)) == AsrAction::Ignore);
            assert(asr_word(id) == nullptr);
        }
    }
    {   // A frame with a value (the Stick's proposed speaker results, a factory reply) is never a word.
        AsrFrame f = word(0x53); f.has_value = true; f.value = 7;
        assert(asr_action(f) == AsrAction::Ignore);
    }
    {   // Back-to-back Stick frames decode; 0x55 (SAY WHAT AGAIN) equals a marker byte and still decodes.
        AsrParser p;
        auto f = feed(p, {0xAA, 0x55, 0x53, 0x55, 0xAA, 0xAA, 0x55, 0x55, 0x55, 0xAA, 0xAA, 0x55, 0x5F, 0x55, 0xAA});
        assert(f.size() == 3 && f[0].id == 0x53 && f[1].id == 0x55 && f[2].id == 0x5F);
        assert(!f[0].has_value && p.discarded() == 0);
    }
    {   // Six-byte frame with a value.
        AsrParser p;
        auto f = feed(p, {0xAA, 0x55, 0xC3, 0x5A, 0x55, 0xAA});
        assert(f.size() == 1 && f[0].id == 0xC3 && f[0].has_value && f[0].value == 0x5A);
    }
    {   // Leading noise and a truncated frame are dropped, the next frame still decodes.
        AsrParser p;
        auto f = feed(p, {0x00, 0x13, 0xAA, 0x55, 0x53, 0xAA, 0x55, 0x50, 0x55, 0xAA});
        assert(f.size() == 1 && f[0].id == 0x50 && p.discarded() == 5);
    }
    {   // Random noise never crashes and a frame after noise is always found.
        std::mt19937 random(20261120);
        for (int round = 0; round < 20000; ++round) {
            AsrParser p;
            std::vector<uint8_t> bytes(random() % 40);
            for (auto& b : bytes) b = random();
            AsrFrame frame;
            for (auto b : bytes) p.push(b, frame);
            auto f = feed(p, {0xAA, 0x55, 0x53, 0x55, 0xAA});
            assert(!f.empty() && f.back().id == 0x53 && !f.back().has_value);
        }
    }
    std::cout << "PASS: StickS3 words (providence calls, seven heard, the rest ignored), frames, noise recovery\n";
}
