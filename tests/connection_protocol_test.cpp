/* SPDX-License-Identifier: MIT */
#include "../upstream/StackChan/firmware/main/collector/connection_protocol.h"
#include <cassert>
#include <limits>

int main()
{
    using namespace collector;
    const std::string nonce = "0123456789abcdef0123456789abcdef";
    assert(discovery_nonce_valid(nonce));
    assert(!discovery_nonce_valid(nonce + "0"));
    assert(!discovery_nonce_valid(nonce.substr(1)));
    assert(!discovery_nonce_valid("0123456789ABCDEF0123456789ABCDEF"));
    assert(!discovery_nonce_valid(std::string(32, '\0')));
    assert(discovery_proof_message("test-device", nonce) == "chan-discovery-v1\ntest-device\n" + nonce);
    int angle = -1;
    assert(pitch_request_tenths(5, angle) && angle == 50);
    assert(pitch_request_tenths(85, angle) && angle == 850);
    assert(pitch_request_tenths(13.1, angle) && angle == 131);
    for (double value : {4.9, 85.1, 12.34, -1.0, 1e100,
                        std::numeric_limits<double>::infinity(), std::numeric_limits<double>::quiet_NaN()}) {
        angle = -1;
        assert(!pitch_request_tenths(value, angle) && angle == -1);
    }
}
