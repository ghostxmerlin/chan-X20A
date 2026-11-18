"""Training set for the keyword verifier (stage 2). Every clip, positive or negative, goes through the SAME path:
augment -> placed in a 1.5 s window over a real background (half from past days, half from today, so the date is not
a cue) -> stage 1 (DTW against the keyword's samples) -> the matched stretch, resampled to 40 frames of the frame
model, plus stage-1 numbers. Fable's review 2026-11-17: fix the date confound, same augmentation for both classes.

Sources
  real positives   <pos dir>/positives.jsonl (locate.py), group = session (leave-one-session-out)
  TTS              <tts dir>/tts.jsonl (gen_tts.py): pos = keyword, neg = halves / shuffles / look-alikes
  past negatives   past10.jsonl (VAD segments of days before the name existed), group = day
  today negatives  today's sessions away from every known saying (±3 s), group = "today"
usage: build_set.py <table> <pos dir> <tts dir> <past10.jsonl> <today sessions file> <out.npz>
"""
import json
import os
import random
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import kws_tool  # noqa: E402
import spotter   # noqa: E402

SR = spotter.SR
WIN = int(1.5 * SR)
FR = 40                      # stage-2 input: matched stretch resampled to 40 frames
REC = os.path.expanduser("~/soul-spike/recordings")
REAL_POS_COPIES = int(os.environ.get("REAL_POS_COPIES", "30"))
TTS_COPIES = int(os.environ.get("TTS_COPIES", "2"))
PAST_NEG = int(os.environ.get("PAST_NEG", "4000"))
TODAY_NEG = int(os.environ.get("TODAY_NEG", "1500"))


def ir(rng):
    """A crude room: exponentially decaying noise, 50-300 ms."""
    tau = rng.uniform(0.05, 0.3)
    n = int(tau * 3 * SR)
    h = rng.standard_normal(n) * np.exp(-np.arange(n) / (tau * SR))
    h[0] = 1.0
    return h / np.sqrt(np.sum(h ** 2))


def augment(x, rng):
    import librosa
    x = np.asarray(x, np.float32)
    r = rng.uniform(0.9, 1.1)                                 # speed (and pitch with it)
    x = librosa.resample(x, orig_sr=SR, target_sr=int(SR / r))
    if rng.random() < 0.5:                                    # pitch ±2 semitones: a rough "other voice"
        x = librosa.effects.pitch_shift(x, sr=SR, n_steps=rng.uniform(-2, 2))
    if rng.random() < 0.6:                                    # far field
        wet = np.convolve(x, ir(rng))[:len(x)]
        m = rng.uniform(0.2, 0.7)
        x = (1 - m) * x + m * wet
    return x.astype(np.float32)


def place(clip, bg, rng, snr_db):
    """clip into a 1.5 s window of background bg at the given SNR (by RMS)."""
    clip = clip[:WIN - SR // 10]
    out = bg[:WIN].astype(np.float32).copy()
    off = rng.integers(0, WIN - len(clip) + 1)
    pc = np.sqrt(np.mean(clip ** 2)) + 1e-9
    pb = np.sqrt(np.mean(out ** 2)) + 1e-9
    gain = pb * 10 ** (snr_db / 20) / pc
    out[off:off + len(clip)] += clip * gain
    peak = np.abs(out).max()
    if peak > 0.99:
        out *= 0.99 / peak
    return out * rng.uniform(0.5, 1.5)


def stage1(kw, q, skip=()):
    """Best sample by mean path cost -> numbers and the matched stretch of q."""
    best = None
    for name, t in kw.samples:
        if name in skip:
            continue
        C, wp, mean = spotter.align(t, q)
        per = np.zeros(len(t)); cnt = np.zeros(len(t))
        for i, j in wp:
            per[i] += C[i, j]; cnt[i] += 1
        per /= np.maximum(cnt, 1)
        f0, f1 = int(wp[:, 1].min()), int(wp[:, 1].max())
        span = (f1 - f0 + 1) / len(t)
        seg = max(c.mean() for c in np.array_split(per, 4))
        score = mean + max(0.0, kw.min_span - span)
        if best is None or score < best["score"]:
            best = {"score": score, "mean": mean, "seg": float(seg), "span": span, "f0": f0, "f1": f1}
    a, b = max(0, best["f0"] - 3), min(len(q), best["f1"] + 4)   # a little context each side
    seg_frames = q[a:b]
    idx = np.linspace(0, len(seg_frames) - 1, FR)
    lo = np.floor(idx).astype(int); hi = np.minimum(lo + 1, len(seg_frames) - 1); w = (idx - lo)[:, None]
    frames = (1 - w) * seg_frames[lo] + w * seg_frames[hi]
    return best, frames.astype(np.float16)


def load_backgrounds(today_sessions, past, rng, n=300):
    """Real 1.5 s room/office chunks: half from past days, half from today."""
    bgs = []
    days = sorted({r["day"] for r in past})
    picks = [("past", d) for d in days] + [("today", s) for s in today_sessions]
    sess_cache = {}
    for i in range(n):
        kind, ref = picks[i % len(picks)]
        if kind == "past":
            d = os.path.join(REC, ref)
            ss = [s for s in os.listdir(d) if "-c" in s]
            sdir = os.path.join(d, ss[rng.integers(len(ss))])
        else:
            sdir = ref
        if sdir not in sess_cache:
            if len(sess_cache) > 40:
                sess_cache.pop(next(iter(sess_cache)))
            try:
                sess_cache[sdir] = kws_tool.decode_opus(os.path.join(sdir, "audio.opus"))
            except OSError:
                continue
        a = sess_cache[sdir]
        if len(a) < WIN * 2:
            continue
        s = rng.integers(0, len(a) - WIN)
        bgs.append((("today" if kind == "today" else "past"), a[s:s + WIN]))
    return bgs


def main(table, pos_dir, tts_dir, past_path, today_file, out):
    import soundfile as sf
    rng = np.random.default_rng(11)
    random.seed(11)
    sp = spotter.Spotter(spotter.Model(kws_tool.MODEL, threads=int(os.environ.get("KWS_THREADS", "3"))), table)
    kw = sp.keywords[0]
    past = list(map(json.loads, open(past_path)))
    today = [l.strip() for l in open(today_file) if l.strip()]
    bgs = load_backgrounds(today, past, rng)
    print(len(bgs), "backgrounds", flush=True)

    items = []   # (label, group, source, clip, skip-samples)
    pos = [json.loads(l) for l in open(os.path.join(pos_dir, "positives.jsonl"))]
    for p in pos:
        x, _ = sf.read(os.path.join(pos_dir, p["clip"]), dtype="float32")
        pre = p["session"].split("-")[0]
        skip = {n for n, _ in kw.samples if n.startswith(pre + "-")}
        for _ in range(REAL_POS_COPIES):
            items.append((1, "real:" + p["session"][:6], "real_pos", x, skip))
    for l in open(os.path.join(tts_dir, "tts.jsonl")):
        r = json.loads(l)
        x, _ = sf.read(os.path.join(tts_dir, r["file"]), dtype="float32")
        for _ in range(TTS_COPIES):
            items.append((1 if r["kind"] == "pos" else 0, "tts", "tts_" + r["kind"], x, ()))
    # past negatives: the hardest by the old score + random, grouped by day
    hard = sorted(past, key=lambda r: r["kw"]["dynames"]["score"])[:600]
    rest = random.sample(past, min(PAST_NEG, len(past)))
    picked = {(r["session"], r["id"]): r for r in hard + rest}
    by_sess = {}
    for r in picked.values():
        by_sess.setdefault((r["day"], r["session"]), []).append(r)
    for (day, s), rs in by_sess.items():
        sdir = os.path.join(REC, day, s)
        try:
            audio = kws_tool.decode_opus(os.path.join(sdir, "audio.opus"))
        except OSError:
            continue
        ends = {e["id"]: e for e in kws_tool.speech_ends(sdir)}
        for r in rs:
            e = ends.get(r["id"])
            if e:
                a, b = e["pos"]
                if b - a > WIN:                                    # a long sentence: a random 1.5 s of it
                    a = int(rng.integers(a, b - WIN)); b = a + WIN - SR // 10
                items.append((0, "day:" + day, "past_neg", audio[a:b], ()))
    # today negatives: random stretches away from every located saying
    near = {}
    for p in pos:
        near.setdefault(p["session"], []).append((p["start"] - 3 * SR, p["end"] + 3 * SR))
    per = max(1, TODAY_NEG // max(1, len(today)))
    for sdir in today:
        audio = kws_tool.decode_opus(os.path.join(sdir, "audio.opus"))
        bad = near.get(os.path.basename(sdir.rstrip("/")), [])
        got = tries = 0
        while got < per and tries < per * 20 and len(audio) > WIN * 2:
            tries += 1
            a = int(rng.integers(0, len(audio) - WIN))
            x = audio[a:a + WIN - SR // 10]
            if any(a < hi and a + WIN > lo for lo, hi in bad):
                continue
            if spotter.level_db(x) < -45:
                continue
            items.append((0, "today", "today_neg", x, ()))
            got += 1
    print({k: sum(1 for it in items if it[2] == k) for k in
           ("real_pos", "tts_pos", "tts_neg", "past_neg", "today_neg")}, flush=True)

    X = np.zeros((len(items), FR, 768), np.float16)
    meta = []
    t0 = time.time()
    for i, (label, group, source, x, skip) in enumerate(items):
        x = augment(x, rng)
        bg_kind, bg = bgs[int(rng.integers(len(bgs)))]
        snr = rng.uniform(0, 20) if source != "past_neg" and source != "today_neg" else rng.uniform(5, 30)
        w = place(x, bg, rng, snr)
        q = sp.model.feats(w)
        s1, frames = stage1(kw, q, skip)
        X[i] = frames
        meta.append({"label": label, "group": group, "source": source, "bg": bg_kind, "snr": round(snr, 1),
                     **{k: round(float(s1[k]), 4) for k in ("score", "mean", "seg", "span")}})
        if i % 500 == 0:
            print(f"{i}/{len(items)} {time.time() - t0:.0f}s", flush=True)
    np.save(out + ".X.npy", X)
    with open(out + ".meta.jsonl", "w") as f:
        for m in meta:
            f.write(json.dumps(m) + "\n")
    print("done", len(items), f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:7])
