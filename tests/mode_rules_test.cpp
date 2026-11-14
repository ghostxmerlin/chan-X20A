// hello modes step 1b (collector-0.4.92): hello owns the mode. Replays every vector of tests/data/mode_vectors.json
// (tests/gen_mode_vectors.py, made from server/mind/modes.py + mode_table.py + the owner's digit table) through
// fw/mode_rules.h and requires the same new state, result code, toast and effects in order; checks the firmware
// mode table (label, color, eye, pool, touch) against the vectors' table.
#include "mode_rules.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
using namespace collector::modes::rules;
using collector::modes::Eye;
using collector::modes::Pool;
using collector::modes::Snapshot;
using collector::modes::TouchAct;
using namespace ArduinoJson;

namespace {
int failures = 0;

State state_from(JsonArrayConst a)
{
    State s;
    s.mode = mode_from(a[0].as<const char*>());
    assert(s.mode != Mode::None);
    s.dir = a[1].isNull() ? Dir::None : !std::strcmp(a[1].as<const char*>(), "zh_en") ? Dir::ZhEn : Dir::EnZh;
    s.hosted = !a[2].isNull();
    s.return_to = a[3].isNull() ? Mode::None : mode_from(a[3].as<const char*>());
    return s;
}
std::string show(const State& s)
{
    std::string o = "[";
    o += mode_id(s.mode); o += ","; o += s.dir == Dir::None ? "null" : dir_id(s.dir); o += ",";
    o += s.hosted ? "event" : "null"; o += ","; o += s.return_to == Mode::None ? "null" : mode_id(s.return_to);
    return o + "]";
}

void table(JsonObjectConst t)
{
    static constexpr const char* kEyes[] = {"laughing_man", "v05", "v14", "v18", "page_subtitle", "page_settings", "page_idle"};
    static constexpr const char* kTouch[] = {"server", "badge", "none"};
    size_t n = 0;
    for (JsonPairConst p : t) {
        const Mode m = mode_from(p.key().c_str());
        assert(m != Mode::None);
        const ModeDef& d = def(m);
        const JsonObjectConst row = p.value().as<JsonObjectConst>();
        char color[8];
        std::snprintf(color, sizeof color, "%06X", static_cast<unsigned>(d.color));
        assert(!std::strcmp(d.label, row["label"].as<const char*>()));
        assert(!std::strcmp(color, row["color"].as<const char*>()));
        assert(!std::strcmp(kEyes[static_cast<size_t>(d.eye)], row["eye"].as<const char*>()));
        assert(row["pool"].isNull() ? d.pool == Pool::Default : (d.pool == Pool::Lively && row["pool"] == "lively"));
        assert(!std::strcmp(kTouch[static_cast<size_t>(d.tap)], row["tap"].as<const char*>()));
        assert(!std::strcmp(kTouch[static_cast<size_t>(d.dbl)], row["double"].as<const char*>()));
        if (m == Mode::Subtitle) {
            assert(!std::strcmp(kSubtitleLabels[1], row["labels"]["zh_en"].as<const char*>()));
            assert(!std::strcmp(kSubtitleLabels[2], row["labels"]["en_zh"].as<const char*>()));
        } else {
            assert(row["labels"].isNull());
        }
        ++n;
    }
    assert(n == kModeCount);
}

void vector(JsonArrayConst v, size_t i)
{
    const State st = state_from(v[0].as<JsonArrayConst>());
    const Input in = input_from(v[1].as<const char*>());
    const Value val = value_from(in, v[2]);
    const bool speaking = v[3].as<bool>();
    const Outcome o = transition(st, in, val, speaking);
    const State want = state_from(v[4].as<JsonArrayConst>());
    bool ok = o.state == want && !std::strcmp(code_id(o.code), v[5].as<const char*>()) &&
              !std::strcmp(o.toast, v[6].as<const char*>());
    const JsonArrayConst fx = v[7].as<JsonArrayConst>();
    ok = ok && fx.size() == o.n_effects;
    for (size_t k = 0; ok && k < o.n_effects; ++k) ok = !std::strcmp(effect_id(o.effects[k]), fx[k].as<const char*>());
    if (!ok) {
        if (++failures <= 20) {
            std::string got;
            for (size_t k = 0; k < o.n_effects; ++k) { got += effect_id(o.effects[k]); got += ' '; }
            std::string text;
            serializeJson(v, text);
            std::fprintf(stderr, "vector %zu %s\n  got %s %s \"%s\" [%s]\n", i, text.c_str(), show(o.state).c_str(),
                         code_id(o.code), o.toast, got.c_str());
        }
    }
}

// Things the vectors do not reach: unknown digits, look(), the id helpers.
void extras()
{
    State talk;
    Value v;
    for (int d : {-1, 0, 9, 10}) {
        v.digit = d;
        const Outcome o = transition(talk, Input::Digit, v, false);
        assert(o.code == Code::UnknownInput && o.state == talk && !o.n_effects && !std::strcmp(o.toast, "NO EFFECT"));
    }
    JsonDocument doc;
    deserializeJson(doc, "[12, 4, \"4\", {\"mode\":\"crowd\"}]");
    assert(value_from(Input::Digit, doc[0]).digit == -1 && value_from(Input::Digit, doc[1]).digit == 4);
    assert(value_from(Input::Digit, doc[2]).digit == -1);
    assert(value_from(Input::Select, doc[3]).target == Mode::Crowd);
    assert(input_from("billing") == Input::Unknown && input_from(nullptr) == Input::Unknown);
    assert(!std::strcmp(input_id(Input::DoubleTap), "double_tap") && !std::strcmp(input_id(Input::Unknown), "unknown"));

    State sub; sub.mode = Mode::Subtitle; sub.dir = Dir::EnZh;
    const Snapshot s = look(sub, 7);
    assert(s.seq == 7 && !std::strcmp(s.mode, "subtitle") && !std::strcmp(s.dir, "en_zh") && !std::strcmp(s.label, "SUB EN>ZH"));
    assert(s.eye == Eye::PageSubtitle && s.color == 0x7C6CF0 && s.tap == TouchAct::None);
    const Snapshot t = look(talk, 1);
    assert(!std::strcmp(t.mode, "talk") && !std::strcmp(t.dir, "") && t.eye == Eye::LaughingMan && !std::strcmp(t.label, "TALK"));
    State set; set.mode = Mode::Settings; set.return_to = Mode::Subtitle; set.dir = Dir::ZhEn;
    assert(!std::strcmp(look(set, 2).label, "SET"));
}
}

int main(int argc, char** argv)
{
    assert(argc == 2);
    std::ifstream f(argv[1]);
    std::stringstream ss;
    ss << f.rdbuf();
    const std::string text = ss.str();
    JsonDocument doc;
    const auto err = deserializeJson(doc, text);
    assert(!err);
    table(doc["table"].as<JsonObjectConst>());
    const JsonArrayConst vectors = doc["vectors"].as<JsonArrayConst>();
    assert(vectors.size() > 1000);
    size_t i = 0;
    for (JsonArrayConst v : vectors) vector(v, i++);
    extras();
    if (failures) { std::fprintf(stderr, "%d of %zu vectors differ\n", failures, i); return 1; }
    std::printf("mode rules ok: %zu vectors\n", i);
}
