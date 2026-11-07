# Sourced by the other scripts in this folder: loads the repo-root .env (never committed).
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
if [ -f "$ROOT/.env" ]; then
  set -a
  . "$ROOT/.env"
  set +a
fi
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

# need VAR...: stop with a hint when a setting is missing
need() {
  for v in "$@"; do
    eval "val=\${$v:-}"
    if [ -z "$val" ]; then
      echo "缺少 $v：请在 $ROOT/.env 里填写（格式见 .env.example）" >&2
      exit 1
    fi
  done
}
