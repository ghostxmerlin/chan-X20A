/* SPDX-License-Identifier: MIT */
#pragma once
#include "m12_protocol.h"
#include <ArduinoJson.hpp>
#include <cstdint>

namespace collector {
// UnitV2 M12 on StackChan's top blue port (Port C): UART0 at 115200 8N1, TX GPIO17,
// RX GPIO18 (CoreS3 pin table). The log console runs on USB Serial/JTAG only, so
// UART0 is free. The task and its UART interrupt live on core 1.
//
// What the M12 runs follows M12Policy: after boot it looks around with face
// detection, identifies a new face with a short recognition burst, keeps detecting
// while someone is there, and dozes in standby (camera off) when idle.
struct M12Snapshot {
    bool valid = false;           // UART driver and task running
    M12Phase phase = M12Phase::idle;
    M12Function function = M12Function::none; // what the M12 was last told (or found) to run
    int64_t requested_us = 0;     // last command written, or the moment a running detector was reused
    uint32_t results = 0;         // Face Detector results since boot
    uint32_t result_seq = 0;      // increments on every Face Detector result
    int64_t last_result_us = 0;
    uint32_t recognitions = 0;    // Face Recognition results since boot
    int64_t last_recognition_us = 0;
    uint32_t rx_bytes = 0;        // bytes received from the M12 since boot
    uint16_t num = 0;             // faces the M12 reported in the last detector result
    size_t count = 0;             // faces kept (at most kM12MaxFaces)
    M12Face faces[kM12MaxFaces];
    M12Identity identity;         // last recognition burst
};
void m12_init();
// Someone is interacting (voice command, touch, remote): wake up from standby.
void m12_wake();
// The face page is shown (never doze) or closed. Non-blocking.
void m12_set_view(bool on);
M12Snapshot m12_snapshot();
const char* m12_phase_name(M12Phase phase);
void m12_json(ArduinoJson::JsonObject out);
void m12_info_json(ArduinoJson::JsonObject out);
}
