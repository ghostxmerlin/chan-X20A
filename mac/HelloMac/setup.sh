#!/bin/sh
# One-time setup of hello on the Mac: writes ~/Library/Application Support/HelloMac/config.json and copies the ear
# token there (mode 600; read over ssh from the server, never printed, never in the repo).
#   mac/HelloMac/setup.sh          keeps an existing device id; rewrites the rest
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
. "$ROOT/server/ear/_env.sh"
need SOUL_SERVER_SSH
DIR="$HOME/Library/Application Support/HelloMac"
mkdir -p "$DIR" && chmod 700 "$DIR"
umask 077
$SSH "$SOUL_SERVER_SSH" 'cat ~/soul-spike/token' > "$DIR/token.tmp"
[ -s "$DIR/token.tmp" ] || { echo "could not read the ear token" >&2; rm -f "$DIR/token.tmp"; exit 1; }
mv "$DIR/token.tmp" "$DIR/token"
[ -s "$DIR/deck.token" ] || python3 -c 'import secrets; print(secrets.token_hex(16))' > "$DIR/deck.token"   # the app's loopback face for monitor-mac.sh
DEV=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("deviceId",""))' "$DIR/config.json" 2>/dev/null || true)
[ -n "$DEV" ] || DEV=$(python3 -c 'import secrets; print(secrets.token_hex(6))')
python3 - "$DIR/config.json" "$SOUL_SERVER_SSH" "$DEV" "$DIR/token" <<'EOF'
import json, sys
path, target, dev, token = sys.argv[1:]
old = {}
try:
    old = json.load(open(path))
except Exception:
    pass
cfg = {"sshTarget": target, "sshOptions": ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"],
       "remotePort": 52832, "localPort": 18832, "tokenFile": token, "deviceId": dev,
       "hearingPath": "/hear", "powerOnNine": bool(old.get("powerOnNine", False))}
json.dump(cfg, open(path, "w"), indent=1)
print("config:", path, "| device", dev, "| ear 127.0.0.1:%d -> server 127.0.0.1:%d" % (cfg["localPort"], cfg["remotePort"]))
EOF
