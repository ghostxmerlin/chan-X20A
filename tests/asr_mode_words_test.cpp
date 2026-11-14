// Unit ASR words → mode inputs (hello modes step 1, owner 2026-11-14). The latest keyword table
// wins: forward/backward, play/pause, open/close, start/stop, next/previous and the digits go to
// mind as mode inputs; their old local uses (open/close tracking, start/stop hearing, next = random
// clip) are gone. Motion words, turn on/off, volume, hello and ok stay local.
#include "asr_mode_words.h"
#include "hearing_policy.h"
#include "interaction_policy.h"
#include "voice_policy.h"
#include "asr_motion.h"
#include "asr_protocol.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

int main() {
    using namespace collector;
    int d = -1;
    // The brief's lines, verbatim.
    assert(!std::strcmp(asr_mode_input(0x07, false, &d), "forward"));
    assert(!std::strcmp(asr_mode_input(0x17, false, &d), "pause"));
    assert(!std::strcmp(asr_mode_input(0x1B, false, &d), "next"));
    assert(!std::strcmp(asr_mode_input(0x29, false, &d), "digit") && d == 9);
    assert(asr_mode_input(0x01, false, &d) == nullptr);       // up: tracking, local
    assert(asr_mode_input(0x14, false, &d) == nullptr);       // turn on: M12 view, local
    assert(asr_mode_input(0x40, false, &d) == nullptr);       // volume, local
    assert(asr_mode_input(0x32, false, &d) == nullptr);       // hello: unchanged in step 1
    assert(asr_mode_input(0x07, true, &d) == nullptr);        // frames with a value are not words
    assert(interaction::asr_mode_action(0x10, false) == interaction::AsrModeAction::None);
    assert(hearing::asr_action(0x12, false) == 0);
    assert(!voice_asr_next(0x1b, false));

    // Every word of the table, by its factory id (asr_protocol.h).
    struct Word { uint8_t id; const char* input; };
    static const Word words[] = {
        {0x07, "forward"}, {0x09, "backward"}, {0x16, "play"}, {0x17, "pause"}, {0x10, "open"},
        {0x11, "close"}, {0x12, "start"}, {0x13, "stop"}, {0x1B, "next"}, {0x1A, "previous"},
    };
    for (const auto& w : words) {
        d = 42;
        const char* in = asr_mode_input(w.id, false, &d);
        assert(in && !std::strcmp(in, w.input));
        assert(d == -1);                                       // a word has no value
        assert(asr_mode_input(w.id, true, &d) == nullptr);
        // Each mode word is also the robot's own factory word of the same name.
        assert(asr_word(w.id) && !std::strcmp(asr_word(w.id), w.input));
        // No mode word moves the head, toggles tracking or hearing, or plays a clip.
        assert(asr_motion_word(w.id, false) == AsrMotionWord::None);
        assert(interaction::asr_mode_action(w.id, false) == interaction::AsrModeAction::None);
        assert(hearing::asr_action(w.id, false) == 0);
        assert(!voice_asr_next(w.id, false));
    }
    for (int n = 0; n <= 9; ++n) {
        d = -1;
        const char* in = asr_mode_input(static_cast<uint8_t>(0x20 + n), false, &d);
        assert(in && !std::strcmp(in, "digit") && d == n);
        d = 7;
        assert(asr_mode_input(static_cast<uint8_t>(0x20 + n), true, &d) == nullptr);
    }
    // A null digit pointer is allowed (the caller only wants the word).
    assert(!std::strcmp(asr_mode_input(0x25, false, nullptr), "digit"));
    assert(!std::strcmp(asr_mode_input(0x16, false, nullptr), "play"));

    // Everything else stays local: motion words, turn on/off, lights, ok, hi ASR, hello, volume,
    // firmware version, announce, wake word, unknown ids.
    int local = 0;
    for (int id = 0; id <= 0xFF; ++id) {
        bool mode = false;
        for (const auto& w : words) mode = mode || w.id == id;
        mode = mode || (id >= 0x20 && id <= 0x29);
        d = -1;
        const char* in = asr_mode_input(static_cast<uint8_t>(id), false, &d);
        if (mode) { assert(in); continue; }
        assert(in == nullptr && d == -1);
        ++local;
    }
    assert(local == 256 - 20);
    // The direction words keep turning the head (unchanged in step 1).
    assert(asr_motion_word(0x01, false) == AsrMotionWord::TrackingOn);
    assert(asr_motion_word(0x02, false) == AsrMotionWord::TrackingOff);
    assert(asr_motion_word(0x03, false) == AsrMotionWord::Left);
    assert(asr_motion_word(0x04, false) == AsrMotionWord::TurnLeft);
    assert(asr_motion_word(0x05, false) == AsrMotionWord::Right);
    assert(asr_motion_word(0x06, false) == AsrMotionWord::TurnRight);
    assert(asr_motion_word(0x08, false) == AsrMotionWord::Front);
    assert(asr_motion_word(0x0A, false) == AsrMotionWord::Back);
    // turn on / turn off still release the visual modes; start/stop no longer touch hearing.
    assert(interaction::asr_mode_action(0x14, false) == interaction::AsrModeAction::ReleaseModes);
    assert(interaction::asr_mode_action(0x15, false) == interaction::AsrModeAction::ReleaseModes);
    assert(interaction::asr_mode_action(0x14, true) == interaction::AsrModeAction::None);
    assert(hearing::asr_action(0x13, false) == 0);
    std::puts("asr mode words ok");
    return 0;
}
