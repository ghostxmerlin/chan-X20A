#!/bin/bash
# Runs ON the server (piped over ssh by deploy.sh). Creates ~/soul-mind/settings.json from the example
# if it does not exist yet, then starts or restarts the transient unit soul-mind (like the ear's
# soul-spike). Writes nothing outside ~/soul-mind; deletes nothing.
#   ssh <host> "MIND_DIR=soul-mind UNIT=soul-mind EAR_UNIT=soul-spike bash -s" < server/mind/remote_setup.sh
set -euo pipefail
D="$HOME/${MIND_DIR:-soul-mind}"
UNIT="${UNIT:-soul-mind}"
EAR_UNIT="${EAR_UNIT:-soul-spike}"
PY="$HOME/soul-spike/.venv/bin/python"
mkdir -p "$D"
[ -s "$D/settings.json" ] || { cp "$D/mind/settings.example.json" "$D/settings.json"; chmod 600 "$D/settings.json"; echo "created $D/settings.json from the example"; }
TOKEN_FILE=$(systemctl show "$EAR_UNIT" -p Environment --value | tr ' ' '\n' | sed -n 's/^SOUL_TOKEN_FILE=//p')
[ -n "$TOKEN_FILE" ] && [ -s "$TOKEN_FILE" ] || { echo "cannot find the ear token file from $EAR_UNIT" >&2; exit 1; }
"$PY" -c "import pymongo" || { echo "pymongo missing in $PY" >&2; exit 1; }
since=$(date '+%Y-%m-%d %H:%M:%S')
if [ "$(systemctl show "$UNIT" -p LoadState --value 2>/dev/null)" = "loaded" ]; then
  sudo systemctl restart "$UNIT"          # running, or failing in its restart loop: restart with the new code
else
  sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
  sudo systemd-run --unit "$UNIT" --description "hello mind (minute timeline)" \
    --uid "$(id -u)" --gid "$(id -g)" -p WorkingDirectory="$D" -p Restart=on-failure -p RestartSec=5s \
    -p MemoryMax=512M -p CPUQuota=50% \
    "$PY" -m mind.runner --token-file "$TOKEN_FILE" --settings "$D/settings.json"
fi
for i in $(seq 30); do
  if sudo journalctl -u "$UNIT" --since "$since" --no-pager | grep -q 'mind: lane'; then break; fi
  sleep 2
done
echo "unit: $(systemctl is-active "$UNIT")"
sudo journalctl -u "$UNIT" --since "$since" --no-pager -o cat | tail -n 5
