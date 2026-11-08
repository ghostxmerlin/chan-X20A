#!/usr/bin/env python3
"""Pin driver prerequisites; shared_storage also drains LCD DMA under the LVGL lock."""
import hashlib
from pathlib import Path
import sys

EXPECTED = {
    'components/esp_lcd/spi/esp_lcd_panel_io_spi.c': '146f69bf5be34d42bb21ed5d9c990b87ca81f16bfcab0a183896cf6a0d99a40a',
    'components/esp_driver_sdspi/src/sdspi_host.c': '04d72fe94ce57f287701e2519b78ed5e531b1e2eb59d7ce73493f3f85b9e4e1d',
}
for name, expected in EXPECTED.items():
    actual = hashlib.sha256((Path(sys.argv[1])/name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f'Shared LCD/SD contract changed in {name}; review GPIO35/DMA arbitration before building')
print('Shared SPI: verified pinned LCD DC release and SD bus ownership')
