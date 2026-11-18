// Words under the eyes for M12 events (collector-0.5.03, m12_words.h): packing, the lock-free feed, which word,
// cooldowns, a busy screen, and that every word's characters are in the compiled glyph set.
#include "../upstream/StackChan/firmware/main/collector/m12_words.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
using namespace collector;
using namespace collector::m12words;

static M12Event ev(M12EvType t) { M12Event e; e.type = t; return e; }
static std::string pick(Picker& p, M12Event e, uint32_t ms, bool busy = false)
{
    const char* w = p.pick(unpack(pack(e)), ms, busy);
    return w ? w : "";
}
// UTF-8 code points of s, one string each
static void each_char(const char* s, void (*fn)(const std::string&))
{
    for (const char* q = s; *q;) {
        const unsigned char c = static_cast<unsigned char>(*q);
        const int n = c < 0x80 ? 1 : c < 0xE0 ? 2 : c < 0xF0 ? 3 : 4;
        fn(std::string(q, n)); q += n;
    }
}

int main()
{
    // pack / unpack keep what the words depend on
    M12Event a = ev(M12EvType::uncover); a.dur = 3.24f;
    Ev u = unpack(pack(a)); assert(u.type == M12EvType::uncover && u.dur > 3.15f && u.dur < 3.35f && u.n == -1 && u.on == -1);
    M12Event b = ev(M12EvType::nod); b.n = 2; u = unpack(pack(b)); assert(u.n == 2 && u.dur < 0);
    M12Event c = ev(M12EvType::light); c.on = 0; u = unpack(pack(c)); assert(u.on == 0);
    M12Event d = ev(M12EvType::wake); d.pan = true; u = unpack(pack(d)); assert(u.pan);
    M12Event big = ev(M12EvType::leave); big.dur = 9999.f; assert(unpack(pack(big)).dur > 409.f);

    // feed: in order, an overwritten slot is reported gone
    Feed f; Ev out;
    for (int i = 0; i < 3; ++i) f.push(ev(M12EvType::nod));
    assert(f.head() == 3 && f.get(1, out) && out.type == M12EvType::nod);
    for (int i = 0; i < 10; ++i) f.push(ev(M12EvType::look));
    assert(f.head() == 13 && !f.get(3, out) && f.get(13, out) && out.type == M12EvType::look);

    // words: replies, in turn; nothing for look_end / smile / a turned M12
    Picker p;
    assert(pick(p, ev(M12EvType::look), 1000) == "你好");
    assert(pick(p, ev(M12EvType::nod), 2000) == "");                 // within 4 s of the last word
    assert(pick(p, ev(M12EvType::nod), 5000) == "嗯嗯");
    assert(pick(p, ev(M12EvType::look), 10000) == "");               // look: 30 s cooldown
    assert(pick(p, ev(M12EvType::look), 31000) == "嗨");
    assert(pick(p, ev(M12EvType::look_end), 40000) == "");
    assert(pick(p, ev(M12EvType::smile), 50000) == "");
    assert(pick(p, d, 60000) == "");                                  // wake with pan
    assert(pick(p, ev(M12EvType::wake), 70000) == "嗯？");
    M12Event peek = ev(M12EvType::uncover); peek.dur = 4;
    assert(pick(p, peek, 80000) == "找到你啦");
    peek.dur = 40; assert(pick(p, peek, 90000) == "哇");
    assert(pick(p, c, 100000) == "好黑");
    M12Event noon = ev(M12EvType::light); assert(pick(p, noon, 120000) == "");   // light without on
    // a mode toast on screen wins, and does not use up the cooldown
    assert(pick(p, ev(M12EvType::enter), 130000, true) == "");
    assert(pick(p, ev(M12EvType::enter), 130500) == "来啦");
    assert(p.shown() == 8);

    // every character of every word is in kGlyphs (else the robot draws a box)
    static std::string glyphs; glyphs = kGlyphs;
    for (int t = 0; t < int(M12EvType::count); ++t)
        for (int on = -1; on <= 1; ++on)
            for (float dur : {-1.f, 3.f, 40.f}) {
                Ev e; e.type = M12EvType(t); e.on = on; e.dur = dur;
                uint8_t n = 0; const char* const* list = words(e, n);
                for (uint8_t i = 0; list && i < n; ++i)
                    each_char(list[i], [](const std::string& ch) {
                        if (glyphs.find(ch) == std::string::npos) { std::printf("missing glyph %s\n", ch.c_str()); assert(false); }
                    });
            }
    std::puts("m12_words_test: ok");
}
