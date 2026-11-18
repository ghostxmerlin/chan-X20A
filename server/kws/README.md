# 关键词耳朵 (keyword ear)

A local process on the server that finds keywords in what the robot hears **by sound, never via text**. It
started with the robot's name 「デュナメス」 (Dynames). SenseVoice spells that name differently every time
(Dナ, B南米, 叮那米, 点到名…), so the name cannot be found in the text.

- **How:** the ear hands each finished speech segment's raw audio to the keyword ear (`server/ear/kws_link.py`,
  without waiting). A local model (chinese-hubert-base cut to 9 layers, ONNX, CPU) turns the audio into frame
  features. Subsequence DTW then compares those features with recorded samples of each keyword (query by example).
- **Out:** hits and near-misses become ear events (`{"type": "keyword"}`) and run-log rows (`heard.keyword`,
  听到关键词, on the sentence's trace). It only reports; mind decides what a hit means.
- **Cost:** none (local CPU, no API). Not behind the paid switches or the pause.

## Keyword table (server only, `~/soul-kws/table/`, people's voices: never in the repo)

    table/<keyword>/keyword.json   {"label": "Dynames", "threshold": 0.35, "near": 0.05, "min_span": 0.6}
    table/<keyword>/*.wav          16 kHz mono samples of the keyword said on its own (5-10 is enough)

To add samples, cut them from a recorded session by time, then reload (no restart, no training):

    cd ~/soul-kws && .venv/bin/python kws_tool.py enrol table <keyword> ~/soul-spike/recordings/<day>/<session> HH:MM:SS ...
    curl -s -X POST 127.0.0.1:52760/reload

Score: lower = more alike. A hit is below `threshold`, and a near-miss is within `near` above it. `min_span`
stops a whole keyword from matching a much shorter sound (the false alarms were 嗯 / 可以 / Yeah).

## Test on recordings before changing a threshold

    .venv/bin/python kws_tool.py eval table runs/<name>.jsonl ~/soul-spike/recordings/<day> ...
    .venv/bin/python kws_tool.py report runs/<name>.jsonl

## Deploy

    server/kws/deploy.sh             # code -> ~/soul-kws, unit soul-kws (127.0.0.1:52760), writes the ear's kws.url
    server/ear/deploy.sh             # the ear reads kws.url at start (also ships kws_link.py)
    server/kws/deploy.sh --off       # stop it and remove kws.url

Status: `curl -s 127.0.0.1:52760/status` on the server. Tests: `tests/test_kws_link.py` and `tests/test_kws_spotter.py`.
The offline spike that chose this method is in `spikes/kws` (2026-11-17).
