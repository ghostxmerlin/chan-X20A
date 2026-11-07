/* SPDX-License-Identifier: MIT */
#ifndef COLLECTOR_MAPPED_TARGET_H
#define COLLECTOR_MAPPED_TARGET_H
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    int16_t yaw, pitch;
    bool initialized;
} mapped_target_filter_t;

// Preserve the factory's joint threshold: crossing either axis accepts both.
// Heartbeats and button changes reuse this accepted pair, not subthreshold
// sensor noise that would keep restarting the robot's spring trajectory.
static inline bool mapped_target_accept(mapped_target_filter_t *filter, int16_t yaw,
                                        int16_t pitch, int threshold)
{
    const int32_t dyaw = (int32_t)yaw - filter->yaw;
    const int32_t dpitch = (int32_t)pitch - filter->pitch;
    if (filter->initialized && dyaw > -threshold && dyaw < threshold &&
        dpitch > -threshold && dpitch < threshold) return false;
    filter->yaw = yaw;
    filter->pitch = pitch;
    filter->initialized = true;
    return true;
}
#endif
