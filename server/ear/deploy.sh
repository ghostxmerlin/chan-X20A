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
FILES="ear_server.py ear_log.py recorder.py sound_kinds.py hearing_protocol.py tingwu.py tingwu_switch.py presence.py mouth_link.py mouth_mode.py realtime_link.py vision_burst.py web_search.py hello_db.py hello_db_import.py vision_link.py signal_link.py"
COMMON="$HERE/../common"
LOGFILES="$COMMON/hello_log.py $COMMON/prices.py"   # the run log's shared modules (the ear never reads the log: no hello_log_query), copied flat next to ear_server.py, before FILES
for f in $LOGFILES; do [ -f "$f" ] || { echo "$f not found" >&2; exit 1; }; done
GITDIR="$HERE/.."
SHA=$(git -C "$GITDIR" rev-parse --short HEAD 2>/dev/null || echo nogit)
[ -z "$(git -C "$GITDIR" status --porcelain 2>/dev/null)" ] || SHA="$SHA-dirty"
VER="$SHA $(date '+%Y-%m-%dT%H:%M:%S%z')"

if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
  echo "(would write $SOUL_SERVER_DIR/CODE_VERSION: $VER; would create $SOUL_SERVER_DIR/log-spool)"
  echo "(dry run: nothing copied; lines starting with '<' would be sent)"
  exit 0
fi

# the shared modules first: an ear restarted in between never finds new code without its modules
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
printf '%s\n' "$VER" | $SSH "$SOUL_SERVER_SSH" "cat > '$SOUL_SERVER_DIR/CODE_VERSION'"
$SSH "$SOUL_SERVER_SSH" "mkdir -p '$SOUL_SERVER_DIR/log-spool' && chmod 700 '$SOUL_SERVER_DIR/log-spool'"
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
