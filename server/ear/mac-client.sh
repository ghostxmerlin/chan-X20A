#!/bin/sh
# Run the Mac test client with the settings from the repo-root .env. Arguments pass through:
#   server/ear/mac-client.sh                  live Mac mic, opens the debug page
#   server/ear/mac-client.sh --enroll 主人     enroll a voice
#   server/ear/mac-client.sh --file a.wav     stream a 16 kHz wav
set -eu
. "$(dirname "$0")/_env.sh"
exec "${SOUL_MAC_PYTHON:-python3}" "$HERE/mic_client.py" "$@"
