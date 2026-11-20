/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
// Head pats (owner 2026-11-20, collector-0.5.21: with the hat off the Si12T head touch is reachable). Pure: the
// sensor task feeds feed(now, touched) every sample (40 ms) and acts on what comes back; tests/head_pat_test.cpp.
//   a pat      touched, then released within kPatMaxUs (a longer touch is a hold: no pat)
//   a burst    pats less than kBurstGapUs apart; when it ends: 1 pat = Tap (= a screen tap), 2-3 = DoubleTap
//   protest    the kProtestPats-th pat within kProtestWinUs of the burst's first: Protest at once (said like being
//              shaken), the rest of that burst ignored
namespace collector::head {
constexpr int64_t kPatMaxUs = 700000, kBurstGapUs = 450000, kProtestWinUs = 3000000;
constexpr int kProtestPats = 4;

enum class Gesture : uint8_t { None, Tap, DoubleTap, Protest };
inline const char* gesture_name(Gesture g)
{
    switch (g) {
    case Gesture::Tap: return "tap";
    case Gesture::DoubleTap: return "double_tap";
    case Gesture::Protest: return "protest";
    default: return "none";
    }
}

class Detector {
public:
    Gesture feed(int64_t now_us, bool touched)
    {
        if (!enabled_) { down_ = false; pats_ = 0; return Gesture::None; }
        Gesture out = Gesture::None;
        if (touched && !down_) { down_ = true; down_us_ = now_us; }
        else if (!touched && down_) {
            down_ = false;
            if (now_us - down_us_ <= kPatMaxUs) {                    // a pat (a hold is not)
                if (pats_ == 0) first_us_ = now_us;
                ++pats_;
                last_us_ = now_us;
                if (!protested_ && pats_ >= kProtestPats && now_us - first_us_ <= kProtestWinUs) {
                    protested_ = true;
                    out = Gesture::Protest;
                }
            }
        }
        if (!down_ && pats_ && now_us - last_us_ >= kBurstGapUs) {   // the burst is over
            if (!protested_) out = pats_ == 1 ? Gesture::Tap : Gesture::DoubleTap;
            pats_ = 0; protested_ = false;
        }
        return out;
    }
    void set_enabled(bool on) { enabled_ = on; if (!on) { down_ = false; pats_ = 0; protested_ = false; } }
    bool enabled() const { return enabled_; }

private:
    bool enabled_ = true, down_ = false, protested_ = false;
    int pats_ = 0;
    int64_t down_us_ = 0, first_us_ = 0, last_us_ = 0;
};
}
