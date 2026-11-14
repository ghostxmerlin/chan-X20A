#!/bin/sh
# Install the screen's CJK font parts on hello's SD card through the server (collector-0.4.96+): the robot's HTTP
# server is off, so the files go Mac -> server (rsync over ssh) -> the ear -> hello's live /mouth link. Nothing on
# the card or the robot changes but the catalog files themselves; the robot checks each against its own firmware
# catalog (size + SHA-256), writes .part, reads it back and renames, and loads a font variant once all its parts are
# there (no reboot).
#   scripts/push-assets-via-server.sh                       parts from assets/ui-font/sdcard/soul/font/
#   scripts/push-assets-via-server.sh --from <folder>       e.g. artifacts/collector-0.4.95-ui/sdcard-font
#   scripts/push-assets-via-server.sh --dry-run             only check the local files and list what would go
# Settings from the repo-root .env (SOUL_SERVER_SSH, SOUL_SERVER_DIR, see .env.example); the ear's token stays in
# <SOUL_SERVER_DIR>/token on the server and is read there. Neither is printed. Order: the license first, then parts.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -f "$ROOT/.env" ]; then
  set -a
  . "$ROOT/.env"
  set +a
fi
FROM="$ROOT/assets/ui-font/sdcard/soul/font"
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM=$2; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "usage: $0 [--from <folder>] [--dry-run]" >&2; exit 2 ;;
  esac
done
for v in SOUL_SERVER_SSH SOUL_SERVER_DIR; do
  eval "val=\${$v:-}"
  if [ -z "$val" ]; then echo "缺少 $v：请在 $ROOT/.env 里填写（格式见 .env.example）" >&2; exit 1; fi
done
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

# Local check against assets/ui-font/manifest.json (the catalog the firmware was generated from): prints the
# part=file list in install order, or stops.
ITEMS=$(python3 - "$ROOT/assets/ui-font/manifest.json" "$FROM" <<'EOF'
import hashlib, json, os, sys
manifest, folder = json.load(open(sys.argv[1])), sys.argv[2]
def check(name, nbytes, sha):
    p = os.path.join(folder, name)
    if not os.path.isfile(p):
        sys.exit(f"missing {name} in {folder} (generate with scripts/generate-cjk-font.py or use --from)")
    data = open(p, "rb").read()
    if len(data) != nbytes or hashlib.sha256(data).hexdigest() != sha:
        sys.exit(f"{name}: size or SHA-256 differs from assets/ui-font/manifest.json")
lic = manifest["license"]
check("LICENSE-OFL.txt", lic["bytes"], lic["sha256"])
items = ["license=LICENSE-OFL.txt"]
for v in manifest["variants"]:
    for n, part in enumerate(v["parts"]):
        name = os.path.basename(part["path"])
        if name != f"{v['id']}.{n}.bin":
            sys.exit(f"unexpected part path {part['path']}")
        check(name, part["bytes"], part["sha256"])
        items.append(f"{v['id']}.{n}={name}")
print(" ".join(items))
EOF
)
echo "local files verified: $ITEMS"
FILES=""
for item in $ITEMS; do FILES="$FILES $FROM/${item#*=}"; done
if [ "$DRY" = 1 ]; then
  echo "(dry run: nothing copied or pushed)"
  exit 0
fi

# Copy to <SOUL_SERVER_DIR>/assets-push/font/ (kept there; rsync only sends what changed).
$SSH "$SOUL_SERVER_SSH" "mkdir -p '$SOUL_SERVER_DIR/assets-push/font'"
# shellcheck disable=SC2086
rsync -ac -e "$SSH" $FILES "$SOUL_SERVER_SSH:$SOUL_SERVER_DIR/assets-push/font/"
echo "copied to the server; pushing over /mouth (hello must be online; it waits while hello speaks)"
# The CLI talks to the ear's loopback control (127.0.0.1:52753) with the token file; it prints progress only.
# shellcheck disable=SC2086
$SSH "$SOUL_SERVER_SSH" "cd '$SOUL_SERVER_DIR' && .venv/bin/python asset_push.py --token-file token --folder assets-push/font $ITEMS"
