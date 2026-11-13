/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace collector::mouth {
// /mouth liveness (collector-0.4.90). The downlink carries nothing while hello is quiet, so a dead TCP path
// was never noticed: after a network blip on 2026-11-13 the ear closed the link and hello stayed
// "connected" for an hour. The ear's WebSocket server pings every 20 s and the socket answers pings
// itself; any frame read (pings included) proves the link. 45 s without one = two missed pings: reconnect.
struct Liveness {
    static constexpr int64_t kDeadUs = 45LL * 1000000;
    int64_t last_us = 0;
    void start(int64_t now_us) { last_us = now_us; }
    void seen(int64_t now_us) { last_us = now_us; }
    bool dead(int64_t now_us) const { return now_us - last_us >= kDeadUs; }
};
}
