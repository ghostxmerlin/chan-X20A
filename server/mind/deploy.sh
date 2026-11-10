#!/bin/sh
# Copy the mind code to the server and (re)start its unit. Settings come from the repo-root .env.
#   server/mind/deploy.sh            copy, start or restart, wait until it runs
#   server/mind/deploy.sh --dry-run  only list what would be copied; changes nothing
# Copies server/mind/*.py and settings.example.json into ~/soul-mind/mind/. The real settings
# (~/soul-mind/settings.json) and everything in the database are never touched or deleted.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../ear/_env.sh"
HERE=$(cd "$(dirname "$0")" && pwd)
need SOUL_SERVER_SSH
MIND_DIR=${SOUL_MIND_DIR:-soul-mind}
UNIT=${SOUL_MIND_UNIT:-soul-mind}
cd "$HERE"
FILES="__init__.py settings.py records.py package.py strong.py store.py runner.py api.py probe.py settings.example.json"
FILES=$(for f in $FILES; do [ -e "$f" ] && printf '%s ' "$f"; done)
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$MIND_DIR/mind"
if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$MIND_DIR/mind/"
  echo "(dry run: nothing copied)"
  exit 0
fi
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$MIND_DIR/mind/"
$SSH "$SOUL_SERVER_SSH" "MIND_DIR=$MIND_DIR UNIT=$UNIT EAR_UNIT=${SOUL_SERVER_UNIT:-soul-spike} bash -s" < "$HERE/remote_setup.sh"
