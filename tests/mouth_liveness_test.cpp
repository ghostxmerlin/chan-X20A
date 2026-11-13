// /mouth liveness (collector-0.4.90): the downlink is silent when idle, so after a network blip the ear
// closed it and hello stayed "connected" for an hour (2026-11-13). The ear pings every 20 s; any frame
// (pings included) proves the link; 45 s without one means it is dead.
#include "mouth_liveness.h"
#include <cassert>
#include <cstdio>
using collector::mouth::Liveness;
int main() {
    constexpr int64_t S = 1000000;
    Liveness l; l.start(100 * S);
    assert(!l.dead(100 * S + 44 * S));
    assert(l.dead(100 * S + 45 * S));                  // no frame since connect
    l.start(0);
    for (int t = 20; t <= 200; t += 20) { l.seen(t * S); assert(!l.dead(t * S + 25 * S)); }   // pings every 20 s
    assert(l.dead(200 * S + 45 * S));                  // pings stopped
    l.start(1000 * S); l.seen(1010 * S);
    assert(!l.dead(1054 * S)); assert(l.dead(1055 * S));
    assert(Liveness::kDeadUs == 45 * S);
    std::puts("mouth liveness ok");
}
