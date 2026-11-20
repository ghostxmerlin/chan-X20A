#!/bin/sh
# The Mac stand-in's own server stack (owner 2026-11-19 night: the server assumes one robot; "clone一份现在的数据给mac").
# Copies this checkout's ear / mind code into ~/soul-spike-mac and ~/soul-mind-mac and (re)starts loopback-only
# units beside the robot's, on the cloned database hello_mac (tools/mac/clone_db.py):
#   soul-spike-mac  ear     127.0.0.1:52832 (ask API 52843)    the Mac app reaches it through an ssh tunnel
#   soul-mind-mac   mind    127.0.0.1:52840
#   soul-tts-mac    tts     127.0.0.1:52850 (mouth 52851)      soul-tts takes one device: the robot keeps its own
#   soul-eye-mac / soul-notes-mac / soul-memory-mac   the vision eye, 纪要 and memory workers on hello_mac (2026-11-20:
#                   without them the Mac's 全开 turned 识别 / 纪要 on with nothing doing the work)
# The keyword ear (soul-kws), models, fillers, personas, hello.env and the ear token are shared read-only. Nothing of
# the robot's stack is restarted, copied over or written. No public port: everything listens on 127.0.0.1.
#   tools/mac/deploy_stack.sh            copy + (re)start the units
#   tools/mac/deploy_stack.sh --stop     stop the units (data stays)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
. "$ROOT/server/ear/_env.sh"
need SOUL_SERVER_SSH
EAR_DIR=soul-spike-mac
MIND_DIR=soul-mind-mac
if [ "${1:-}" = "--stop" ]; then
  $SSH "$SOUL_SERVER_SSH" "for u in soul-spike-mac soul-mind-mac soul-tts-mac soul-eye-mac soul-notes-mac soul-memory-mac; do sudo systemctl stop \$u 2>/dev/null || true; echo \"\$u: \$(systemctl is-active \$u 2>/dev/null || true)\"; done"
  exit 0
fi
EAR_FILES=$(sed -n 's/^FILES="\(.*\)"$/\1/p' "$ROOT/server/ear/deploy.sh")
[ -n "$EAR_FILES" ] || { echo "cannot read the ear's file list from server/ear/deploy.sh" >&2; exit 1; }
PERSONA="$ROOT/config/persona/$(cat "$ROOT/config/persona/active")"
SHA=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)
[ -z "$(git -C "$ROOT" status --porcelain -- server config 2>/dev/null)" ] || SHA="$SHA-dirty"
VER="$SHA $(date '+%Y-%m-%dT%H:%M:%S%z') mac-stack"

$SSH "$SOUL_SERVER_SSH" "mkdir -p ~/$EAR_DIR/log-spool ~/$MIND_DIR/mind ~/$MIND_DIR/tools ~/$MIND_DIR/log-spool && chmod 700 ~/$EAR_DIR/log-spool ~/$MIND_DIR/log-spool"
# ear: the same files as server/ear/deploy.sh
(cd "$ROOT/server/ear" && rsync -aic -e "$SSH" "$ROOT/server/common/hello_log.py" "$ROOT/server/common/prices.py" "$ROOT/server/common/bodies.py" $EAR_FILES "$SOUL_SERVER_SSH:$EAR_DIR/")
rsync -aic -e "$SSH" "$PERSONA" "$SOUL_SERVER_SSH:$EAR_DIR/persona.md"
rsync -aic -e "$SSH" "$(dirname "$PERSONA")/" "$SOUL_SERVER_SSH:$EAR_DIR/personas/"
rsync -aic -e "$SSH" "$ROOT/config/skills/skills.json" "$SOUL_SERVER_SSH:$EAR_DIR/skills.json"
rsync -aic -e "$SSH" "$ROOT/config/skills/examples.json" "$SOUL_SERVER_SSH:$EAR_DIR/skill_examples.json"
rsync -aic --delete -e "$SSH" "$ROOT/config/skills/skills/" "$SOUL_SERVER_SSH:$EAR_DIR/skills/"
printf '%s\n' "$VER" | $SSH "$SOUL_SERVER_SSH" "cat > ~/$EAR_DIR/CODE_VERSION"
# mind: as server/mind/deploy.sh (code, run-log modules, roles, persona, tool registry)
(cd "$ROOT/server/mind" && rsync -aic -e "$SSH" ./*.py settings.example.json "$SOUL_SERVER_SSH:$MIND_DIR/mind/")
rsync -aic -e "$SSH" "$ROOT/server/common/hello_log.py" "$ROOT/server/common/hello_log_query.py" "$ROOT/server/common/prices.py" "$ROOT/server/common/bodies.py" "$SOUL_SERVER_SSH:$MIND_DIR/"
rsync -aic -e "$SSH" "$ROOT/config/roles/" "$SOUL_SERVER_SSH:$MIND_DIR/roles/"
rsync -aic -e "$SSH" "$PERSONA" "$SOUL_SERVER_SSH:$MIND_DIR/persona.md"
rsync -aic -e "$SSH" "$ROOT/config/tools/registry.json" "$SOUL_SERVER_SSH:$MIND_DIR/tools/registry.json"
printf '%s\n' "$VER" | $SSH "$SOUL_SERVER_SSH" "cat > ~/$MIND_DIR/CODE_VERSION"

$SSH "$SOUL_SERVER_SSH" "EAR_DIR=$EAR_DIR MIND_DIR=$MIND_DIR bash -s" <<'REMOTE'
set -euo pipefail
E="$HOME/$EAR_DIR"; M="$HOME/$MIND_DIR"; PY="$HOME/soul-spike/.venv/bin/python"
# server-side files the copy starts from (never overwritten once there: the Mac's own voiceprints / settings evolve)
[ -s "$E/speakers.npz" ] || cp "$HOME/soul-spike/speakers.npz" "$E/speakers.npz"
[ -s "$E/kws.url" ] || { [ -s "$HOME/soul-spike/kws.url" ] && cp "$HOME/soul-spike/kws.url" "$E/kws.url"; } || true
[ -s "$M/settings.json" ] || { cp "$HOME/soul-mind/settings.json" "$M/settings.json"; chmod 600 "$M/settings.json"; }
[ -s "$M/clip_texts.json" ] || { [ -s "$HOME/soul-mind/clip_texts.json" ] && cp "$HOME/soul-mind/clip_texts.json" "$M/"; } || true
TOKEN="$HOME/soul-spike/token"
since=$(date '+%Y-%m-%d %H:%M:%S')
start() {   # unit description args...
  local u=$1 d=$2; shift 2
  if [ "$(systemctl show "$u" -p LoadState --value 2>/dev/null)" = "loaded" ]; then sudo systemctl stop "$u" || true; fi
  sudo systemctl reset-failed "$u" 2>/dev/null || true
  sudo systemd-run --unit "$u" --description "$d" --uid "$(id -u)" --gid "$(id -g)" -p Restart=on-failure -p RestartSec=5s "$@" >/dev/null
}
start soul-tts-mac "hello tts (Mac stand-in)" -p WorkingDirectory="$HOME/soul-tts" -p MemoryMax=1G -p CPUQuota=50% \
  --setenv=SOUL_TTS_PLAYER=stream --setenv=SOUL_TTS_TOKEN_FILE="$HOME/soul-tts/api.token" \
  --setenv=SOUL_TTS_DEVICE_TOKEN_FILE="$TOKEN" --setenv=SOUL_TTS_ENV_FILE="$HOME/soul-spike/hello.env" \
  --setenv=SOUL_TTS_LISTEN=127.0.0.1:52850 --setenv=SOUL_TTS_MOUTH_LISTEN=127.0.0.1:52851 \
  "$PY" -m soul_tts serve
start soul-mind-mac "hello mind (Mac stand-in)" -p WorkingDirectory="$M" -p MemoryMax=512M -p CPUQuota=50% \
  --setenv=SOUL_DB=hello_mac --setenv=SOUL_EAR_ASK_URL=http://127.0.0.1:52843/ask \
  --setenv=SOUL_LOG_SPOOL="$M/log-spool" --setenv=SOUL_CODE_VERSION_FILE="$M/CODE_VERSION" \
  "$PY" -m mind.runner --token-file "$TOKEN" --settings "$M/settings.json" --api-port 52840 --tts-url http://127.0.0.1:52850
for w in eye:eye_runner notes:notes_runner memory:memory_worker; do   # the workers beside mind, on the Mac's own data
  extra=""
  [ "${w%%:*}" = eye ] && extra="--recordings $E/recordings"
  [ "${w%%:*}" = eye ] || extra="--clip-texts $M/clip_texts.json"
  start "soul-${w%%:*}-mac" "hello ${w%%:*} (Mac stand-in)" -p WorkingDirectory="$M" -p MemoryMax=512M -p CPUQuota=50% \
    --setenv=SOUL_DB=hello_mac --setenv=SOUL_LOG_SPOOL="$M/log-spool" --setenv=SOUL_CODE_VERSION_FILE="$M/CODE_VERSION" \
    "$PY" -m "mind.${w#*:}" --settings "$M/settings.json" $extra
done
start soul-spike-mac "hello ear (Mac stand-in)" -p WorkingDirectory="$E" -p MemoryMax=3G -p CPUQuota=150% \
  --setenv=SOUL_TOKEN_FILE="$TOKEN" --setenv=SOUL_DB=hello_mac --setenv=SOUL_MIND_URL=http://127.0.0.1:52840 \
  --setenv=SOUL_TTS_MOUTH_URL=ws://127.0.0.1:52851/mouth --setenv=SOUL_LOG_SPOOL="$E/log-spool" \
  --setenv=SOUL_CODE_VERSION_FILE="$E/CODE_VERSION" \
  "$PY" "$E/ear_server.py" --host 127.0.0.1 --port 52832 --ask-port 52843 --models "$HOME/soul-spike/models" --threads 2
for i in $(seq 60); do
  if sudo journalctl -u soul-spike-mac --since "$since" --no-pager | grep -q 'listening on'; then break; fi
  sleep 2
done
for u in soul-tts-mac soul-mind-mac soul-eye-mac soul-notes-mac soul-memory-mac soul-spike-mac; do
  echo "== $u: $(systemctl is-active $u)"
  sudo journalctl -u "$u" --since "$since" --no-pager -o cat | grep -v -i -E 'token|key|uri|password' | tail -n 3
done
REMOTE
