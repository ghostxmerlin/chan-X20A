#!/usr/bin/env python3
"""Generate a narrowly patched copy of the pinned IDF SPI driver, never edit IDF."""
import hashlib
from pathlib import Path
import sys

EXPECTED = '1981ca91462f20cb74978e794895cd4b0650990711031e03cf433509fcad90eb'
PATCHED = '2239515feaaa0e890aeb0c4c474e3f2fd318ae841001f66603a963feb1a89b1c'


def patched(source):
    if hashlib.sha256(source).hexdigest() != EXPECTED:
        raise ValueError('SPI master source changed; review the allocation-failure patch')
    original = b'''clean_up:
    uninstall_priv_desc(priv_desc);
    return ret;
}'''
    replacement = b'''clean_up:
    // Setup never submitted a transfer: release local DMA allocations without
    // RX copy-back. priv_desc is not populated until setup succeeds.
    if (send_ptr != ((trans_desc->flags & SPI_TRANS_USE_TXDATA) ?
                    (uint32_t *)trans_desc->tx_data : (uint32_t *)trans_desc->tx_buffer)) {
        free(send_ptr);
    }
    if (rcv_ptr != ((trans_desc->flags & SPI_TRANS_USE_RXDATA) ?
                   (uint32_t *)trans_desc->rx_data : (uint32_t *)trans_desc->rx_buffer)) {
        free(rcv_ptr);
    }
    return ret;
}'''
    if source.count(original) != 1:
        raise ValueError('SPI setup cleanup patch is ambiguous')
    result = source.replace(original, replacement)
    if hashlib.sha256(result).hexdigest() != PATCHED:
        raise ValueError('SPI cleanup patch output changed; review and update its hash')
    return result


if __name__ == '__main__':
    target = Path(sys.argv[2])
    result = patched(Path(sys.argv[1]).read_bytes())
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != result:
        target.write_bytes(result)
