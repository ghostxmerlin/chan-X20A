// Host tests for the normal eye mode (collector/eye_mode.h) and the player features it uses.
#include "../upstream/StackChan/firmware/main/collector/eye_mode.h"
#include "../upstream/StackChan/firmware/main/collector/eyes/player.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <set>
using namespace collector;
using namespace collector::eyes;
namespace collector::eyes { bool valid_request_id(const char*); }

static Command command(unsigned n, Operation op = Operation::Play)
{
    Command c; std::snprintf(c.request_id, sizeof(c.request_id), "00000000-0000-4000-8000-%012x", n); c.op = op; return c;
}

int main()
{
    // emotion labels → eye states; neutral / unknown show nothing
    assert(eye_state_for_emotion("happy") == State::Happy && eye_state_for_emotion("sad") == State::Sad);
    assert(eye_state_for_emotion("angry") == State::Angry && eye_state_for_emotion("fearful") == State::Scared);
    assert(eye_state_for_emotion("disgusted") == State::Refusing && eye_state_for_emotion("surprised") == State::Surprised);
    for (const char* e : {"neutral", "unknown", "", "HAPPY"}) assert(eye_state_for_emotion(e) == State::Invalid);
    assert(eye_state_for_emotion(nullptr) == State::Invalid);

    // request ids are well-formed and distinct
    unsigned seed = 1;
    auto rnd = [&seed] { seed = seed * 1664525u + 1013904223u; return seed; };
    std::set<std::string> ids;
    for (int i = 0; i < 200; ++i) {
        char id[37];
        eye_request_id(id, rnd);
        assert(valid_request_id(id) && id[14] == '4' && std::strchr("89ab", id[19]));
        ids.insert(id);
    }
    assert(ids.size() == 200);

    // controller: configure once per vision change; retry after a refused submit
    EyeModeController m;
    assert(m.due(false, 0));                          // boot: make the home page explicit once
    m.applied(false, true, 0);
    assert(!m.due(false, 1) && m.due(true, 1));
    m.applied(true, false, 1);                         // e.g. storage not ready
    assert(!m.due(true, 2) && m.due(true, 1 + EyeModeController::kRetryUs));
    m.applied(true, true, 1 + EyeModeController::kRetryUs);
    assert(m.vision() && !m.due(true, 9000000) && m.due(false, 9000000));

    // voice cues no longer enable the eyes: without vision the home page stays
    {
        Player p; p.set_resources(true, true); p.set_gate(true, Error::None, 100); p.set_voice_auto_enable(false);
        auto voice = command(1); voice.state = State::Happy;
        const auto r = p.submit(voice, Source::Voice, 1000, 7);
        assert(!r.accepted && r.error == Error::Disabled && !p.snapshot().enabled);
        auto heard = command(2); heard.state = State::Sad;
        assert(p.submit(heard, Source::Hearing, 1000).error == Error::Disabled);
    }
    // with vision: natural cycle only uses the pool; hearing emotions play; voice outranks hearing
    {
        Player p; p.set_resources(true, true); p.set_gate(true, Error::None, 100);
        p.set_voice_auto_enable(false); p.set_natural_pool(kNormalNaturalPool);
        auto cfg = command(10, Operation::Configure); cfg.enabled = true; cfg.style = kNormalVisionStyle;
        assert(p.submit(cfg, Source::Manual, 200).accepted);
        p.loaded(p.snapshot().generation, 201);
        assert(p.snapshot().enabled && p.snapshot().natural_cycle && p.snapshot().selected_style == Style::V14);
        std::set<State> seen;
        int64_t t = 300;
        for (int round = 0; round < 60; ++round) {
            assert(p.presented(p.snapshot().generation, 0, 1, t));    // idle frame shown: schedules the next pick
            t += 8000000; p.tick(t);
            if (p.snapshot().source != Source::Natural) continue;
            seen.insert(p.snapshot().state);
            const auto gen = p.snapshot().generation;
            p.loaded(gen, t + 1);
            for (uint16_t f = 0; f < 8 * 3; ++f) { t += 400000; p.presented(gen, f % 8, 100 + f, t); }
            t += 400000; p.tick(t);
        }
        assert(!seen.empty());
        for (State s : seen) assert(eye_bit(s) & kNormalNaturalPool);
        auto heard = command(20); heard.state = State::Happy;
        assert(p.submit(heard, Source::Hearing, t + 10).accepted && p.snapshot().source == Source::Hearing);
        auto voice = command(21); voice.state = State::Sad; voice.mode = Mode::Loop; voice.loop_for_ms = 2000;
        assert(p.submit(voice, Source::Voice, t + 20, 9).accepted && p.snapshot().source == Source::Voice);
        auto late = command(22); late.state = State::Angry;
        assert(p.submit(late, Source::Hearing, t + 30).error == Error::Busy);   // never interrupts a voice cue
        auto manual = command(23); manual.state = State::Proud;
        assert(p.submit(manual, Source::Manual, t + 40).accepted);
        auto after = command(24); after.state = State::Happy;
        assert(p.submit(after, Source::Hearing, t + 50).error == Error::Busy);  // manual outranks hearing
    }
    std::puts("eye mode tests passed");
}
