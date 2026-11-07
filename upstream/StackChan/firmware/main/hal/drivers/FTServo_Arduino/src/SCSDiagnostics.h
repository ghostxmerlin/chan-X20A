/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

// Passive observations of existing SCSCL transactions. No polling or retries.
// Copy only on the bus-owning task; publish that copy under the caller's lock.
struct SCSDiagnostics {
    struct Counter {
        uint32_t attempts=0,ok=0,no_reply=0,bad_checksum=0,wrong_id=0,bad_length=0,servo_error=0,tx_error=0;
    };
    struct Trace {
        bool valid=false,status_valid=false,raw_valid=false;
        uint64_t timestamp_us=0,duration_us=0;
        // API submission timing, not physical UART wire timing. Span excludes
        // wait_tx_done/RX; gap is previous write return -> next write entry.
        uint64_t tx_submit_span_us=0,tx_max_gap_us=0;
        uint8_t id=0,instruction=0,address=0,requested_bytes=0;
        uint8_t transport_error=0,servo_status=0,rx_prefix_size=0,rx_prefix[24]{};
        // Command payload passed to the API, not the full UART wire packet.
        uint8_t tx_data_prefix_size=0,tx_data_prefix[6]{};
        uint32_t rx_bytes=0,tx_bytes=0,tx_calls=0,tx_short_writes=0,discarded_before=0;
        int tx_wait_error=0,raw_value=0;
    };
    struct UartEvents {
        bool enabled=false;
        // ESP-IDF's default UART mask omits frame errors. This records the
        // optional additive enable call; failure does not stop communication.
        bool frame_error_interrupt_enabled=false;
        int frame_error_interrupt_error=0;
        // IDF may discard events when its queue is full. These counters are
        // lower bounds; not observing a full queue never proves completeness.
        bool counts_complete=false;
        uint32_t queue_capacity=0,queue_full_observations=0,events_drained=0;
        uint32_t frame_errors=0,parity_errors=0,fifo_overflows=0,buffer_full=0,breaks=0;
        // Queue drain time, not the occurrence time of a hardware event.
        uint64_t last_poll_timestamp_us=0;
    };
    uint32_t generation=0;
    Counter all,position_read,moving_read,torque_read,torque_disable;
    Trace last,last_error,last_position_write,last_torque_disable;
    // Shared bus totals copied into each axis snapshot: never sum both axes.
    UartEvents uart_events;
};
