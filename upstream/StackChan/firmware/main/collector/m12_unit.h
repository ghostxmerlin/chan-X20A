/* SPDX-License-Identifier: MIT */
#pragma once
#include "m12_protocol.h"
#include <ArduinoJson.hpp>
#include <cstdint>

namespace collector {
// UnitV2 M12 on StackChan's top blue port (Port C): UART0 at 115200 8N1, TX GPIO17,
// RX GPIO18 (CoreS3 pin table). The log console runs on USB Serial/JTAG only, so
// UART0 is free. At boot nothing is sent: the M12 keeps whatever it was doing.
struct M12Snapshot {
    bool valid = false;           // UART driver and task running
    int requested = 0;            // last request: 1 face detector, -1 standby, 0 none yet
    uint32_t request_seq = 0;     // increments on every m12_request_faces call
    int64_t requested_us = 0;     // when the last command was written
    uint32_t results = 0;         // face results received since boot
    uint32_t result_seq = 0;      // increments on every face result
    int64_t last_result_us = 0;
    uint32_t rx_bytes = 0;        // bytes received from the M12 since boot
    uint16_t num = 0;             // faces the M12 reported in the last result
    size_t count = 0;             // faces kept (at most kM12MaxFaces)
    M12Face faces[kM12MaxFaces];
};
void m12_init();
// Non-blocking: the M12 task writes the switch command.
void m12_request_faces(bool on);
M12Snapshot m12_snapshot();
void m12_json(ArduinoJson::JsonObject out);
void m12_info_json(ArduinoJson::JsonObject out);
}
