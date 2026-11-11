#!/bin/bash
# Runs ON the server (piped by deploy.sh): start or restart the transient unit soul-tts in stream mode.
# API on 127.0.0.1:52750 (mind calls it), mouth on 127.0.0.1:52751 (the ear relays the robot's /mouth to it).
# Tokens: the device (robot) uses the ear's SOUL_TOKEN_FILE (the ear relays /mouth with it); the API (mind
# calls it) uses its own ~/soul-tts/api.token, generated once here (soul-tts refuses the same token for both).
# DashScope key from ~/aidea/agent/.env.
set -euo pipefail
D="$HOME/${TTS_DIR:-soul-tts}"
UNIT="${UNIT:-soul-tts}"
EAR_UNIT="${EAR_UNIT:-soul-spike}"
PY="$HOME/soul-spike/.venv/bin/python"
TOKEN_FILE=$(systemctl show "$EAR_UNIT" -p Environment --value | tr ' ' '\n' | sed -n 's/^SOUL_TOKEN_FILE=//p')
[ -n "$TOKEN_FILE" ] && [ -s "$TOKEN_FILE" ] || { echo "cannot find the ear token file from $EAR_UNIT" >&2; exit 1; }
"$PY" -c "import websockets, opuslib_next" || { echo "websockets/opuslib_next missing in $PY" >&2; exit 1; }
API_TOKEN_FILE="$D/api.token"
if [ ! -s "$API_TOKEN_FILE" ]; then
  (umask 077; "$PY" -c "import secrets; print(secrets.token_urlsafe(32))" > "$API_TOKEN_FILE")
  echo "made $API_TOKEN_FILE"
fi
since=$(date '+%Y-%m-%d %H:%M:%S')
if [ "$(systemctl show "$UNIT" -p LoadState --value 2>/dev/null)" = "loaded" ]; then
  sudo systemctl restart "$UNIT"
else
  sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
  sudo systemd-run --unit "$UNIT" --description "hello mouth (soul-tts, stream)" \
    --uid "$(id -u)" --gid "$(id -g)" -p WorkingDirectory="$D" -p Restart=on-failure -p RestartSec=5s \
    -p MemoryMax=512M -p CPUQuota=50% \
    --setenv=SOUL_TTS_PLAYER=stream --setenv=SOUL_TTS_TOKEN_FILE="$API_TOKEN_FILE" \
    --setenv=SOUL_TTS_DEVICE_TOKEN_FILE="$TOKEN_FILE" --setenv=SOUL_TTS_ENV_FILE="$HOME/aidea/agent/.env" \
    --setenv=SOUL_TTS_LISTEN=127.0.0.1:52750 --setenv=SOUL_TTS_MOUTH_LISTEN=127.0.0.1:52751 \
    "$PY" -m soul_tts serve
fi
for i in $(seq 20); do
  if sudo journalctl -u "$UNIT" --since "$since" --no-pager | grep -q 'listening on'; then break; fi
  sleep 2
done
echo "unit: $(systemctl is-active "$UNIT")"
sudo journalctl -u "$UNIT" --since "$since" --no-pager -o cat | tail -n 4
