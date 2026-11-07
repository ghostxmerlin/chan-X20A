#!/bin/bash
# Runs ON the server (piped over ssh, see README.md). Creates ~/$DIR only: venv + models + token.
# Touches nothing outside that directory, except that Opus needs the system libopus0 (apt, see README).
#   ssh <user@host> "DIR=<deploy dir> bash -s" < server/ear/remote_setup.sh
set -euo pipefail
D="$HOME/${DIR:-soul-ear}"
mkdir -p "$D/models"
cd "$D"
[ -d .venv ] || python3.11 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q "sherpa-onnx==2.0.1" numpy "websockets>=13" "opuslib_next>=1.3.1"
.venv/bin/python -c "import sherpa_onnx, websockets, numpy, opuslib_next; print('sherpa_onnx', sherpa_onnx.__version__, '| websockets', websockets.__version__, '| numpy', numpy.__version__)"

B=https://github.com/k2-fsa/sherpa-onnx/releases/download
cd models
dl() {
  f=$(basename "$1")
  if [ -e "$f" ] || [ -d "${f%.tar.bz2}" ]; then echo "have $f"; return; fi
  curl -sSL --fail -o "$f" "$B/$1" && echo "ok $f"
  case "$f" in *.tar.bz2) tar xjf "$f" && rm "$f";; esac
}
dl asr-models/silero_vad.onnx
dl speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx
dl asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
dl audio-tagging-models/sherpa-onnx-ced-mini-audio-tagging-2024-04-19.tar.bz2
du -sh .
cd ..
if [ ! -s token ]; then openssl rand -hex 16 > token; chmod 600 token; fi
echo "setup done in $D"
