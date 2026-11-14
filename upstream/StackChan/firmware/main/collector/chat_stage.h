/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
#include <cstring>

namespace collector::chat {
// Chat stage dots (owner 2026-11-14, collector-0.4.93): in chat mode the home screen shows three small dots for the
// stage of the reply. Pure logic, host-tested (tests/chat_stage_test.cpp).
//   ○○○ idle   ●○○ listen   ○●○ think   ○○● speak   skip = all three grey for 0.6 s, then idle
// listen / think / skip come from the ear on /mouth ({"type":"chat_stage","stage":..,"at_ms":..}, only sent while
// hello is in chat); speak is the robot's own: a /mouth stream is playing. Speak overrides listen / think; the skip
// flash overrides everything while it lasts. listen falls back to idle after 20 s, think after 8 s, and the end of
// speaking clears the stage to idle (a stage that arrived after the speech began stays: the next turn's listen).
// Outside chat nothing is drawn and stages are dropped.
//
// StageState lives in the mode session (PSRAM, under the mode lock) and is written by the /mouth task; the screen
// task copies it and runs DotsView, so only the screen task touches LVGL.

enum class Stage : uint8_t { Idle, Listen, Think, Skip };
constexpr int64_t kListenUs = 20LL * 1000000, kThinkUs = 8LL * 1000000, kSkipUs = 600LL * 1000;
constexpr int64_t kStartTrustUs = 1000000;     // a recorded tts.start this close before the tick's edge is this speech's
constexpr size_t kMessageMax = 200;            // a chat_stage message is at most 200 B

// "listen" / "think" / "skip"; false for anything else (ignored).
inline bool stage_from(const char* s, Stage& out)
{
    if (!s) return false;
    if (!std::strcmp(s, "listen")) { out = Stage::Listen; return true; }
    if (!std::strcmp(s, "think")) { out = Stage::Think; return true; }
    if (!std::strcmp(s, "skip")) { out = Stage::Skip; return true; }
    return false;
}
inline const char* stage_id(Stage s)
{
    static constexpr const char* kIds[] = {"idle", "listen", "think", "skip"};
    return kIds[static_cast<size_t>(s)];
}

// The last stage the ear sent, in robot time. seq changes with every accepted stage (and with clear()).
struct StageState {
    Stage stage = Stage::Idle;      // Listen / Think, or Idle (also after a skip)
    int64_t at_us = 0;              // when it arrived
    int64_t skip_until_us = 0;      // the grey flash runs until then
    int64_t speak_from_us = 0;      // the last tts.start on /mouth (review Minor 4): when the speech really began
    uint32_t seq = 0;
    void clear() { stage = Stage::Idle; at_us = 0; skip_until_us = 0; speak_from_us = 0; ++seq; }
    void on_speech_start(int64_t now_us) { speak_from_us = now_us; ++seq; }
    void on_stage(Stage s, int64_t now_us)
    {
        if (s == Stage::Skip) { stage = Stage::Idle; skip_until_us = now_us + kSkipUs; }   // flash, then idle
        else { stage = s; skip_until_us = 0; }
        at_us = now_us;
        ++seq;
    }
};

// What to draw now. lit: -1 none, 0 listen, 1 think, 2 speak; grey: all three filled grey (skip flash).
struct Dots {
    bool visible = false;
    int8_t lit = -1;
    bool grey = false;
    bool operator==(const Dots& o) const { return visible == o.visible && lit == o.lit && grey == o.grey; }
    bool operator!=(const Dots& o) const { return !(*this == o); }
};

// Screen side: takes a copy of StageState when its seq changes, tracks speaking, and decides the dots each tick.
class DotsView {
public:
    // in_chat: the robot's mode is chat. Leaving chat forgets everything.
    Dots tick(bool in_chat, const StageState& s, bool speaking, int64_t now_us)
    {
        if (!in_chat) { have_ = false; speaking_ = false; return Dots{}; }
        if (!have_ || s.seq != seq_) { cur_ = s; seq_ = s.seq; have_ = true; }
        if (!speaking_ && speaking) speak_from_us_ = now_us;
        // Speaking ended: idle, unless the stage came after the speech began (the next turn's listen). "Began" is the
        // recorded tts.start when it belongs to this speech (at most kStartTrustUs before the tick saw speaking), so a
        // listen that came between tts.start and that tick is kept; else the tick's own edge.
        if (speaking_ && !speaking) {
            const bool recorded = cur_.speak_from_us && cur_.speak_from_us <= speak_from_us_ &&
                                  speak_from_us_ - cur_.speak_from_us < kStartTrustUs;
            if (cur_.at_us <= (recorded ? cur_.speak_from_us : speak_from_us_)) cur_.stage = Stage::Idle;
        }
        speaking_ = speaking;
        Dots d;
        d.visible = true;
        if (now_us < cur_.skip_until_us) { d.grey = true; return d; }
        if (speaking) { d.lit = 2; return d; }
        if (cur_.stage == Stage::Listen && now_us - cur_.at_us < kListenUs) d.lit = 0;
        else if (cur_.stage == Stage::Think && now_us - cur_.at_us < kThinkUs) d.lit = 1;
        return d;
    }

private:
    StageState cur_{};
    int64_t speak_from_us_ = 0;
    uint32_t seq_ = 0;
    bool have_ = false, speaking_ = false;
};
}
