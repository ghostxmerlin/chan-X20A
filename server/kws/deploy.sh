#!/bin/sh
# Deploy the keyword ear (关键词耳朵) to the server: ~/soul-kws/, transient unit soul-kws on 127.0.0.1:52760.
#   server/kws/deploy.sh            copy the code, start or restart the unit, then write the ear's kws.url
#                                   (the ear connects at its next restart: server/ear/deploy.sh)
#   server/kws/deploy.sh --dry-run  only list what would be copied
#   server/kws/deploy.sh --off      stop the unit and remove kws.url (the ear stops sending at its next restart)
# The model (hubert-zh-l9.onnx, made on the server by export_onnx.py) and the keyword table (people's voices)
# live only on the server, in ~/soul-kws/, and are never copied from or to this repo.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../ear/_env.sh"
HERE=$(cd "$(dirname "$0")" && pwd)
need SOUL_SERVER_SSH SOUL_SERVER_DIR
KWS_DIR=${SOUL_KWS_DIR:-soul-kws}
FILES="$HERE/spotter.py $HERE/kws_server.py $HERE/kws_tool.py $HERE/export_onnx.py"
SHA=$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo nogit)
[ -z "$(git -C "$HERE" status --porcelain 2>/dev/null)" ] || SHA="$SHA-dirty"
VER="$SHA $(date '+%Y-%m-%dT%H:%M:%S%z')"

if [ "${1:-}" = "--off" ]; then
  $SSH "$SOUL_SERVER_SSH" "rm -f ~/$SOUL_SERVER_DIR/kws.url; sudo systemctl stop soul-kws 2>/dev/null || true; echo 'soul-kws stopped, kws.url removed'"
  exit 0
fi
$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$KWS_DIR"
if [ "${1:-}" = "--dry-run" ]; then
  # shellcheck disable=SC2086
  rsync -aicn -e "$SSH" $FILES "$SOUL_SERVER_SSH:$KWS_DIR/"
  echo "(dry run: nothing copied)"; exit 0
fi
# shellcheck disable=SC2086
rsync -aic -e "$SSH" $FILES "$SOUL_SERVER_SSH:$KWS_DIR/"
printf '%s\n' "$VER" | $SSH "$SOUL_SERVER_SSH" "cat > ~/$KWS_DIR/CODE_VERSION"
$SSH "$SOUL_SERVER_SSH" "KWS_DIR=$KWS_DIR EAR_DIR=$SOUL_SERVER_DIR bash -s" < "$HERE/remote_setup.sh"
