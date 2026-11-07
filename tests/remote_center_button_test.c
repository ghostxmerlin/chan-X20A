/* SPDX-License-Identifier: MIT */
#include "../upstream/StackChan/remote/code/main/joystick/center_button.h"
#include "../upstream/StackChan/remote/code/main/joystick/mapped_target.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    center_button_filter_t button = {0};
    // Boot while held cannot count as a click.
    for (int i = 0; i < 10; ++i) assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 1));
    // Bounce and failed reads cannot synthesize a click.
    assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, false, 0));
    assert(!center_button_sample(&button, true, 0));
    assert(center_button_sample(&button, true, 0));
    for (int i = 0; i < 100; ++i) assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, true, 255));
    assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 0));
    assert(center_button_sample(&button, true, 0));
    // Previously observed release must not qualify a press begun in setup and
    // held while returning to either running mode. The mode setter uses this
    // reset under the same mutex as the I2C sample/filter update.
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 1));
    center_button_reset(&button);
    for (int i = 0; i < 10; ++i) assert(!center_button_sample(&button, true, 0));
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 1));
    assert(!center_button_sample(&button, true, 0));
    assert(center_button_sample(&button, true, 0));

    for (int threshold = 5; threshold <= 10; threshold += 5) {
        mapped_target_filter_t target = {0};
        assert(mapped_target_accept(&target, 100, 450, threshold));
        // Repeated heartbeat/button frames must carry the accepted pair even
        // when raw samples move throughout the factory deadband.
        for (int i = 0; i < 100; ++i) {
            const int noise = i % (2 * threshold - 1) - threshold + 1;
            assert(!mapped_target_accept(&target, 100 + noise, 450 - noise, threshold));
            assert(target.yaw == 100 && target.pitch == 450);
        }
        assert(mapped_target_accept(&target, 100 + threshold, 451, threshold));
        assert(target.yaw == 100 + threshold && target.pitch == 451);
        assert(mapped_target_accept(&target, 101 + threshold, 451 - threshold, threshold));
        assert(target.yaw == 101 + threshold && target.pitch == 451 - threshold);
        target.initialized = false; // fresh mode generation accepts its baseline
        assert(mapped_target_accept(&target, 102 + threshold, 452 - threshold, threshold));
        assert(target.yaw == 102 + threshold && target.pitch == 452 - threshold);
        assert(mapped_target_accept(&target, INT16_MIN, INT16_MAX, threshold));
        assert(mapped_target_accept(&target, INT16_MAX, INT16_MIN, threshold));
    }
    puts("PASS: center boot/mode-held suppression, release/repress, noise/read failures; heartbeat angle deadband, joint threshold and mode baseline");
    return 0;
}
