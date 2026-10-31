#!/usr/bin/env bash
# Run the pinned ESP-IDF toolchain against the official StackChan firmware.
set -eo pipefail

STACKCHAN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IDF_PATH="$STACKCHAN_ROOT/.tools/esp-idf-v5.5.4"
export IDF_TOOLS_PATH="$STACKCHAN_ROOT/.tools/espressif"

if [[ ! -f "$IDF_PATH/export.sh" ]]; then
    echo "Missing ESP-IDF v5.5.4; see docs/development-baseline.md." >&2
    exit 1
fi

source "$IDF_PATH/export.sh" >/dev/null
exec idf.py -C "$STACKCHAN_ROOT/upstream/StackChan/firmware" "$@"
