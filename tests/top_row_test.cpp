// Home top row (fw/top_row.h, collector-0.4.98): what M12 / Wi-Fi / power values mean for the drawing.
//   M12: offline / off (standby) / hot (paused) / on, then person / face / recognised name from the last fresh line,
//   held 1 s against missed frames. Name: CJK font only with every glyph, ASCII names in the ASCII font, else "known".
//   Wi-Fi: -1 off, 0 unknown signal, 1..4 bars. Battery: unknown / low <= 15 / mid <= 30 / ok, fill width.
#include "top_row.h"
#include "m12_unit.h"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace collector;
using namespace collector::top;

namespace {
constexpr int64_t kMs = 1000, kS = 1000000;
M12Snapshot detecting(int64_t frame_us)
{
    M12Snapshot m;
    m.valid = m.online = true;
    m.function = M12Function::detect;
    m.requested_us = 1 * kS;
    m.frame_us = frame_us;
    return m;
}
void add_face(M12Snapshot& m, int w, int h, const char* name = "")
{
    auto& f = m.faces[m.count++];
    f.w = int16_t(w); f.h = int16_t(h);
    std::snprintf(f.name, sizeof f.name, "%s", name);
}
Vision on(bool person, bool face, const char* name = "")
{
    Vision v;
    v.power = VisionPower::On; v.person = person; v.face = face;
    std::snprintf(v.name, sizeof v.name, "%s", name);
    return v;
}
}

int main()
{
    // ---- vision_from ----
    {
        M12Snapshot m;
        assert(vision_from(m, 10 * kS).power == VisionPower::Offline);           // no serial
        m.valid = true;
        assert(vision_from(m, 10 * kS).power == VisionPower::Offline);           // no heartbeat
        m.online = true;
        assert(vision_from(m, 10 * kS).power == VisionPower::Off);               // function none (not confirmed)
        m.function = M12Function::standby;
        assert(vision_from(m, 10 * kS).power == VisionPower::Off);
        m.function = M12Function::detect;
        m.hb.tier = 3;
        assert(vision_from(m, 10 * kS).power == VisionPower::Hot);               // paused, too hot
        m.hb.tier = 2;
        const Vision v = vision_from(m, 10 * kS);
        assert(v == on(false, false));                                         // detecting, no line yet
    }
    {
        // A fresh line with nobody, a person only, a face (= person too), and named faces.
        M12Snapshot m = detecting(9 * kS);
        assert(vision_from(m, 10 * kS) == on(false, false));
        m.persons = 1;
        assert(vision_from(m, 10 * kS) == on(true, false));
        m.persons = 0;
        add_face(m, 40, 40);
        assert(vision_from(m, 10 * kS) == on(true, true));                     // close-up: face, no body
        add_face(m, 30, 30, "Alice");
        add_face(m, 80, 80, "张三");                                            // largest named face wins
        assert(vision_from(m, 10 * kS) == on(true, true, "张三"));
        assert(vision_from(m, 10 * kS).known());
        // Stale (>= 1.5 s) or older than the last request: says nothing (still on).
        assert(vision_from(m, 9 * kS + 1500 * kMs) == on(false, false));
        assert(vision_from(m, 9 * kS + 1499 * kMs) == on(true, true, "张三"));
        m.requested_us = 9 * kS;
        assert(vision_from(m, 10 * kS) == on(false, false));
        // Offline beats everything in the last line.
        m.requested_us = 1 * kS; m.online = false;
        assert(vision_from(m, 10 * kS) == Vision{});
    }
    // ---- VisionHold ----
    {
        VisionHold h;
        assert(h.tick(on(false, false), 0) == on(false, false));
        assert(h.tick(on(true, true, "Alice"), 100 * kMs) == on(true, true, "Alice"));
        // A missed frame (or a few) keeps what was seen for 1 s; then it goes.
        assert(h.tick(on(false, false), 400 * kMs) == on(true, true, "Alice"));
        assert(h.tick(on(true, false), 1099 * kMs) == on(true, true, "Alice"));
        assert(h.tick(on(true, false), 1100 * kMs) == on(true, false));
        assert(h.tick(on(false, false), 2099 * kMs) == on(true, false));
        assert(h.tick(on(false, false), 2100 * kMs) == on(false, false));
        // A new name replaces the held one at once.
        assert(h.tick(on(true, true, "Alice"), 3000 * kMs) == on(true, true, "Alice"));
        assert(h.tick(on(true, true, "Bob"), 3100 * kMs) == on(true, true, "Bob"));
        // A held face is a person too.
        assert(h.tick(on(false, false), 3500 * kMs) == on(true, true, "Bob"));
        // Vision off: at once, nothing held; on again starts empty.
        Vision off; off.power = VisionPower::Off;
        assert(h.tick(off, 3600 * kMs) == off);
        assert(h.tick(on(false, false), 3700 * kMs) == on(false, false));
        Vision hot; hot.power = VisionPower::Hot;
        assert(h.tick(hot, 3800 * kMs) == hot);
    }
    // ---- names ----
    {
        size_t i = 0; uint32_t cp = 0;
        const char* zh = "张三";
        assert(utf8_next(zh, i, cp) && cp == 0x5F20 && i == 3 && utf8_next(zh, i, cp) && cp == 0x4E09 && !utf8_next(zh, i, cp));
        const auto any = [](uint32_t) { return true; };
        const auto bmp_ascii = [](uint32_t c) { return c < 0x80; };
        assert(all_chars("Alice", any) && all_chars("张三", any) && all_chars("", any));
        assert(!all_chars("\xE5\xBC", any));                       // cut in the middle of a character
        assert(!all_chars("\xC0\xAF", any));                       // overlong
        assert(!all_chars("\xFF", any) && !all_chars(nullptr, any));
        assert(all_chars("Bob 2", bmp_ascii) && !all_chars("Bob张", bmp_ascii));
        assert(name_font("", true) == NameFont::None && name_font(nullptr, false) == NameFont::None);
        assert(name_font("张三", true) == NameFont::Cjk);
        assert(name_font("张三", false) == NameFont::Stand);          // no font or a missing glyph: "known"
        assert(name_font("Alice", false) == NameFont::Ascii && name_font("Alice", true) == NameFont::Cjk);
        assert(ascii_text(kKnownStandIn));
        // A long name is cut between characters (CJK 3 bytes, 16 px; ASCII 9 px), never through one.
        const auto w = [](uint32_t c) { return c < 0x80 ? 9 : 16; };
        assert(fit_prefix("张三李四", 64, w) == 12 && fit_prefix("张三李四", 63, w) == 9 && fit_prefix("张三李四", 15, w) == 0);
        assert(fit_prefix("Alexandria", 45, w) == 5 && fit_prefix("Alexandria", 200, w) == 10);
        assert(fit_prefix("Al张", 33, w) == 2 && fit_prefix("Al张", 34, w) == 5);
        assert(fit_prefix("ab\xE5\xBC", 100, w) == 2 && fit_prefix(nullptr, 100, w) == 0 && fit_prefix("", 100, w) == 0);
    }
    // ---- Wi-Fi ----
    {
        assert(wifi_bars(false, true, -40) == -1);
        assert(wifi_bars(true, false, 0) == 0 && wifi_bars(true, true, 0) == 0);
        assert(wifi_bars(true, true, -40) == 4 && wifi_bars(true, true, -55) == 4);
        assert(wifi_bars(true, true, -56) == 3 && wifi_bars(true, true, -65) == 3);
        assert(wifi_bars(true, true, -66) == 2 && wifi_bars(true, true, -75) == 2);
        assert(wifi_bars(true, true, -76) == 1 && wifi_bars(true, true, -95) == 1);
    }
    // ---- Battery ----
    {
        assert(battery_from(false, 80, true) == Battery{});
        const Battery low = battery_from(true, 15, false), mid = battery_from(true, 30, true), ok = battery_from(true, 31, false);
        assert(low.level == BatteryLevel::Low && low.percent == 15 && !low.charging);
        assert(mid.level == BatteryLevel::Mid && mid.charging);
        assert(ok.level == BatteryLevel::Ok);
        assert(battery_from(true, 140, false).percent == 100 && battery_from(true, -3, false).percent == 0);
        assert(battery_from(true, -3, false).level == BatteryLevel::Low);
        assert(battery_fill(battery_from(true, 100, false), 19) == 19);
        assert(battery_fill(battery_from(true, 50, false), 19) == 9);
        assert(battery_fill(battery_from(true, 1, false), 19) == 1);
        assert(battery_fill(battery_from(true, 0, false), 19) == 0 && battery_fill(Battery{}, 19) == 0);
    }
    // ---- layout ----
    {
        const auto w = [](uint32_t c) { return c < 0x80 ? 9 : 16; };
        assert(text_width("100", w) == 27 && text_width("张三", w) == 32 && text_width("", w) == 0);
        // The widest badge ("SUB ZH>EN", 89 px) with everything on: in order, nothing overlaps, room for a name.
        for (int badge : {44, 80, 89}) for (bool stage : {false, true}) for (bool on : {false, true}) for (bool bolt : {false, true}) {
            const int width = 320 - 6 - 6 - badge;
            const Layout l = layout(width, stage, on, 27, bolt);
            int x = 0;
            if (stage) { assert(l.blocks == 0); x = kBlocksW; } else assert(l.blocks == -1);
            assert(l.lens >= x + (stage ? kGroupGap : 0)); x = l.lens + kLensPx;
            if (on) { assert(l.person > x && l.face > l.person + kPersonW); x = l.face + kFaceW; }
            else assert(l.person == -1 && l.face == -1);
            assert(l.name > x && l.name_w >= 39);
            x = l.name + l.name_w;
            assert(l.wifi > x && l.pct >= l.wifi + kBarsW + kPctGap);
            x = l.pct + 27;
            if (bolt) { assert(l.bolt > x); x = l.bolt + kBoltW; } else assert(l.bolt == -1);
            assert(l.battery > x && l.battery + kBatW + kBatNub == width);
        }
        // A very narrow row never gives a negative name width.
        assert(layout(60, true, true, 27, true).name_w == 0);
        // 0.5.00 burst group: between the name and the Wi-Fi bars, its width taken from the name's room only.
        assert(burst_w(18) == kEyeW + kEyeGap + 18);
        for (int badge : {44, 80, 89}) for (bool stage : {false, true}) for (bool bolt : {false, true}) {
            const int width = 320 - 6 - 6 - badge;
            const Layout plain = layout(width, stage, true, 27, bolt);
            assert(plain.burst == -1 && plain.burst_w == 0);
            for (int count_w : {9, 18, 27}) {
                const Layout l = layout(width, stage, true, 27, bolt, burst_w(count_w));
                assert(l.burst_w == burst_w(count_w));
                assert(l.burst + l.burst_w + kGroupGap == l.wifi);
                // the name keeps the rest (never negative: on the widest badge with everything it can reach 0)
                assert(l.name_w == std::max(0, plain.name_w - kGroupGap - l.burst_w));
                if (l.name_w > 0) assert(l.name + l.name_w + kGroupGap == l.burst);
                assert(l.wifi == plain.wifi && l.pct == plain.pct && l.battery == plain.battery && l.name == plain.name);
                // A four-letter badge (MEET / TALK), blocks, 100 %, not charging, two-digit count: room for a
                // 3-character Chinese name (16 px each).
                if (badge == 44 && !bolt && count_w == 18) assert(l.name_w >= 48);
                // burst_fit: no name or a name that fits = the full group; a longer one = the eye alone.
                assert(burst_fit(width, stage, true, 27, bolt, count_w, 0) == burst_w(count_w));
                assert(burst_fit(width, stage, true, 27, bolt, count_w, l.name_w) == burst_w(count_w));
                assert(burst_fit(width, stage, true, 27, bolt, count_w, l.name_w + 1) == kEyeW);
                const Layout eye = layout(width, stage, true, 27, bolt, kEyeW);
                assert(eye.burst_w == kEyeW && eye.name_w == std::max(0, plain.name_w - kGroupGap - kEyeW) && eye.name_w >= l.name_w);
                assert(eye.name_w >= 10);       // even the widest badge, blocks, bolt and 100 %: a little of the name
            }
        }
        assert(layout(60, true, true, 27, true, burst_w(18)).name_w == 0);
    }
    std::puts("top row ok");
}
