#!/bin/bash
# Runs ON the server (piped by deploy.sh): a lean runtime venv (no torch), the model and keyword table in place,
# then start or restart the transient unit soul-kws on 127.0.0.1:52760, and point the ear at it (kws.url).
# First time only: the model is exported from the spike folder's export venv if ~/soul-kws has none
# (~/soul-spike/kws/.venv-export, see export_onnx.py), and the table is taken from ~/soul-spike/kws/table.
set -euo pipefail
D="$HOME/${KWS_DIR:-soul-kws}"
EAR="$HOME/${EAR_DIR:-soul-spike}"
UNIT="${UNIT:-soul-kws}"
PORT="${PORT:-52760}"
SPIKE="$HOME/soul-spike/kws"
cd "$D"
if [ ! -x .venv/bin/python ]; then
  "$(readlink -f "$EAR/.venv/bin/python")" -m venv .venv
  .venv/bin/pip -q install --upgrade pip
fi
.venv/bin/pip -q install onnxruntime numpy librosa soundfile opuslib_next
if [ ! -s hubert-zh-l9.onnx ]; then
  if [ -s "$SPIKE/hubert-zh-l9.onnx" ]; then cp "$SPIKE/hubert-zh-l9.onnx" .;
  else echo "no model: run export_onnx.py in an export venv first (see its docstring)" >&2; exit 1; fi
fi
if [ ! -d table ]; then
  if [ -d "$SPIKE/table" ]; then cp -r "$SPIKE/table" table; else mkdir -p table; fi
  chmod 700 table
fi
since=$(date '+%Y-%m-%d %H:%M:%S')
if [ "$(systemctl show "$UNIT" -p LoadState --value 2>/dev/null)" = "loaded" ] && \
   [ "$(systemctl show "$UNIT" -p ActiveState --value)" != "inactive" ]; then
  sudo systemctl restart "$UNIT"
else
  sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
  sudo systemd-run --unit "$UNIT" --description "hello keyword ear (关键词耳朵)" \
    --uid "$(id -u)" --gid "$(id -g)" -p WorkingDirectory="$D" -p Restart=on-failure -p RestartSec=5s \
    -p MemoryMax=1500M -p CPUQuota=200% -p Nice=5 \
    "$D/.venv/bin/python" "$D/kws_server.py" --table "$D/table" --model "$D/hubert-zh-l9.onnx" --port "$PORT" --threads 2
fi
for i in $(seq 30); do
  if sudo journalctl -u "$UNIT" --since "$since" --no-pager | grep -q 'kws listening on'; then break; fi
  sleep 2
done
echo "unit: $(systemctl is-active "$UNIT")"
sudo journalctl -u "$UNIT" --since "$since" --no-pager -o cat | tail -n 3
echo "http://127.0.0.1:$PORT" > "$EAR/kws.url"
echo "wrote $EAR/kws.url (the ear connects at its next restart)"
