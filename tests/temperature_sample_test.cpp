#include "../upstream/StackChan/firmware/main/collector/temperature_sample.h"
#include <cassert>
#include <limits>
int main() {
    collector::TemperatureSample sample;
    assert(!sample.fresh(100));
    sample.update(52.5f, 0, 1000000);
    assert(sample.fresh(2000000) && sample.celsius == 52.5f);
    assert(!sample.fresh(4000000));
    assert(!sample.fresh(999999));
    sample.update(0, -1, 2000000);
    assert(!sample.valid && !sample.fresh(2000001));
    sample.update(std::numeric_limits<float>::quiet_NaN(), 0, 3000000);
    assert(!sample.valid);
    sample.update(101, 0, 4000000); assert(!sample.valid);
    sample.update(19, 0, 5000000); assert(!sample.valid);
    sample.update(100, 0, 6000000); assert(sample.valid);
    sample.update(20, 0, 7000000); assert(sample.valid);
}
