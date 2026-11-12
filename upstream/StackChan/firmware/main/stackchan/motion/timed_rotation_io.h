/* SPDX-License-Identifier: MIT */
#pragma once
namespace stackchan::motion {
// SCS0009 wheel mode uses zero angle limits. Preserve and restore the actual
// limits with torque OFF; do not change calibration or unlock EEPROM.
struct TimedRotationIO {
    bool saved = false;
    int minimum = 0, maximum = 0;
    // The stock driver returns 1 even for a status-error ACK. Check the status
    // immediately, before another transaction can overwrite it.
    template<class Bus> static bool good(Bus& bus) { return bus.getState() == 0 && bus.getLastError() == 0; }
    template<class Bus> static bool ack(Bus& bus, int result) { return result == 1 && good(bus); }
    template<class Bus> static bool word(Bus& bus, int id, int reg, int expected) { return bus.readWord(id, reg) == expected && good(bus); }
    template<class Bus> static bool byte(Bus& bus, int id, int reg, int expected) { return bus.readByte(id, reg) == expected && good(bus); }
    template<class Bus> static bool positionMode(Bus& bus, int id) {
        const int low = bus.readWord(id, 9); if (!good(bus)) return false;
        const int high = bus.readWord(id, 11); if (!good(bus)) return false;
        return low >= 0 && high > low && high <= 1023;
    }
    template<class Bus> bool begin(Bus& bus, int id, int velocity) {
        if (saved || velocity == 0 || velocity < -1000 || velocity > 1000) return false;
        if (!ack(bus, bus.EnableTorque(id, 0)) || !byte(bus, id, 40, 0) || !byte(bus, id, 48, 1)) return false;
        minimum = bus.readWord(id, 9); if (!good(bus)) return false;
        maximum = bus.readWord(id, 11); if (!good(bus)) return false;
        if (minimum < 0 || maximum <= minimum || maximum > 1023) return false;
        saved = true; // even a missing mode ACK must restore the original limits
        // Read back the stopped PWM and wheel mode BEFORE torque on, otherwise a
        // rejected mode write could energize the stale position goal.
        return ack(bus, bus.WritePWM(id, 0)) && word(bus, id, 44, 0) &&
            ack(bus, bus.PWMMode(id)) && word(bus, id, 9, 0) && word(bus, id, 11, 0) &&
            ack(bus, bus.EnableTorque(id, 1)) && byte(bus, id, 40, 1) &&
            ack(bus, bus.WritePWM(id, velocity * 1023 / 1000));
    }
    template<class Bus> bool end(Bus& bus, int id) {
        const bool zero = ack(bus, bus.WritePWM(id, 0));
        const bool off = ack(bus, bus.EnableTorque(id, 0));
        if (!zero || !off || !byte(bus, id, 40, 0) || !word(bus, id, 44, 0)) return false;
        if (saved) {
            const bool low = ack(bus, bus.writeWord(id, 9, minimum));
            const bool high = ack(bus, bus.writeWord(id, 11, maximum));
            if (!low || !high || !word(bus, id, 9, minimum) || !word(bus, id, 11, maximum)) return false;
            saved = false;
        }
        return true;
    }
};
}
