/* SPDX-License-Identifier: MIT */
#pragma once
#include "remote_protocol.h"
#include <array>
#include <cstdlib>

namespace collector {
enum class RemoteModeDirection : uint8_t { None, Left, Right, Up, Down };
// Raw mapped stick coordinates, not motor targets. The wire format has no page
// identifier: use the remote's joystick page, not its IMU page.
class RemoteModeInput {
public:
    RemoteModeDirection accept(const RemoteCommand& input, const std::array<uint8_t,6>& source, int64_t now)
    {
        const bool neutral_x = std::abs(static_cast<int>(input.yaw)) <= 120;
        const bool neutral_y = input.pitch >= 391 && input.pitch <= 551;
        const bool neutral = neutral_x && neutral_y;
        const bool baseline = !seen_ || source != source_ || input.center_v1 != center_v1_ ||
            input.sender_boot_nonce != nonce_ || now < last_us_ || now-last_us_ >= 3000000;
        seen_=true;source_=source;center_v1_=input.center_v1;nonce_=input.sender_boot_nonce;last_us_=now;
        if (baseline) { armed_=neutral; return RemoteModeDirection::None; }
        if (neutral) { armed_=true; return RemoteModeDirection::None; }
        if (!armed_) return RemoteModeDirection::None;
        // A diagonal consumes the edge; releasing just one axis cannot turn it
        // into a press. A gradual straight deflection may reach its threshold.
        if (!neutral_x && !neutral_y) { armed_=false; return RemoteModeDirection::None; }
        if (neutral_y && input.yaw >= 300) { armed_=false; ++left_; return RemoteModeDirection::Left; }
        if (neutral_y && input.yaw <= -300) { armed_=false; ++right_; return RemoteModeDirection::Right; }
        if (neutral_x && input.pitch >= 600) { armed_=false; ++up_; return RemoteModeDirection::Up; }
        if (neutral_x && input.pitch <= 300) { armed_=false; ++down_; return RemoteModeDirection::Down; }
        return RemoteModeDirection::None;
    }
    uint32_t left_sequence() const { return left_; }
    uint32_t right_sequence() const { return right_; }
    uint32_t up_sequence() const { return up_; }
    uint32_t down_sequence() const { return down_; }
private:
    bool seen_=false,armed_=false,center_v1_=false;
    std::array<uint8_t,6> source_{};
    uint32_t nonce_=0,left_=0,right_=0,up_=0,down_=0;
    int64_t last_us_=0;
};
}
