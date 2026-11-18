#pragma once
// collector-0.5.03 (2026-11-18): a short word under the eyes for some M12 events (m12d 0.2.0), the robot's reply to
// what it saw, not the event's name: a face turns to it -> 你好, a nod -> 嗯嗯, covered -> 看不见啦, uncovered -> 找到你啦.
// Shown in the toast row for 2 s, drawn with collector_words_20 (only these words' glyphs, compiled in: the SD CJK
// font is not installed). Owner 2026-11-18: interactive words, never the state itself.
//
// The M12 task hands events over lock free (Feed: a packed word per slot, a release sequence); the screen loop
// polls it, Picker decides. Rate: at most one word per kGapMs, each event type its own cooldown, a mode toast on
// screen wins (the caller passes busy). Nothing here reacts beyond the screen: no sound, no motion.
#include "m12_protocol.h"
#include <atomic>
#include <cstdint>

namespace collector::m12words {

// The fields a word depends on, packed into 24 bits: type 5, n 4 (0 = absent), on 2 (0 absent / 1 off / 2 on),
// pan 1, dur in 0.1 s (12 bits, 0 = absent, capped at 409.5 s).
struct Ev {
    M12EvType type = M12EvType::wake;
    int n = -1, on = -1;
    bool pan = false;
    float dur = -1;
};
inline uint32_t pack(const M12Event& e)
{
    const uint32_t n = e.n > 0 ? (e.n > 15 ? 15u : uint32_t(e.n)) : 0u;
    const uint32_t on = e.on < 0 ? 0u : e.on ? 2u : 1u;
    uint32_t dur = 0;
    if (e.dur >= 0) { const float ds = e.dur * 10.f + 0.5f; dur = ds >= 4095.f ? 4095u : (ds < 1.f ? 1u : uint32_t(ds)); }
    return (uint32_t(e.type) & 0x1f) | n << 5 | on << 9 | (e.pan ? 1u : 0u) << 11 | dur << 12;
}
inline Ev unpack(uint32_t w)
{
    Ev e;
    const uint32_t t = w & 0x1f;
    e.type = t < uint32_t(M12EvType::count) ? M12EvType(t) : M12EvType::count;
    const uint32_t n = (w >> 5) & 0xf, on = (w >> 9) & 3, dur = (w >> 12) & 0xfff;
    e.n = n ? int(n) : -1;
    e.on = on ? int(on) - 1 : -1;
    e.pan = (w >> 11) & 1;
    e.dur = dur ? dur / 10.f : -1;
    return e;
}

// Single writer (the M12 task), single reader (the screen loop). A slot holds (seq low byte << 24 | packed); the
// reader takes slot seq % kSlots only when its byte matches, so a slot overwritten meanwhile is skipped, not misread.
class Feed {
public:
    static constexpr uint32_t kSlots = 8;
    void push(const M12Event& e)
    {
        const uint32_t seq = head_.load(std::memory_order_relaxed) + 1;
        slot_[seq % kSlots].store((seq & 0xff) << 24 | (pack(e) & 0xffffff), std::memory_order_relaxed);
        head_.store(seq, std::memory_order_release);
    }
    uint32_t head() const { return head_.load(std::memory_order_acquire); }
    // the event `seq` (<= head(), not older than kSlots); false if it is gone
    bool get(uint32_t seq, Ev& out) const
    {
        const uint32_t w = slot_[seq % kSlots].load(std::memory_order_relaxed);
        if ((w >> 24) != (seq & 0xff)) return false;
        out = unpack(w & 0xffffff);
        return out.type != M12EvType::count;
    }
private:
    std::atomic<uint32_t> head_{0};
    std::atomic<uint32_t> slot_[kSlots] = {};
};

constexpr uint32_t kGapMs = 4000;          // any two words
inline uint32_t cooldown_ms(M12EvType t)
{
    switch (t) {
    case M12EvType::look: case M12EvType::wake: return 30000;
    case M12EvType::enter: case M12EvType::leave: return 20000;
    case M12EvType::near: case M12EvType::far: return 15000;
    case M12EvType::light: case M12EvType::dark: case M12EvType::bright: return 10000;
    case M12EvType::nod: case M12EvType::shake: case M12EvType::tilt: return 6000;
    default: return 5000;                  // cover / uncover
    }
}
// The words, by event; several = taken in turn. nullptr = no word for that event (look_end, smile, a wake from the
// M12 being turned).
inline const char* const* words(const Ev& e, uint8_t& count)
{
    static const char* const look[] = {"你好", "嗨", "在呢"};
    static const char* const nod[] = {"嗯嗯", "好呀"};
    static const char* const shake[] = {"好吧", "不要吗"};
    static const char* const tilt[] = {"嗯？", "怎么啦"};
    static const char* const cover[] = {"看不见啦", "黑黑的"};
    static const char* const found[] = {"找到你啦"};
    static const char* const wow[] = {"哇"};
    static const char* const enter[] = {"来啦", "欢迎"};
    static const char* const leave[] = {"拜拜"};
    static const char* const near[] = {"太近啦"};
    static const char* const far[] = {"别走呀"};
    static const char* const wake[] = {"嗯？"};
    static const char* const lit[] = {"亮了"};
    static const char* const dark[] = {"好黑"};
#define M12W(list) (count = uint8_t(sizeof(list) / sizeof(list[0])), list)
    switch (e.type) {
    case M12EvType::look: return M12W(look);
    case M12EvType::nod: return M12W(nod);
    case M12EvType::shake: return M12W(shake);
    case M12EvType::tilt: return M12W(tilt);
    case M12EvType::cover: return M12W(cover);
    case M12EvType::uncover: return e.dur >= 0 && e.dur < 15.f ? M12W(found) : M12W(wow);   // peekaboo: short = found
    case M12EvType::enter: return M12W(enter);
    case M12EvType::leave: return M12W(leave);
    case M12EvType::near: return M12W(near);
    case M12EvType::far: return M12W(far);
    case M12EvType::wake: if (e.pan) break; return M12W(wake);
    case M12EvType::light: if (e.on < 0) break; return e.on ? M12W(lit) : M12W(dark);
    case M12EvType::bright: return M12W(lit);
    case M12EvType::dark: return M12W(dark);
    default: break;
    }
#undef M12W
    count = 0;
    return nullptr;
}
// 0.5.05 (owner 2026-11-18 "给qwen拒绝加一个明确的机器上的ui"): the realtime model's two refusals in 围观, shown in the
// same row: smart_turn_v2 judged a sentence not for hello (chat_stage aside, with the blue blocks), and a reply of
// hello's own was refused because someone was speaking (chat_stage busy).
constexpr const char* kQwenAside = "不是跟我说的";
constexpr const char* kQwenBusy = "等你说完";
// Every character any word uses: the glyph set of collector_words_20 (scripts/generate-words-font.sh).
constexpr const char* kGlyphs = "你好嗨在呢嗯好呀吧不要吗？怎么啦看见黑的找到哇来欢迎拜太近别走亮了是跟我说等完";

class Picker {
public:
    // -> the word to show now, or nullptr. busy: another toast is on screen (it wins; this event is dropped).
    const char* pick(const Ev& e, uint32_t now_ms, bool busy)
    {
        uint8_t count = 0;
        const char* const* list = words(e, count);
        if (!list || !count || busy) return nullptr;
        const size_t t = size_t(e.type);
        if (any_ && now_ms - last_any_ < kGapMs) return nullptr;
        if (seen_[t] && now_ms - last_[t] < cooldown_ms(e.type)) return nullptr;
        any_ = seen_[t] = true;
        last_any_ = last_[t] = now_ms;
        const char* w = list[turn_[t] % count];
        turn_[t] = uint8_t(turn_[t] + 1);
        ++shown_;
        return w;
    }
    uint32_t shown() const { return shown_; }
private:
    static constexpr size_t kTypes = size_t(M12EvType::count);
    uint32_t last_[kTypes] = {}, last_any_ = 0, shown_ = 0;
    uint8_t turn_[kTypes] = {};
    bool seen_[kTypes] = {}, any_ = false;
};

}  // namespace collector::m12words
