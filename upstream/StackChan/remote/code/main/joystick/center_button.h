/* SPDX-License-Identifier: MIT */
#ifndef COLLECTOR_CENTER_BUTTON_H
#define COLLECTOR_CENTER_BUTTON_H
#include <stdbool.h>
#include <stdint.h>

// Mini JoyC 0x30 is active-low. Require release before the first press so
// booting or entering a control mode while holding the stick is not a click.
typedef struct {
    bool released;
    uint8_t candidate;
    uint8_t samples;
} center_button_filter_t;

static inline void center_button_reset(center_button_filter_t *filter)
{
    filter->released = false;
    filter->candidate = 0;
    filter->samples = 0;
}

static inline bool center_button_sample(center_button_filter_t *filter, bool valid, uint8_t value)
{
    if (!valid || value > 1) { filter->samples = 0; return false; }
    if (!filter->samples || value != filter->candidate) {
        filter->candidate = value;
        filter->samples = 1;
        return false;
    }
    if (filter->samples < 2) ++filter->samples;
    if (filter->samples < 2) return false;
    if (value == 1) { filter->released = true; return false; }
    if (!filter->released) return false;
    filter->released = false;
    return true;
}
#endif
