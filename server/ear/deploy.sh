#!/bin/sh
# Copy the ear server code to the server and restart its systemd unit. Settings come from .env.
#   server/ear/deploy.sh            copy changed files, restart, wait until it listens again
#   server/ear/deploy.sh --dry-run  only list what would be copied; changes nothing
# Only the code files listed below are copied. Models, token, speakers.npz and recordings on the server
# are never touched, and nothing is deleted there.
set -eu
. "$(dirname "$0")/_env.sh"
need SOUL_SERVER_SSH SOUL_SERVER_DIR SOUL_SERVER_UNIT
cd "$HERE"
FILES="ear_server.py recorder.py sound_kinds.py hearing_protocol.py tingwu.py tingwu_switch.py mouth_link.py hello_db.py hello_db_import.py vision_link.py"

if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
  echo "(dry run: nothing copied; lines starting with '<' would be sent)"
  exit 0
fi

# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
$SSH "$SOUL_SERVER_SSH" "
  set -e
  since=\$(date '+%Y-%m-%d %H:%M:%S')
  sudo systemctl restart '$SOUL_SERVER_UNIT'
  for i in \$(seq 60); do
    if sudo journalctl -u '$SOUL_SERVER_UNIT' --since \"\$since\" --no-pager | grep -q 'listening on'; then break; fi
    sleep 2
  done
  echo \"unit: \$(systemctl is-active '$SOUL_SERVER_UNIT')\"
  sudo journalctl -u '$SOUL_SERVER_UNIT' --since \"\$since\" --no-pager -o cat | tail -n 5
"
