// Chat stage dots (fw/chat_stage.h, collector-0.4.93): ○○○ idle, ●○○ listen, ○●○ think, ○○● speak (the robot's own
// /mouth playback, overrides listen / think), skip = all grey for 0.6 s then idle; listen -> idle after 20 s, think
// after 8 s, end of speaking -> idle; outside chat nothing is drawn.
#include "chat_stage.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
using namespace collector::chat;

namespace {
constexpr int64_t kMs = 1000, kS = 1000000;
Dots dots(bool visible, int lit, bool grey = false) { Dots d; d.visible = visible; d.lit = int8_t(lit); d.grey = grey; return d; }
const Dots kHidden{}, kIdle = dots(true, -1), kListen = dots(true, 0), kThink = dots(true, 1), kSpeak = dots(true, 2),
           kGrey = dots(true, -1, true);
}

int main()
{
    Stage st;
    assert(stage_from("listen", st) && st == Stage::Listen && stage_from("think", st) && st == Stage::Think);
    assert(stage_from("skip", st) && st == Stage::Skip);
    for (const char* bad : {"speak", "idle", "", "LISTEN", "listen ", static_cast<const char*>(nullptr)}) assert(!stage_from(bad, st));
    assert(!std::strcmp(stage_id(Stage::Think), "think"));

    // Stages and the timeouts.
    {
        StageState s; DotsView v;
        assert(v.tick(true, s, false, 0) == kIdle);
        s.on_stage(Stage::Listen, 1 * kS);
        assert(v.tick(true, s, false, 1 * kS) == kListen);
        assert(v.tick(true, s, false, 1 * kS + kListenUs - 1) == kListen);
        assert(v.tick(true, s, false, 1 * kS + kListenUs) == kIdle);           // listen: 20 s
        s.on_stage(Stage::Think, 30 * kS);
        assert(v.tick(true, s, false, 30 * kS) == kThink);
        assert(v.tick(true, s, false, 30 * kS + kThinkUs - 1) == kThink);
        assert(v.tick(true, s, false, 30 * kS + kThinkUs) == kIdle);           // think: 8 s
        s.on_stage(Stage::Listen, 40 * kS);
        s.on_stage(Stage::Think, 41 * kS);                                     // the newest wins
        assert(v.tick(true, s, false, 41 * kS) == kThink);
    }
    // Speak overrides listen / think while playing; when it ends, idle (not back to think).
    {
        StageState s; DotsView v;
        s.on_stage(Stage::Think, 0);
        assert(v.tick(true, s, false, 1 * kS) == kThink);
        assert(v.tick(true, s, true, 2 * kS) == kSpeak);
        assert(v.tick(true, s, true, 30 * kS) == kSpeak);                      // no timeout while speaking
        assert(v.tick(true, s, false, 31 * kS) == kIdle);
        s.on_stage(Stage::Listen, 32 * kS);                                    // the next turn
        assert(v.tick(true, s, false, 32 * kS) == kListen);
        assert(v.tick(true, s, true, 33 * kS) == kSpeak);                      // speaking with listen pending
        assert(v.tick(true, s, false, 34 * kS) == kIdle);
        // A listen that arrives while speaking (the next turn) stays after the speech ends.
        assert(v.tick(true, s, true, 40 * kS) == kSpeak);
        s.on_stage(Stage::Listen, 41 * kS);
        assert(v.tick(true, s, true, 41 * kS) == kSpeak && v.tick(true, s, false, 42 * kS) == kListen);
        // Speaking with no stage at all (a line that came without one).
        assert(v.tick(true, s, true, 65 * kS) == kSpeak && v.tick(true, s, false, 66 * kS) == kIdle);
    }
    // Skip: all grey for 0.6 s, then idle (the turn is over); it shows even over speaking.
    {
        StageState s; DotsView v;
        s.on_stage(Stage::Think, 0);
        s.on_stage(Stage::Skip, 1 * kS);
        assert(v.tick(true, s, false, 1 * kS) == kGrey);
        assert(v.tick(true, s, false, 1 * kS + kSkipUs - 1) == kGrey);
        assert(v.tick(true, s, false, 1 * kS + kSkipUs) == kIdle);
        assert(v.tick(true, s, false, 1 * kS + 600 * kMs + 5 * kS) == kIdle);  // not back to think
        s.on_stage(Stage::Skip, 10 * kS);
        assert(v.tick(true, s, true, 10 * kS + 100 * kMs) == kGrey);
        assert(v.tick(true, s, true, 10 * kS + kSkipUs) == kSpeak);
        // A stage after a skip starts fresh.
        s.on_stage(Stage::Listen, 11 * kS);
        assert(v.tick(true, s, false, 11 * kS) == kListen);
    }
    // Outside chat: nothing drawn, and the view forgets (the session also clears on leaving chat).
    {
        StageState s; DotsView v;
        s.on_stage(Stage::Listen, 0);
        assert(v.tick(true, s, false, 1 * kS) == kListen);
        assert(v.tick(false, s, false, 2 * kS) == kHidden);
        assert(v.tick(false, s, true, 3 * kS) == kHidden);                     // speaking outside chat: nothing
        s.clear();
        assert(v.tick(true, s, false, 4 * kS) == kIdle);                       // back in chat: idle
        // Without the session's clear, re-entering still re-reads the state rather than a stale local copy.
        StageState t; DotsView w;
        t.on_stage(Stage::Think, 0);
        assert(w.tick(true, t, false, 1 * kS) == kThink);
        assert(w.tick(true, t, true, 2 * kS) == kSpeak && w.tick(true, t, false, 3 * kS) == kIdle);   // local clear
        assert(w.tick(false, t, false, 4 * kS) == kHidden);
        assert(w.tick(true, t, false, 5 * kS) == kThink);                      // fresh copy (still within 8 s)
    }
    // Review Minor 4: a next-turn listen that arrives between tts.start and the first screen tick that sees speaking
    // is kept: "speech began" is tts.start's time (StageState.speak_from_us), not the tick's.
    {
        StageState s; DotsView v;
        s.on_stage(Stage::Think, 0);
        assert(v.tick(true, s, false, 1 * kS) == kThink);
        s.on_speech_start(2 * kS);                                             // tts.start on /mouth
        s.on_stage(Stage::Listen, 2 * kS + 5 * kMs);                           // the next turn, 5 ms later
        assert(v.tick(true, s, true, 2 * kS + 20 * kMs) == kSpeak);            // the first tick that sees speaking
        assert(v.tick(true, s, false, 5 * kS) == kListen);                     // kept
        // A listen from before tts.start is still cleared at the end of the speech.
        s.on_stage(Stage::Listen, 6 * kS);
        s.on_speech_start(6 * kS + 10 * kMs);
        assert(v.tick(true, s, true, 6 * kS + 20 * kMs) == kSpeak && v.tick(true, s, false, 8 * kS) == kIdle);
        // A stale speech start (an earlier speech) is not trusted: the tick's own edge counts then.
        s.on_stage(Stage::Listen, 9 * kS);
        assert(v.tick(true, s, false, 9 * kS) == kListen);
        assert(v.tick(true, s, true, 10 * kS) == kSpeak && v.tick(true, s, false, 11 * kS) == kIdle);
    }
    std::puts("chat stage ok");
}
