#include "../upstream/StackChan/firmware/main/collector/remote_modes.h"
/* SPDX-License-Identifier: MIT */
#include "../upstream/StackChan/firmware/main/collector/remote_gestures.h"
#include "../upstream/StackChan/firmware/main/collector/remote_stop.h"
#include "../upstream/StackChan/firmware/main/collector/remote_actuation.h"
#include <cassert>
#include <cstring>
#include <iostream>

using collector::RemoteGestureInput;
using collector::RemoteReaction;

struct StopTestAxis {
    unsigned cancellations = 0;
    void cancelMotion() { ++cancellations; }
};
struct StopTestMotion {
    StopTestAxis yaw, pitch;
    StopTestAxis& yawServo() { return yaw; }
    StopTestAxis& pitchServo() { return pitch; }
};

static void center_display_events()
{
    collector::RemoteCenterInput input;
    std::array<uint8_t, 6> source{1, 2, 3, 4, 5, 6};
    collector::RemoteCommand packet{1, 123, 450, 600, false};
    int64_t now = 0;
    auto receive = [&](uint32_t seq, int64_t elapsed = 20000) {
        packet.center_press_seq = seq;
        now += elapsed;
        return input.accept(packet, source, now);
    };
    auto expect = [&](uint32_t count, bool requested) {
        assert(input.event_sequence() == count && input.display_requested() == requested);
    };
    expect(0, true);
    assert(input.event_timestamp() == 0);
    // Legacy B toggles are not center events, including at first startup.
    assert(!receive(0)); packet.auxiliary = true; assert(!receive(1)); expect(0, true);
    packet.center_v1 = true; packet.sender_boot_nonce = 42;
    assert(!receive(7)); expect(0, true); // Existing sender counter is baseline.
    packet.auxiliary = false; assert(!receive(7)); expect(0, true);
    assert(receive(8)); expect(1, false);
    const auto first_event = input.event_timestamp();
    for (unsigned held = 0; held < 100; ++held) assert(!receive(8));
    expect(1, false); assert(input.event_timestamp() == first_event);
    assert(receive(9)); expect(2, true);
    // Lost packets/UI polling cannot lose two presses' parity.
    assert(receive(11)); expect(4, true);
    assert(receive(14)); expect(7, false);
    const auto latest_event = input.event_timestamp();
    assert(!receive(13)); assert(!receive(14));
    assert(!receive(14u + 0x80000000u));
    expect(7, false); assert(input.event_timestamp() == latest_event);
    assert(receive(15)); expect(8, true); // Reordered packet did not lower baseline.

    packet.sender_boot_nonce = 43;
    assert(!receive(23)); expect(8, true);
    assert(receive(24)); expect(9, false);
    ++source[5];
    assert(!receive(100)); expect(9, false); // New MAC preserves requested off.
    assert(receive(101)); expect(10, true);
    packet.center_v1 = false; packet.auxiliary = true;
    assert(!receive(102)); expect(10, true);
    packet.center_v1 = true;
    assert(!receive(102)); expect(10, true); // Legacy interruption requires baseline.
    assert(receive(103)); expect(11, false);

    assert(!receive(200, collector::RemoteCenterInput::kReconnectGapUs)); expect(11, false);
    assert(receive(201)); expect(12, true);
    assert(receive(202, collector::RemoteCenterInput::kReconnectGapUs - 1)); expect(13, false);
    assert(!receive(300, collector::RemoteCenterInput::kReconnectGapUs)); expect(13, false);
    assert(receive(301)); expect(14, true);
    // Monotonic clock discontinuity is conservative and cannot replay input.
    assert(!receive(0, -1000)); expect(14, true);
    assert(receive(1)); expect(15, false);
    packet.sender_boot_nonce = 0;
    assert(!receive(2)); expect(15, false);
    packet.sender_boot_nonce = 43;
    assert(!receive(2)); expect(15, false);
    assert(receive(3)); expect(16, true);

    collector::RemoteCenterInput wrap;
    packet.center_press_seq = 0xfffffffeu;
    assert(!wrap.accept(packet, source, 0));
    packet.center_press_seq = 0xffffffffu;
    assert(wrap.accept(packet, source, 1));
    assert(wrap.event_sequence() == 1 && !wrap.display_requested());
    packet.center_press_seq = 0;
    assert(wrap.accept(packet, source, 2));
    assert(wrap.event_sequence() == 2 && wrap.display_requested());
    packet.center_press_seq = 0xffffffffu;
    assert(!wrap.accept(packet, source, 3));
    packet.center_press_seq = 2;
    assert(wrap.accept(packet, source, 4));
    assert(wrap.event_sequence() == 4 && wrap.display_requested());

    collector::RemoteCenterInput count_wrap;
    packet.center_press_seq = 0;
    assert(!count_wrap.accept(packet, source, 0));
    packet.center_press_seq = 0x7fffffffu;
    assert(count_wrap.accept(packet, source, 1));
    packet.center_press_seq = 0xfffffffeu;
    assert(count_wrap.accept(packet, source, 2));
    packet.center_press_seq = 0;
    assert(count_wrap.accept(packet, source, 3));
    assert(count_wrap.event_sequence() == 0 && count_wrap.display_requested());
}

int main()
{
    { // Live sender center is 471 (1960 ADC mapped from 310..3460), not 450.
        collector::RemoteCommand c{1,0,471,600,false}; c.center_v1=true;
        assert(collector::remote_pitch_target(c,100).pitch == 100);
        c.pitch=900; assert(collector::remote_pitch_target(c,100).pitch == 850);
        c.pitch=0; assert(collector::remote_pitch_target(c,100).pitch == 50);
        c.pitch=471; assert(collector::remote_pitch_target(c,300).pitch == 300);
        for(int neutral : {50,100,300,850}) {
            int previous=0;
            for(int raw=0;raw<=900;++raw) {
                c.pitch=raw;const int target=collector::remote_pitch_target(c,neutral).pitch;
                assert(target>=50 && target<=850 && target>=previous);previous=target;
                if(raw>=466 && raw<=476) assert(target==neutral);
            }
        }
    }
    { // Loaded head: all physical vertical inputs are blocked, including legacy packets.
        collector::RemotePitchControl pitch;
        for (bool center : {true, false}) for (int raw = -32768; raw <= 32767; ++raw) {
            collector::RemoteCommand c{1,123,static_cast<int16_t>(raw),600,false}; c.center_v1 = center;
            assert(!pitch.accept(c, true, 100000 + raw, 0));
            assert(!pitch.active());
            assert(collector::remote_yaw_target(c).yaw == -123);
        }
        assert(!collector::kRemotePitchControlEnabled);
    }
    center_display_events();
    RemoteGestureInput input;
    assert(input.accept(450) == RemoteReaction::None);
    assert(input.accept(650) == RemoteReaction::Heart);
    for (int i = 0; i < 100; ++i) {
        assert(input.accept(590 + i % 20) == RemoteReaction::None);
    }
    assert(input.accept(520) == RemoteReaction::None);
    assert(input.accept(600) == RemoteReaction::Heart);
    assert(input.accept(300) == RemoteReaction::Reject);
    assert(input.accept(300) == RemoteReaction::None);
    assert(input.accept(380) == RemoteReaction::None);
    assert(input.accept(-32768) == RemoteReaction::Reject);
    assert(input.accept(32767) == RemoteReaction::Heart);

    // HTTP stop and re-enable can both finish between the task's 20 ms
    // samples. Requested is already true again, but startup and both actuators
    // must still receive the intervening stop exactly once.
    collector::RemoteStopLatch stop_latch;
    collector::StartupPose startup;
    StopTestMotion motion;
    bool requested = true;
    assert(startup.step(0, requested, true, true) == collector::StartupPose::Action::Start);
    // No motor-task sample is taken between these control requests.
    requested = false;
    stop_latch.request();
    requested = true;
    assert(requested && stop_latch.consume());
    collector::apply_remote_stop(startup, motion, 20000);
    assert(motion.yaw.cancellations == 1 && motion.pitch.cancellations == 1);
    assert(std::strcmp(startup.state(), "cancelled") == 0);
    assert(startup.step(20000, requested, true, true) == collector::StartupPose::Action::None);
    assert(!stop_latch.consume());
    assert(startup.step(40000, requested, true, true) == collector::StartupPose::Action::None);

    // Mode-event decoding preserves center data and never restarts startup.
    collector::RemoteModeInput modes;
    std::array<uint8_t,6> mode_source{1,2,3,4,5,6};
    collector::RemoteCommand command{1, 123, 450, 600, false};
    command.center_v1 = true;
    for (uint32_t boot : {1u, 2u, 0xffffffffu}) {
        for (uint32_t sequence : {0u, 1u, 1u, 0u, 2u, 0xffffffffu}) {
            command.sender_boot_nonce = boot;
            command.center_press_seq = sequence;
            modes.accept(command, mode_source, 60000);
            assert(command.sender_boot_nonce == boot && command.center_press_seq == sequence);
            assert(startup.step(60000, requested, true, true) == collector::StartupPose::Action::None);
        }
    }
    std::cout << "PASS: direction hysteresis/held input, inter-sample stop/re-enable cancels startup and both axes, "
                 "center display baseline/delta/wrap/reconnect, independent yaw/pitch arbitration and no startup restart\n";
}
