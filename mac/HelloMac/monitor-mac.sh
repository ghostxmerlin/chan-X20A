#!/bin/sh
# A second observation deck for hello on the Mac: http://127.0.0.1:8788/ shows the Mac stack's mind (soul-mind-mac,
# server loopback 52840, through its own ssh -L): switches, run log, 漏答漏斗, Docking flow, day board. The robot's deck
# (8787) is untouched. No robot pane here (the Mac app shows its own status). Ctrl-C stops both.
#   mac/HelloMac/monitor-mac.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
. "$ROOT/server/ear/_env.sh"
need SOUL_SERVER_SSH
TOKEN_FILE="$HOME/Library/Application Support/HelloMac/token"
[ -s "$TOKEN_FILE" ] || { echo "run mac/HelloMac/setup.sh first" >&2; exit 1; }
LOCAL=18841
$SSH -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -L "127.0.0.1:$LOCAL:127.0.0.1:52840" "$SOUL_SERVER_SSH" &
TUN=$!
trap 'kill $TUN 2>/dev/null || true' EXIT INT TERM
for i in $(seq 40); do nc -z 127.0.0.1 $LOCAL 2>/dev/null && break; sleep 0.25; done
SOUL_MIND_LOCAL="http://127.0.0.1:$LOCAL" SOUL_MIND_LOCAL_TOKEN="$(cat "$TOKEN_FILE")" SOUL_MONITOR_PORT=8788 \
  node "$ROOT/web/monitor/server.mjs"
