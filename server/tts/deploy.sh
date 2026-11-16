#!/bin/sh
# Deploy soul-tts (hello's mouth) from the voice project: the code in ../voice/tts_service is the source.
#   server/tts/deploy.sh            copy soul_tts/ to ~/soul-tts/ on the server, start or restart the unit
#                                   (plus the run log's server/common/hello_log.py and prices.py next to the package)
#   server/tts/deploy.sh --dry-run  only list what would be copied
# Settings come from the repo-root .env (SOUL_SERVER_SSH). Nothing on the server is deleted.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../ear/_env.sh"
HERE=$(cd "$(dirname "$0")" && pwd)
need SOUL_SERVER_SSH
SRC=${SOUL_TTS_SRC:-$HERE/../../../voice/tts_service/soul_tts}
TTS_DIR=${SOUL_TTS_DIR:-soul-tts}
[ -f "$SRC/server.py" ] || { echo "soul_tts not found at $SRC" >&2; exit 1; }
COMMON="$HERE/../common"
LOGFILES="$COMMON/hello_log.py $COMMON/prices.py"       # soul_tts/runlog.py imports them from ~/soul-tts/
for f in $LOGFILES; do [ -f "$f" ] || { echo "$f not found" >&2; exit 1; }; done
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$TTS_DIR/soul_tts"
if [ "${1:-}" = "--dry-run" ]; then
  rsync -aicn --exclude __pycache__ -e "$SSH" "$SRC/" "$SOUL_SERVER_SSH:$TTS_DIR/soul_tts/"
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$TTS_DIR/"
  echo "(dry run: nothing copied)"; exit 0
fi
rsync -aic --exclude __pycache__ -e "$SSH" "$SRC/" "$SOUL_SERVER_SSH:$TTS_DIR/soul_tts/"
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$TTS_DIR/"
$SSH "$SOUL_SERVER_SSH" "TTS_DIR=$TTS_DIR EAR_UNIT=${SOUL_SERVER_UNIT:-soul-spike} bash -s" < "$HERE/remote_setup.sh"
