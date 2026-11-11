#!/bin/sh
# Copy the mind code to the server and (re)start its units (soul-mind, soul-eye, soul-notes). Settings come from the repo-root .env.
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
FILES="$(ls ./*.py | sed 's#^\./##' | tr '\n' ' ')settings.example.json"   # every module, so new ones are never left behind
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$MIND_DIR/mind"
if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$MIND_DIR/mind/"
  echo "(dry run: nothing copied)"
  exit 0
fi
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$MIND_DIR/mind/"
# hello's voice clip texts (selfvoice), built locally and never committed:
#   python3 server/mind/selfvoice.py <voice library>/stackchan_playable_80.json .private/clip_texts.json
rsync -aic -e "$SSH" "$HERE/../../config/roles/" "$SOUL_SERVER_SSH:$MIND_DIR/roles/"     # hello's speaking roles
CLIPS="$HERE/../../.private/clip_texts.json"
if [ -s "$CLIPS" ]; then rsync -aic -e "$SSH" "$CLIPS" "$SOUL_SERVER_SSH:$MIND_DIR/clip_texts.json"; else echo "no .private/clip_texts.json: hello's own voice is not matched by text"; fi
$SSH "$SOUL_SERVER_SSH" "MIND_DIR=$MIND_DIR UNIT=$UNIT EAR_UNIT=${SOUL_SERVER_UNIT:-soul-spike} bash -s" < "$HERE/remote_setup.sh"
