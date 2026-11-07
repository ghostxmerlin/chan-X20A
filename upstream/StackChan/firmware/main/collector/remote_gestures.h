/* SPDX-License-Identifier: MIT */
#pragma once
#include "remote_protocol.h"
#include <array>
#include <cstdint>

namespace collector {
enum class RemoteReaction : uint8_t { None, Heart, Reject };
constexpr uint32_t kReactionDurationMs = 1000;

// The factory packet contains mapped pitch, not raw stick coordinates or mode.
// Its setup UI identifies high Y (and therefore high mapped pitch) as up.
class RemoteGestureInput {
public:
    RemoteReaction accept(int pitch)
    {
        RemoteReaction reaction = RemoteReaction::None;
        if (pitch >= 600 && direction_ != 1) {
            direction_ = 1; reaction = RemoteReaction::Heart;
        } else if (pitch <= 300 && direction_ != -1) {
            direction_ = -1; reaction = RemoteReaction::Reject;
        } else if ((direction_ == 1 && pitch <= 520) || (direction_ == -1 && pitch >= 380)) {
            direction_ = 0;
        }
        return reaction;
    }
private:
    int direction_ = 0;
};

// Center packets carry a cumulative press counter, not a held-button level.
// Keep the requested display state independent of motor enable/feedback and
// independent of how often the UI consumes a snapshot.
class RemoteCenterInput {
public:
    static constexpr int64_t kReconnectGapUs = 3000000;

    bool accept(const RemoteCommand& command, const std::array<uint8_t, 6>& source, int64_t timestamp)
    {
        if (!command.center_v1 || !command.sender_boot_nonce) {
            have_session_ = false; // Legacy B input cannot become a center press.
            return false;
        }
        const bool baseline = !have_session_ || source != source_ || command.sender_boot_nonce != boot_nonce_ ||
            timestamp < packet_timestamp_ || timestamp - packet_timestamp_ >= kReconnectGapUs;
        packet_timestamp_ = timestamp;
        if (baseline) {
            have_session_ = true;
            source_ = source;
            boot_nonce_ = command.sender_boot_nonce;
            press_sequence_ = command.center_press_seq;
            return false;
        }
        const uint32_t forward = command.center_press_seq - press_sequence_;
        // Serial-number arithmetic accepts wrap but rejects duplicates and
        // backwards/ambiguous jumps. Never lower the accepted press sequence.
        if (!forward || forward >= 0x80000000u) return false;
        press_sequence_ = command.center_press_seq;
        event_sequence_ += forward;
        event_timestamp_ = timestamp;
        if (forward & 1u) display_requested_ = !display_requested_;
        return true;
    }
    bool display_requested() const { return display_requested_; }
    uint32_t event_sequence() const { return event_sequence_; }
    int64_t event_timestamp() const { return event_timestamp_; }
private:
    bool have_session_ = false, display_requested_ = true;
    std::array<uint8_t, 6> source_{};
    uint32_t boot_nonce_ = 0, press_sequence_ = 0, event_sequence_ = 0;
    int64_t packet_timestamp_ = 0, event_timestamp_ = 0;
};

}
