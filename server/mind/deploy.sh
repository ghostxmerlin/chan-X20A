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
COMMON="$HERE/../common"
LOGFILES="$COMMON/hello_log.py $COMMON/hello_log_query.py $COMMON/prices.py"   # shipped into ~/soul-mind/ (the package's parent, on sys.path)
for f in $LOGFILES; do [ -f "$f" ] || { echo "$f not found" >&2; exit 1; }; done
SPOOL=${SOUL_SERVER_DIR:-soul-spike}/log-spool
SHA=$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo nogit)
[ -z "$(git -C "$HERE" status --porcelain 2>/dev/null)" ] || SHA="$SHA-dirty"
VER="$SHA $(date '+%Y-%m-%dT%H:%M:%S%z')"
if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$MIND_DIR/mind/"
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$MIND_DIR/"
  echo "(would write $MIND_DIR/CODE_VERSION: $VER; would create $SPOOL)"
  echo "(dry run: nothing copied)"
  exit 0
fi
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$MIND_DIR/mind"
# the shared modules first: a mind service restarted in between never finds new code without its modules
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $LOGFILES "$SOUL_SERVER_SSH:$MIND_DIR/"
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$MIND_DIR/mind/"
printf '%s\n' "$VER" | $SSH "$SOUL_SERVER_SSH" "cat > ~/$MIND_DIR/CODE_VERSION"
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$SPOOL && chmod 700 ~/$SPOOL"
# hello's voice clip texts (selfvoice), built locally and never committed:
#   python3 server/mind/selfvoice.py <voice library>/stackchan_playable_80.json .private/clip_texts.json
rsync -aic -e "$SSH" "$HERE/../../config/roles/" "$SOUL_SERVER_SSH:$MIND_DIR/roles/"     # hello's speaking roles
PERSONA="$HERE/../../config/persona/$(cat "$HERE/../../config/persona/active")"            # hello's persona (config/persona/README.md)
rsync -aic -e "$SSH" "$PERSONA" "$SOUL_SERVER_SSH:$MIND_DIR/persona.md"
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$MIND_DIR/tools"
rsync -aic -e "$SSH" "$HERE/../../config/tools/registry.json" "$SOUL_SERVER_SSH:$MIND_DIR/tools/registry.json"   # 工具注册表 (B1, docs/hello-tools.md)
CLIPS="$HERE/../../.private/clip_texts.json"
if [ -s "$CLIPS" ]; then rsync -aic -e "$SSH" "$CLIPS" "$SOUL_SERVER_SSH:$MIND_DIR/clip_texts.json"; else echo "no .private/clip_texts.json: hello's own voice is not matched by text"; fi
$SSH "$SOUL_SERVER_SSH" "MIND_DIR=$MIND_DIR UNIT=$UNIT EAR_UNIT=${SOUL_SERVER_UNIT:-soul-spike} bash -s" < "$HERE/remote_setup.sh"
