#include "monitor_send.h"
#include <cassert>
#include <vector>
using namespace collector::monitor;
int main() {
    std::vector<uint8_t> payload(24000,'x'),wire;
    int64_t clock=0;
    auto progressing=[&](const uint8_t* p,size_t n){clock+=4000;wire.insert(wire.end(),p,p+n);return int(n);};
    // A busy CPU still advances the transfer: do not cut a valid 24 KiB reply
    // merely because its total duration exceeds 100 ms.
    assert(send_frame(1,payload.data(),payload.size(),progressing,[&]{return clock;}));
    assert(wire.size()==24016&&clock>100000);
    Header h;assert(parse_header(wire.data(),h)&&h.length==24000);
    assert(h.crc==crc32(wire.data()+16,24000));
    clock=0;
    auto stalled=[&](const uint8_t*,size_t){clock+=10000;return 0;};
    assert(!send_frame(1,payload.data(),payload.size(),stalled,[&]{return clock;}));
    assert(clock>=100000&&clock<=120000);
    clock=0;
    auto trickle=[&](const uint8_t*,size_t){clock+=10000;return 1;};
    assert(!send_frame(1,payload.data(),payload.size(),trickle,[&]{return clock;}));
    assert(clock<=1020000); // Progress must not permit an unbounded transfer.
}
