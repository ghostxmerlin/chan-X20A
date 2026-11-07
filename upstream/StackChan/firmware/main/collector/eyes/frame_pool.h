/* SPDX-License-Identifier: MIT */
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
namespace collector::eyes {
class FramePool {
public:
    enum class Life { Free, Decoding, Ready, Bound, Retiring };
    struct Slot { Life life=Life::Free;uint32_t generation=0;uint16_t frame=0;int64_t due_us=0; };
    void cancel(uint32_t generation) {
        current_=generation;
        for (auto& s:slots_) if (s.life==Life::Ready && s.generation!=generation) s.life=Life::Free;
    }
    int begin(uint32_t generation) {
        if (generation!=current_) return -1;
        for (size_t i=0;i<2;++i) if (slots_[i].life==Life::Free) { slots_[i]={Life::Decoding,generation,0,0};return int(i); }
        return -1;
    }
    bool publish(int i,uint32_t generation,uint16_t frame,int64_t due) {
        if (i<0 || i>=2 || slots_[i].life!=Life::Decoding || slots_[i].generation!=generation) return false;
        if (generation!=current_) { slots_[i].life=Life::Free;return false; }
        slots_[i]={Life::Ready,generation,frame,due};return true;
    }
    void abandon(int i) { if (i>=0 && i<2 && (slots_[i].life==Life::Decoding || slots_[i].life==Life::Ready)) slots_[i].life=Life::Free; }
    int ready(uint32_t generation,int64_t now) const {
        for (size_t i=0;i<2;++i) if (slots_[i].life==Life::Ready && slots_[i].generation==generation && now>=slots_[i].due_us) return int(i);
        return -1;
    }
    int take(uint32_t generation,int64_t now) {
        const int i=ready(generation,now);if (i<0) return -1;
        if (bound_>=0) slots_[bound_].life=Life::Retiring;
        slots_[i].life=Life::Bound;bound_=i;++submitted_;return i;
    }
    void rendered(bool shown) {
        for (auto& s:slots_) if (s.life==Life::Retiring) s.life=Life::Free;
        if (shown) rendered_=submitted_;
    }
    int bound() const { return bound_; }
    const Slot& slot(int i) const { return slots_[i]; }
    uint32_t submitted() const { return submitted_; }
    uint32_t rendered_sequence() const { return rendered_; }
private:
    std::array<Slot,2> slots_{};uint32_t current_=0,submitted_=0,rendered_=0;int bound_=-1;
};
}
