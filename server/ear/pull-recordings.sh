#!/bin/sh
# Copy the server's recordings to this Mac ($SOUL_RECORDINGS_DIR/server/). Only adds and updates
# files; never deletes anything on either side. Safe to run any time, as often as you like.
set -eu
. "$(dirname "$0")/_env.sh"
need SOUL_SERVER_SSH SOUL_SERVER_DIR
DEST="${SOUL_RECORDINGS_DIR:-$ROOT/.private/recordings}/server"
mkdir -p "$DEST"
rsync -a --partial --human-readable --stats -e "$SSH" \
  "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/recordings/" "$DEST/" \
  | grep -E "Number of regular files transferred|Total transferred file size" || true
echo "done: $DEST"
