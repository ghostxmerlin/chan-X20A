/* SPDX-License-Identifier: MIT */
#pragma once
#include "m12_protocol.h"
#include <ArduinoJson.hpp>
#include <cstdint>

namespace collector {
// UnitV2 M12 running m12d on StackChan's top blue port (Port C): UART0 at 115200 8N1,
// TX GPIO17, RX GPIO18 (CoreS3 pin table). The log console runs on USB Serial/JTAG only,
// so UART0 is free. One task (PSRAM stack, core 1) reads lines, matches replies, expires
// requests and keeps det (standby/detect) in line with what is requested. It powers the
// M12 from the Grove port only: never plug the M12's USB-C at the same time.
struct M12Snapshot {
    bool valid = false;                 // UART driver and task running
    bool online = false;                // a heartbeat within kM12HeartbeatTimeoutUs
    int64_t last_hb_us = 0;
    M12Heartbeat hb;
    // Vision mode. `function` is what m12d confirmed in its last cfg reply (none = not yet).
    M12Function function = M12Function::none;
    int64_t requested_us = 0;           // last cfg det written
    uint32_t mode_request_revision = 0; // request revision m12d has confirmed
    uint32_t results = 0, result_seq = 0;   // detection lines since boot
    int64_t last_result_us = 0;
    uint32_t rx_bytes = 0;
    // Last detection line.
    uint32_t frame_seq = 0, frame_f = 0;
    int64_t frame_us = 0;
    uint16_t num = 0;
    size_t count = 0;
    M12Face faces[kM12MaxFaces];
    size_t persons = 0;
    M12Person person[kM12MaxPersons];
};
void m12_init();
void m12_wake();                         // compatibility no-op: ordinary interaction never starts vision
// Explicit view request: true = detect (face view on screen), false = standby. Non-blocking.
void m12_set_view(bool on);
void m12_set_mode(M12Function mode);
// Background vision (tracking / recognition) without the face view page. Returns the request
// revision it owns; m12_mode_request() != that revision means someone else took over.
uint32_t m12_set_background_mode(M12Function mode);
uint32_t m12_mode_request();
uint32_t m12_view_request();
M12Snapshot m12_snapshot();

// Request to m12d. The callback (optional) runs in the M12 task when the reply arrives or
// the client-side timeout passes (learn/who 4 s, others 1 s); err is nullptr on success,
// else the m12d code or "client_timeout". Returns the request id, or a negative error:
// -1 serial unavailable, -2 M12 offline, -3 too many requests waiting, -4 bad arguments.
using M12Callback = void (*)(const M12Reply& reply, const char* err, void* context);
int32_t m12_request(M12Op op, const M12RequestArgs& args, M12Callback callback = nullptr, void* context = nullptr);
const char* m12_request_error(int32_t code);
// Vision change events (face/person found, named, attrs, unknown, lost; vision on/off; M12
// online/offline), oldest first, each with a sequence number. The vision uplink peeks, sends,
// then commits what was sent; meanwhile the newest 64 wait here (older ones dropped, counted).
size_t m12_peek_vision_events(M12VisionEvent* out, uint32_t* seq, size_t max);
void m12_commit_vision_events(uint32_t through_seq);
size_t m12_vision_pending();

void m12_json(ArduinoJson::JsonObject out);
void m12_info_json(ArduinoJson::JsonObject out);
}
