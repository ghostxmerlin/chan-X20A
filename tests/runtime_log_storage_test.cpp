// This translation unit measures the real object's storage, not a duplicate
// test array. The local display ring must fit its 1,089-byte internal-RAM budget.
#include "../upstream/StackChan/firmware/main/collector/runtime_log.cpp"
#include <cassert>
int main()
{
    assert(sizeof(collector::lines)<=1089);
    assert(collector::kRuntimeLogTextBytes<=1089);
}
