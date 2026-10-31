/* SPDX-License-Identifier: MIT */
#include "collector/collector.h"

extern "C" void app_main(void)
{
    // Dedicated observation firmware: no factory apps, Xiaozhi, OTA or motion.
    collector::run();
}
