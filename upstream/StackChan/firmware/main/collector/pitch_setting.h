/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace collector {
constexpr int kPitchSettingDefault = 130;
constexpr int kPitchSettingMinimum = 50;
constexpr int kPitchSettingMaximum = 850;
// Inward recovery for boot pose and web pitch: start from feedback up to 10°
// below the lower limit (the command still starts at the limit). 2° refused a
// head resting at -3.4° (2026-11-09), so pitch requests silently did nothing.
constexpr int kPitchSettingLowerMargin = 100;
inline bool valid_pitch_setting(int angle)
{
    return angle >= kPitchSettingMinimum && angle <= kPitchSettingMaximum;
}

struct PitchSettingRequest {
    uint32_t sequence = 0, stop_epoch = 0;
    int target = kPitchSettingDefault;
    int64_t timestamp = 0;
    bool from_boot = true;
};

// Access under the receiver mutex. A stop has its own generation, so a brief
// stop/re-enable cancels earlier requests but cannot erase a later explicit one.
class PitchSettingRequests {
public:
    void configure_boot(int target)
    {
        request_.target = valid_pitch_setting(target) ? target : kPitchSettingDefault;
    }
    uint32_t submit_committed(int target, int64_t timestamp)
    {
        if (!valid_pitch_setting(target)) return request_.sequence;
        ++request_.sequence;
        request_.target = target;
        request_.timestamp = timestamp;
        request_.stop_epoch = stop_epoch_;
        request_.from_boot = false;
        return request_.sequence;
    }
    void stop() { ++stop_epoch_; }
    bool cancelled(const PitchSettingRequest& request) const { return request.stop_epoch != stop_epoch_; }
    const PitchSettingRequest& current() const { return request_; }
private:
    PitchSettingRequest request_{};
    uint32_t stop_epoch_ = 0;
};

// Both real NVS and fault-injection tests use this transaction ordering. A
// failed set never commits; callers publish/queue only after a zero return.
template<class Storage>
int persist_pitch_setting(Storage& storage, int target)
{
    const int error = storage.set(target);
    return error ? error : storage.commit();
}
}
