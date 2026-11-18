"""Synthesize training clips with a local VOICEVOX engine (127.0.0.1:50021, free, no API cost; owner approved
2026-11-17 for TRAINING data only, tests use real people only): many voices saying the keyword (positives) and
confusable non-keywords (hard negatives: halves of the name, shuffled syllables, look-alike words).

usage: gen_tts.py <out dir> [--engine http://127.0.0.1:50021] [--per-style 2]
writes <out>/pos/*.wav, <out>/neg/*.wav (16 kHz mono) and <out>/tts.jsonl
"""
import argparse
import io
import json
import os
import random
import urllib.parse
import urllib.request

import numpy as np

SR = 16000
# positives as AquesTalk kana: ONE word (plain text splits it into デュ'ナ / メ'ス), the accent on each mora in turn
POS_KANA = ["デュ'ナメス", "デュナ'メス", "デュナメ'ス", "デュ'ナメス？", "デュナ'メス？", "ネ'エ、デュ'ナメス",
            "デュナ'メス、オイデ'", "デュ'ナメ_ス"]
NEG_TEXTS = ["デュナ", "メス", "ナメス", "デュメ", "ナデュスメ", "スメナデュ", "ダイナモ", "ディナー", "ナミダ",
             "デュエル", "ダイナミック", "ドラマ", "なめこ", "メガネ", "デュオ", "ナメコ", "あなたです", "ですね"]


def call(engine, path, data=None, params=None, timeout=60):
    url = f"{engine}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    body = json.dumps(data).encode() if data is not None else (b"" if path.startswith(("/audio_query", "/accent_phrases")) else None)
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def to16k(wav_bytes):
    import soundfile as sf
    import librosa
    x, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    return librosa.resample(x, orig_sr=sr, target_sr=SR) if sr != SR else x


def main():
    import soundfile as sf
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--engine", default="http://127.0.0.1:50021")
    ap.add_argument("--per-style", type=int, default=2, help="positive clips per voice style")
    ap.add_argument("--neg-per-style", type=int, default=2)
    a = ap.parse_args()
    random.seed(7)
    for d in ("pos", "neg"):
        os.makedirs(os.path.join(a.out, d), exist_ok=True)
    speakers = json.loads(call(a.engine, "/speakers"))
    styles = [(sp["name"], st["name"], st["id"]) for sp in speakers for st in sp["styles"]
              if st.get("type", "talk") in ("talk", "streaming_talk")]
    print(len(styles), "voice styles", flush=True)
    log = open(os.path.join(a.out, "tts.jsonl"), "a")
    n = 0
    for name, style, sid in styles:
        jobs = [("pos", random.choice(POS_KANA)) for _ in range(a.per_style)] + \
               [("neg", random.choice(NEG_TEXTS)) for _ in range(a.neg_per_style)]
        for kind, text in jobs:
            try:
                q = json.loads(call(a.engine, "/audio_query", params={"text": "あ", "speaker": sid})) if kind == "pos" \
                    else json.loads(call(a.engine, "/audio_query", params={"text": text, "speaker": sid}))
                if kind == "pos":
                    q["accent_phrases"] = json.loads(call(a.engine, "/accent_phrases",
                                                          params={"text": text, "speaker": sid, "is_kana": "true"}))
                q["speedScale"] = round(random.uniform(0.8, 1.25), 2)
                q["pitchScale"] = round(random.uniform(-0.08, 0.08), 3)
                q["intonationScale"] = round(random.uniform(0.7, 1.4), 2)
                q["volumeScale"] = 1.0
                q["prePhonemeLength"] = 0.1
                q["postPhonemeLength"] = 0.1
                x = to16k(call(a.engine, "/synthesis", data=q, params={"speaker": sid}, timeout=120))
            except Exception as error:     # noqa: BLE001 - one voice failing must not stop the run
                print("fail", name, style, text, type(error).__name__, flush=True)
                continue
            fn = f"{kind}/v{sid:03d}-{n:05d}.wav"
            sf.write(os.path.join(a.out, fn), x / (np.abs(x).max() + 1e-6) * 0.5, SR)
            log.write(json.dumps({"file": fn, "kind": kind, "text": text, "speaker": name, "style": style, "id": sid,
                                  "speed": q["speedScale"], "pitch": q["pitchScale"],
                                  "dur": round(len(x) / SR, 2)}, ensure_ascii=False) + "\n")
            n += 1
        log.flush()
        print(f"{name}/{style}: {n} clips", flush=True)


if __name__ == "__main__":
    main()
