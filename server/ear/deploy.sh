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
FILES="ear_server.py ear_log.py recorder.py sound_kinds.py hearing_protocol.py tingwu.py tingwu_switch.py presence.py mouth_link.py mouth_mode.py realtime_link.py rt_base.py rt_rules.py rt_gate.py rt_looks.py rt_tools.py rt_skills.py rt_interrupt.py rt_events.py rt_manner.py vision_burst.py web_search.py hello_db.py hello_db_import.py vision_link.py body_sense.py mode_span.py signal_link.py kws_link.py ask_api.py intent.py"
COMMON="$HERE/../common"
LOGFILES="$COMMON/hello_log.py $COMMON/prices.py"   # the run log's shared modules (the ear never reads the log: no hello_log_query), copied flat next to ear_server.py, before FILES
for f in $LOGFILES; do [ -f "$f" ] || { echo "$f not found" >&2; exit 1; }; done
PERSONA="$HERE/../../config/persona/$(cat "$HERE/../../config/persona/active")"   # hello's persona (config/persona/README.md) -> persona.md
[ -f "$PERSONA" ] || { echo "$PERSONA not found" >&2; exit 1; }
GITDIR="$HERE/.."
SHA=$(git -C "$GITDIR" rev-parse --short HEAD 2>/dev/null || echo nogit)
[ -z "$(git -C "$GITDIR" status --porcelain 2>/dev/null)" ] || SHA="$SHA-dirty"
VER="$SHA $(date '+%Y-%m-%dT%H:%M:%S%z')"

if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
  rsync -aicn -e "$SSH" "$PERSONA" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/persona.md"
  rsync -aicn -e "$SSH" "$(dirname "$PERSONA")/" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/personas/"
  echo "(would write $SOUL_SERVER_DIR/CODE_VERSION: $VER; would create $SOUL_SERVER_DIR/log-spool)"
  echo "(dry run: nothing copied; lines starting with '<' would be sent)"
  exit 0
fi

# the shared modules first: an ear restarted in between never finds new code without its modules
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/"
rsync -aic -e "$SSH" "$PERSONA" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/persona.md"
rsync -aic -e "$SSH" "$(dirname "$PERSONA")/" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/personas/"   # every persona + voices.json (set_voice, 2026-11-18)
rsync -aic -e "$SSH" "$HERE/../../config/skills/skills.json" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/skills.json"   # Docking skills (2026-11-18)
rsync -aic -e "$SSH" "$HERE/../../config/skills/examples.json" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/skill_examples.json"
rsync -aic --delete -e "$SSH" "$HERE/../../config/skills/skills/" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/skills/"   # each skill's full text
$SSH "$SOUL_SERVER_SSH" "mkdir -p $SOUL_SERVER_DIR/intent-tools"   # (relative = from the home directory)
rsync -aic -e "$SSH" "$HERE/../../tools/intent/train.py" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/intent-tools/train.py"   # nightly retraining of the auto mode
$SSH "$SOUL_SERVER_SSH" "mkdir -p $SOUL_SERVER_DIR/skills-tools"
rsync -aic -e "$SSH" "$HERE/../../tools/skills/eval.py" "$HERE/../../tools/skills/cases.json" "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/skills-tools/"   # skill acceptance test
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
