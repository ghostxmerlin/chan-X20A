// Robot command line (fw/robot_cmd.h, collector-0.5.10): parsing each verb's args, the no-id / unknown / bad / glyph
// answers, the per-verb rate limit, the word glyph check, volume steps and the word that stays.
#include "robot_cmd.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
using namespace collector;
using namespace collector::cmd;

namespace {
// The JSON as mouth_modes.h read_text leaves it (the type is checked there, not here).
Parse run(const char* text, Cmd& c)
{
    ArduinoJson::JsonDocument doc;
    const bool parsed = !ArduinoJson::deserializeJson(doc, text);
    assert(parsed);
    (void)parsed;
    return parse(doc.as<ArduinoJson::JsonObjectConst>(), std::strlen(text), c);
}
}

int main()
{
    Cmd c;
    // ids: missing, zero, negative, bool, string, too long -> NoId (no answer possible)
    for (const char* t : {R"({"type":"cmd","do":"status"})", R"({"type":"cmd","id":0,"do":"status"})",
                          R"({"type":"cmd","id":-3,"do":"status"})", R"({"type":"cmd","id":true,"do":"status"})",
                          R"({"type":"cmd","id":"7","do":"status"})"})
        assert(run(t, c) == Parse::NoId);
    {
        std::string big = R"({"type":"cmd","id":5,"do":"status","pad":")" + std::string(600, 'x') + "\"}";
        assert(run(big.c_str(), c) == Parse::NoId);
    }
    assert(run(R"({"type":"cmd","id":4294967295,"do":"status"})", c) == Parse::Ok && c.id == 4294967295u && c.verb == Verb::Status);
    assert(run(R"({"type":"cmd","id":9,"do":"fly"})", c) == Parse::UnknownVerb && c.id == 9);
    assert(run(R"({"type":"cmd","id":9})", c) == Parse::UnknownVerb);
    assert(run(R"({"type":"cmd","id":9,"do":"status","args":[1]})", c) == Parse::BadArgs);
    assert(!std::strcmp(parse_error(Parse::NoGlyph), "no_glyph") && !std::strcmp(parse_error(Parse::BadArgs), "bad_args"));

    // eyes
    assert(run(R"({"id":1,"do":"eyes","args":{"state":"happy"}})", c) == Parse::Ok && c.verb == Verb::Eyes && !std::strcmp(c.text, "happy"));
    assert(run(R"({"id":1,"do":"eyes","args":{}})", c) == Parse::BadArgs);
    assert(run(R"({"id":1,"do":"eyes","args":{"state":"abcdefghijklmnopqrstu"}})", c) == Parse::BadArgs);   // 21 B
    assert(run(R"({"id":1,"do":"eyes","args":{"state":7}})", c) == Parse::BadArgs);

    // word: glyphs, default and bounded hold, empty = clear
    assert(run(R"({"id":2,"do":"word","args":{"text":"安静中","hold_ms":600000}})", c) == Parse::Ok);
    assert(c.verb == Verb::Word && !std::strcmp(c.text, "安静中") && c.hold_ms == 600000);
    assert(run(R"({"id":2,"do":"word","args":{"text":"查好了"}})", c) == Parse::Ok && c.hold_ms == kWordDefaultMs);
    assert(run(R"({"id":2,"do":"word","args":{"text":""}})", c) == Parse::Ok && !*c.text);
    assert(run(R"({"id":2,"do":"word","args":{"text":"OK"}})", c) == Parse::NoGlyph);            // ASCII: not in the font
    assert(run(R"({"id":2,"do":"word","args":{"text":"晚饭"}})", c) == Parse::NoGlyph);
    assert(run(R"({"id":2,"do":"word","args":{"text":"你好","hold_ms":3600001}})", c) == Parse::BadArgs);
    assert(run(R"({"id":2,"do":"word","args":{"text":"你好","hold_ms":-1}})", c) == Parse::BadArgs);
    assert(run(R"({"id":2,"do":"word","args":{"text":"你好你好你好你好你好你好"}})", c) == Parse::BadArgs);  // 36 B
    assert(run(R"({"id":2,"do":"word","args":{}})", c) == Parse::BadArgs);
    for (const char* w : m12words::kCmdWords) {              // every word the font was made for is drawable
        if (!glyphs_cover(w, m12words::kGlyphs)) { std::printf("missing glyph in %s\n", w); assert(false); }
    }
    assert(glyphs_cover("", m12words::kGlyphs) && !glyphs_cover("\xe5\xae", m12words::kGlyphs));   // cut UTF-8
    assert(!glyphs_cover("\x80", "\x80"));

    // clip
    assert(run(R"({"id":3,"do":"clip","args":{"clip_id":80}})", c) == Parse::Ok && c.value == 80);
    assert(run(R"({"id":3,"do":"clip","args":{"clip_id":0}})", c) == Parse::BadArgs);
    assert(run(R"({"id":3,"do":"clip","args":{"clip_id":81}})", c) == Parse::BadArgs);
    assert(run(R"({"id":3,"do":"clip","args":{"emotion":"happy"}})", c) == Parse::Ok && !c.value && !std::strcmp(c.text, "happy"));
    assert(run(R"({"id":3,"do":"clip"})", c) == Parse::Ok && !c.value && !*c.text);          // random

    // yaw / front / turn_around / tracking
    int slot = 9;
    assert(yaw_slot("left90", slot) && slot == -2 && yaw_slot("right45", slot) && slot == 1 && !yaw_slot("front", slot));
    assert(run(R"({"id":4,"do":"yaw","args":{"to":"left45"}})", c) == Parse::Ok && c.verb == Verb::Yaw && c.value == -1);
    assert(run(R"({"id":4,"do":"yaw","args":{"to":"up"}})", c) == Parse::BadArgs);
    assert(run(R"({"id":4,"do":"front"})", c) == Parse::Ok && c.verb == Verb::Front);
    assert(run(R"({"id":4,"do":"turn_around","args":{}})", c) == Parse::Ok && c.verb == Verb::TurnAround);
    assert(run(R"({"id":4,"do":"tracking","args":{"on":true}})", c) == Parse::Ok && c.on);
    assert(run(R"({"id":4,"do":"tracking","args":{"on":1}})", c) == Parse::BadArgs);

    // nod / shake: default two cycles (owner default), 1..2
    assert(run(R"({"id":5,"do":"nod"})", c) == Parse::Ok && c.verb == Verb::Nod && c.value == 2);
    assert(run(R"({"id":5,"do":"shake","args":{"times":1}})", c) == Parse::Ok && c.verb == Verb::Shake && c.value == 1);
    assert(run(R"({"id":5,"do":"nod","args":{"times":3}})", c) == Parse::BadArgs);
    assert(run(R"({"id":5,"do":"nod","args":{"times":true}})", c) == Parse::BadArgs);

    // volume: a level or a step, never both
    assert(run(R"({"id":6,"do":"volume","args":{"level":60}})", c) == Parse::Ok && volume_after(c, 80) == 60);
    assert(run(R"({"id":6,"do":"volume","args":{"step":-30}})", c) == Parse::Ok && volume_after(c, 30) == 10 && volume_after(c, 80) == 50);
    assert(run(R"({"id":6,"do":"volume","args":{"step":30}})", c) == Parse::Ok && volume_after(c, 90) == 100);
    assert(run(R"({"id":6,"do":"volume","args":{"level":60,"step":10}})", c) == Parse::BadArgs);
    assert(run(R"({"id":6,"do":"volume","args":{}})", c) == Parse::BadArgs);
    assert(run(R"({"id":6,"do":"volume","args":{"level":5}})", c) == Parse::BadArgs);

    // face_learn
    assert(run(R"({"id":7,"do":"face_learn","args":{"name":"小王"}})", c) == Parse::Ok && !std::strcmp(c.text, "小王"));
    assert(run(R"({"id":7,"do":"face_learn","args":{"name":""}})", c) == Parse::BadArgs);
    assert(run(R"({"id":7,"do":"face_learn","args":{"name":"a\nb"}})", c) == Parse::BadArgs);
    assert(run(R"({"id":7,"do":"face_learn","args":{"name":"123456789012345678901234567890123"}})", c) == Parse::BadArgs);

    // rate limit: 2 per 10 s per A-level verb; volume / face_learn / status are free
    {
        RateLimit r;
        constexpr int64_t s = 1000000;
        assert(r.allow(Verb::Nod, 1 * s) && r.allow(Verb::Nod, 2 * s) && !r.allow(Verb::Nod, 3 * s));
        assert(r.allow(Verb::Shake, 3 * s));                                   // its own budget
        assert(!r.allow(Verb::Nod, 1 * s + kRateWindowUs - 1) && r.allow(Verb::Nod, 1 * s + kRateWindowUs));
        assert(!r.allow(Verb::Nod, 2 * s + kRateWindowUs - 1) && r.allow(Verb::Nod, 2 * s + kRateWindowUs));
        for (int i = 0; i < 10; ++i) assert(r.allow(Verb::Status, 5 * s) && r.allow(Verb::Volume, 5 * s) && r.allow(Verb::FaceLearn, 5 * s));
        assert(r.allow(Verb::Word, 0) && r.allow(Verb::Word, 0) && !r.allow(Verb::Word, 1));   // a time of 0 still counts
    }

    // the word that stays
    {
        StickyWord w;
        w.set("查好了", 1000, kWordDefaultMs);                                // a one-shot word never stays
        assert(!w.on && !w.due(2000, false));
        w.set("安静中", 1000, 600000);
        assert(w.on && !w.due(1500, true) && !std::strcmp(w.due(1500, false), "安静中"));
        assert(w.due(600999, false) && !w.due(601000, false) && !w.on);          // ends by itself
        w.set("少插话", 0, 10000);
        w.clear();
        assert(!w.due(1, false));
    }
    for (int v = 0; v < int(Verb::Count); ++v) {
        Verb back;
        assert(verb_from(verb_id(Verb(v)), back) && back == Verb(v));
    }
    std::puts("robot_cmd ok");
    return 0;
}
