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
// M12Policy starts in standby. ASR selects detection/standby; the web may
// explicitly select recognition. Other inputs and face results never select modes.
struct M12Snapshot {
    bool valid = false;           // UART driver and task running
    M12Phase phase = M12Phase::idle;
    M12Function function = M12Function::none; // last requested function, not confirmed device state
    int64_t requested_us = 0;      // last command written
    uint32_t mode_request_revision = 0; // request actually written to UART
    uint32_t results = 0;         // Face Detector results since boot
    uint32_t result_seq = 0;      // increments on every Face Detector result
    int64_t last_result_us = 0;
    uint32_t recognitions = 0;    // Face Recognition results since boot
    int64_t last_recognition_us = 0;
    int64_t last_recognition_report_us = 0; // startup ACK, not a face or heartbeat
    uint32_t rx_bytes = 0;        // bytes received from the M12 since boot
    uint32_t frame_seq = 0;
    int64_t frame_us = 0;
    bool frame_recognition = false;
    uint16_t num = 0;             // faces the M12 reported in the last detector result
    size_t count = 0;             // faces kept (at most kM12MaxFaces)
    M12Face faces[kM12MaxFaces];
    M12Identity identity;         // most recent recognition (web also has per-face identities)
};
void m12_init();
// Compatibility no-op: ordinary interaction must not start the camera.
void m12_wake();
// Explicit request: true selects Face Detector; false selects standby. Non-blocking.
void m12_set_view(bool on);
void m12_set_mode(M12Function mode);
// Background processing leaves the eye home visible. Returns a mode ownership
// revision; explicit preview requests use a separate display revision.
uint32_t m12_set_background_mode(M12Function mode);
uint32_t m12_mode_request();
uint32_t m12_view_request();
M12Snapshot m12_snapshot();
const char* m12_phase_name(M12Phase phase);
void m12_json(ArduinoJson::JsonObject out);
void m12_info_json(ArduinoJson::JsonObject out);
}
