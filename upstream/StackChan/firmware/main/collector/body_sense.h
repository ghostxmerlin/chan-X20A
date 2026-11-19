/* SPDX-License-Identifier: MIT */
#pragma once
// Body sense (collector-0.5.12, owner 2026-11-19 "姿态、加速度、距离…汇总到服务器"): what the body's own sensors feel,
// sent to the ear once a second on the vision uplink, plus a few events. Pure logic (host-tested,
// tests/body_sense_test.cpp); the sensor task feeds it, the vision uplink task takes from it, under one lock outside.
//
// Inputs: the BMI270 at 50 Hz (accel m/s^2 with gravity, gyro dps; the chip sits on the moving head, axes not
// calibrated to the body), the LTR553 proximity count (0..2047, bigger = nearer) and lux at 10 Hz, the BMM150 field
// (uT). self_moving: the robot's own servos had a goal written in the last kSelfMoveMs (the head turning shakes the
// IMU; those samples count neither as motion nor as stillness).
//
// Summary (one per kWindowUs): pitch / roll of gravity in the head frame (deg), mean |a| (g), the largest |a|-1g and
// |gyro| in the window, the share of samples that moved, still (no moving sample for kStillUs), held (see below),
// self_moving, the latest proximity / lux / field.
//
// Events (queued, at most kEvQueue; the oldest goes first when full):
//   picked_up  a run of moving samples (gaps < kGapUs) lasting kPickRunUs after at least kStillBeforeUs of stillness;
//              held = true. Somebody lifted / carried / pushed the robot.
//   put_down   held, and no moving sample for kPutDownUs; held = false. Carries how long it was held.
//   shaken     kShakeHits strong samples (|a|-1g > kShakeG or |gyro| > kShakeDps) within kShakeWinUs; kShakeCoolUs apart.
//   near / far proximity above baseline + kNearOver for kNearReads reads / back under baseline + kFarOver for kFarReads
//              reads. baseline: the slow minimum of far readings (it creeps up kBaseCreep per read, drops at once).
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace collector::body {

constexpr float kG = 9.80665f;
constexpr int64_t kWindowUs = 1000000;
constexpr uint32_t kSelfMoveMs = 800;
constexpr float kMoveG = 0.06f, kMoveDps = 20.0f;          // one sample "moved"
constexpr int64_t kGapUs = 400000, kPickRunUs = 600000, kStillBeforeUs = 2000000, kStillUs = 1500000;
constexpr int64_t kPutDownUs = 2500000;
constexpr float kShakeG = 0.7f, kShakeDps = 250.0f;
constexpr int kShakeHits = 6;
constexpr int64_t kShakeWinUs = 1000000, kShakeCoolUs = 3000000;
constexpr int kNearOver = 250, kFarOver = 120, kNearReads = 2, kFarReads = 5;
constexpr int kBaseCreep = 1;
constexpr int kEvQueue = 8;

enum class Ev : uint8_t { none, picked_up, put_down, shaken, near, far };

inline const char* ev_name(Ev e)
{
    switch (e) {
    case Ev::picked_up: return "picked_up";
    case Ev::put_down: return "put_down";
    case Ev::shaken: return "shaken";
    case Ev::near: return "near";
    case Ev::far: return "far";
    default: return "none";
    }
}

struct Event {
    Ev ev = Ev::none;
    int64_t t_us = 0;
    uint32_t seq = 0;
    int held_ms = -1;          // put_down: how long it was held
    int prox = -1;             // near / far: the reading
    float peak_g = 0, peak_dps = 0;   // shaken / picked_up: the strongest sample so far
};

struct Summary {
    uint32_t seq = 0;
    int64_t t_us = 0;
    uint16_t samples = 0;      // IMU samples in the window (self-moving ones included)
    float pitch_deg = 0, roll_deg = 0, acc_g = 0, dev_max_g = 0, gyro_max_dps = 0, moved = 0;
    bool imu = false, still = false, held = false, self_moving = false;
    int prox = -1, prox_base = -1;
    bool near = false;
    float lux = -1;
    bool mag = false;
    float mag_ut[3]{};
};

class Sense {
public:
    void imu(float ax, float ay, float az, float gx, float gy, float gz, int64_t t_us, bool self_moving)
    {
        if (!start_us_) start_us_ = t_us;
        const float a = std::sqrt(ax * ax + ay * ay + az * az);
        const float dev = std::fabs(a / kG - 1.0f);
        const float w = std::sqrt(gx * gx + gy * gy + gz * gz);
        ++n_; sx_ += ax; sy_ += ay; sz_ += az; sa_ += a / kG;
        if (self_moving) { self_ = true; return; }
        dev_max_ = std::max(dev_max_, dev); gyro_max_ = std::max(gyro_max_, w);
        const bool moved = dev > kMoveG || w > kMoveDps;
        if (!moved) {
            if (run_start_ && t_us - last_move_ > kGapUs) run_start_ = 0;      // the run ended
            if (held_ && last_move_ && t_us - last_move_ >= kPutDownUs) {
                held_ = false;
                Event e; e.ev = Ev::put_down; e.t_us = t_us; e.held_ms = static_cast<int>((t_us - held_since_) / 1000);
                push(e);
                still_since_ = last_move_;
            }
            if (!last_move_ || t_us - last_move_ > kGapUs) { if (!still_since_) still_since_ = last_move_ ? last_move_ : start_us_; }
            return;
        }
        ++moved_n_;
        peak_g_ = std::max(peak_g_, dev); peak_dps_ = std::max(peak_dps_, w);
        if (!run_start_) {
            run_start_ = t_us;
            run_still_ = still_since_ ? t_us - still_since_ : 0;   // how long it had been still before this run
        }
        last_move_ = t_us;
        still_since_ = 0;
        if (!held_ && t_us - run_start_ >= kPickRunUs && run_still_ >= kStillBeforeUs) {
            held_ = true; held_since_ = run_start_;
            Event e; e.ev = Ev::picked_up; e.t_us = t_us; e.peak_g = peak_g_; e.peak_dps = peak_dps_;
            push(e);
        }
        if (dev > kShakeG || w > kShakeDps) {
            hits_[hit_i_ % kShakeHits] = t_us; ++hit_i_;
            const int64_t oldest = hits_[hit_i_ % kShakeHits];
            if (hit_i_ >= kShakeHits && t_us - oldest <= kShakeWinUs && (!last_shake_ || t_us - last_shake_ >= kShakeCoolUs)) {
                last_shake_ = t_us;
                Event e; e.ev = Ev::shaken; e.t_us = t_us; e.peak_g = peak_g_; e.peak_dps = peak_dps_;
                push(e);
            }
        }
    }

    void proximity(int raw, int64_t t_us)
    {
        if (raw < 0) return;
        prox_ = raw;
        if (base_ < 0) base_ = raw;
        if (!near_) {
            base_ = raw < base_ ? raw : std::min(raw, base_ + kBaseCreep);
            near_n_ = raw >= base_ + kNearOver ? near_n_ + 1 : 0;
            if (near_n_ >= kNearReads) { near_ = true; near_n_ = 0; Event e; e.ev = Ev::near; e.t_us = t_us; e.prox = raw; push(e); }
        } else {
            far_n_ = raw <= base_ + kFarOver ? far_n_ + 1 : 0;
            if (far_n_ >= kFarReads) { near_ = false; far_n_ = 0; Event e; e.ev = Ev::far; e.t_us = t_us; e.prox = raw; push(e); }
        }
    }

    void light(float lux) { lux_ = lux; }
    void magnetic(const float ut[3]) { mag_ = true; std::copy(ut, ut + 3, mag_ut_); }

    // The window's summary once kWindowUs passed since the last one (false before). Resets the window.
    bool take_summary(int64_t now_us, Summary& s)
    {
        if (!win_us_) { win_us_ = now_us; return false; }
        if (now_us - win_us_ < kWindowUs) return false;
        s = Summary{};
        s.seq = ++sum_seq_; s.t_us = now_us; s.samples = static_cast<uint16_t>(std::min<uint32_t>(n_, 65535));
        s.imu = n_ > 0;
        if (n_) {
            const float x = sx_ / n_, y = sy_ / n_, z = sz_ / n_;
            s.pitch_deg = std::atan2(-x, std::sqrt(y * y + z * z)) * 57.29578f;
            s.roll_deg = std::atan2(y, z) * 57.29578f;
            s.acc_g = sa_ / n_;
        }
        s.dev_max_g = dev_max_; s.gyro_max_dps = gyro_max_; s.moved = n_ ? static_cast<float>(moved_n_) / n_ : 0;
        s.still = !self_ && (last_move_ ? now_us - last_move_ >= kStillUs : n_ > 0);
        s.held = held_; s.self_moving = self_;
        s.prox = prox_; s.prox_base = base_; s.near = near_; s.lux = lux_;
        s.mag = mag_; std::copy(mag_ut_, mag_ut_ + 3, s.mag_ut);
        win_us_ = now_us; n_ = moved_n_ = 0; sx_ = sy_ = sz_ = sa_ = 0; dev_max_ = gyro_max_ = 0; self_ = false;
        if (!held_) peak_g_ = peak_dps_ = 0;
        return true;
    }

    bool take_event(Event& e)
    {
        if (q_n_ == 0) return false;
        e = q_[q_head_]; q_head_ = (q_head_ + 1) % kEvQueue; --q_n_;
        return true;
    }
    int pending() const { return q_n_; }
    uint32_t dropped() const { return dropped_; }
    bool held() const { return held_; }

private:
    void push(Event e)
    {
        e.seq = ++ev_seq_;
        if (q_n_ == kEvQueue) { q_head_ = (q_head_ + 1) % kEvQueue; --q_n_; ++dropped_; }
        q_[(q_head_ + q_n_) % kEvQueue] = e; ++q_n_;
    }

    int64_t start_us_ = 0, win_us_ = 0, last_move_ = 0, still_since_ = 0, run_start_ = 0, run_still_ = 0;
    int64_t held_since_ = 0, last_shake_ = 0;
    int64_t hits_[kShakeHits]{};
    uint32_t hit_i_ = 0, n_ = 0, moved_n_ = 0, sum_seq_ = 0, ev_seq_ = 0, dropped_ = 0;
    float sx_ = 0, sy_ = 0, sz_ = 0, sa_ = 0, dev_max_ = 0, gyro_max_ = 0, peak_g_ = 0, peak_dps_ = 0;
    bool self_ = false, held_ = false, near_ = false, mag_ = false;
    int prox_ = -1, base_ = -1, near_n_ = 0, far_n_ = 0;
    float lux_ = -1, mag_ut_[3]{};
    Event q_[kEvQueue]{};
    int q_head_ = 0, q_n_ = 0;
};

}  // namespace collector::body

namespace collector {
// collector.cpp: the robot's one Sense (PSRAM, behind its own lock); the vision uplink takes from it.
bool body_take_summary(int64_t now_us, body::Summary& out);
bool body_take_event(body::Event& out);
}
