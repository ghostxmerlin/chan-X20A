#include "../upstream/StackChan/firmware/main/hal/drivers/FTServo_Arduino/src/SCS.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <deque>
#include <iostream>
#include <vector>

class FakeBus : public SCS {
public:
    FakeBus() : SCS(1) {}
    std::vector<std::vector<unsigned char>> writes;
    std::deque<unsigned char> reply;
    void respond(std::initializer_list<unsigned char> bytes) { reply = bytes; writes.clear(); }
    std::vector<unsigned char> wire() const {
        std::vector<unsigned char> result;
        for (const auto& chunk : writes) result.insert(result.end(), chunk.begin(), chunk.end());
        return result;
    }
protected:
    int writeSCS(unsigned char* bytes, int count) override {
        writes.emplace_back(bytes, bytes + count); return count;
    }
    int writeSCS(unsigned char byte) override { return writeSCS(&byte, 1); }
    int readSCS(unsigned char* bytes, int count) override {
        int copied = 0;
        while (copied < count && !reply.empty()) {
            if (bytes) bytes[copied] = reply.front();
            reply.pop_front(); ++copied;
        }
        return copied;
    }
    int readSCS(unsigned char* bytes, int count, unsigned long) override { return readSCS(bytes, count); }
    void rFlushSCS() override {}
    void wFlushSCS() override {}
};

int main() {
    FakeBus bus;
    // Real protocol response for yaw position 461 (0x01cd), including checksum.
    bus.respond({0xff, 0xff, 1, 4, 0, 1, 0xcd, 0x2c});
    assert(bus.readWord(1, 56) == 461);
    assert(bus.wire() == (std::vector<unsigned char>{0xff, 0xff, 1, 4, 2, 56, 2, 0xbe}));
    // Verify the official transport's bytes without imposing a different writer.
    bus.respond({0xff, 0xff, 1, 2, 0, 0xfc});
    assert(bus.writeByte(1, 40, 0) == 1);
    assert(bus.wire() == (std::vector<unsigned char>{0xff, 0xff, 1, 4, 3, 40, 0, 0xcf}));
    // The observed corrupted position must never become valid feedback.
    bus.respond({0xff, 0xff, 1, 4, 0, 1, 0xfd, 0x2c});
    assert(bus.readWord(1, 56) == -1 && bus.getLastError() == ERR_CRC_CMP);
    bus.respond({0xff, 0xff, 2, 4, 0, 1, 0xcd, 0x2b});
    assert(bus.readWord(1, 56) == -1 && bus.getLastError() == ERR_SLAVE_ID);
    bus.respond({0xff, 0xff, 1, 4, 0, 1});
    assert(bus.readWord(1, 56) == -1 && bus.getLastError() == ERR_NO_REPLY);
    bus.respond({0xff, 0xff, 1, 4, 32, 1, 0xcd, 0x0c});
    assert(bus.readWord(1, 56) == 461 && bus.getState() == 32 && bus.getLastError() == 0);
    std::cout << "PASS: official wire bytes, real position response, checksum/ID/truncation rejection and servo status\n";
}
