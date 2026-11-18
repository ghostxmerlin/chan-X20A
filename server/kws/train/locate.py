"""Cut the owner's sayings of a keyword into exact clips (positives for training): around each known time, run the
keyword's samples over the audio with subsequence DTW and keep the matched stretch (the DTW path), not a fixed
window and not an energy trim (quiet sayings sit a few dB over the room, so energy cannot find their edges).

usage: locate.py <table> <keyword> <out dir> <list.txt>
list.txt: one "<session dir> HH:MM:SS" (an ear sentence time, ±1-2 s) or "<session dir> @<sample>" (a stream
report's look start) per line
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import kws_tool  # noqa: E402
import spotter   # noqa: E402

SR = spotter.SR
FRAME = 320                    # one model frame = 20 ms
PAD = int(0.08 * SR)


def best_match(kw, q):
    """-> (cost, first frame, last frame, sample) of the best sample's best match inside q."""
    best = (9.0, 0, 0, None)
    for name, t in kw.samples:
        _, wp, cost = spotter.align(t, q)
        span = (wp[:, 1].max() - wp[:, 1].min() + 1) / len(t)
        if span < kw.min_span:
            cost += kw.min_span - span
        if cost < best[0]:
            best = (cost, int(wp[:, 1].min()), int(wp[:, 1].max()), name)
    return best


def main(table, keyword, out, listing):
    sp = spotter.Spotter(spotter.Model(kws_tool.MODEL, threads=2), table)
    kw = next(k for k in sp.keywords if k.name == keyword)
    os.makedirs(out, exist_ok=True)
    import soundfile as sf
    cache, found = {}, []
    for line in open(listing):
        if not line.strip() or line.startswith("#"):
            continue
        sdir, hms = line.split()
        if sdir not in cache:
            ticks = [json.loads(l) for l in open(os.path.join(sdir, "ticks.jsonl")) if l.startswith("{")]
            rx = np.array([t["rx"] for t in ticks], float)
            tt = np.array([t["t"] for t in ticks], float)
            cache[sdir] = (kws_tool.decode_opus(os.path.join(sdir, "audio.opus")), rx, tt)
        audio, rx, tt = cache[sdir]
        if hms.startswith("@"):                     # a stream report's look start (absolute sample): exact
            c = int(hms[1:])
            a, b = max(0, c - int(0.25 * SR)), min(len(audio), c + int(1.75 * SR))
            hms = time.strftime("%H:%M:%S", time.localtime(np.interp(c, rx, tt) / 1000))
        else:
            day = os.path.basename(os.path.dirname(sdir.rstrip("/")))
            t_ms = time.mktime(time.strptime(f"{day} {hms}", "%Y%m%d %H:%M:%S")) * 1000
            c = int(np.interp(t_ms, tt, rx))
            a, b = max(0, c - SR), min(len(audio), c + int(2.5 * SR))
        q = sp.model.feats(audio[a:b])
        cost, f0, f1, sample = best_match(kw, q)
        s0 = max(0, a + f0 * FRAME - PAD)
        s1 = min(len(audio), a + (f1 + 1) * FRAME + PAD)
        found.append({"session": os.path.basename(sdir.rstrip("/")), "hms": hms, "start": s0, "end": s1,
                      "dur": round((s1 - s0) / SR, 2), "cost": round(cost, 4), "sample": sample, "_audio": audio})
    found.sort(key=lambda r: (r["session"], r["start"]))
    kept = []
    for r in found:                                   # two listed times that found the same saying: keep one
        if kept and kept[-1]["session"] == r["session"] and r["start"] < kept[-1]["end"]:
            if r["cost"] < kept[-1]["cost"]:
                kept[-1] = r
            continue
        kept.append(r)
    with open(os.path.join(out, "positives.jsonl"), "w") as f:
        for i, r in enumerate(kept):
            audio = r.pop("_audio")
            r["clip"] = f"{r['session'].split('-')[0]}-{r['start']}.wav"
            sf.write(os.path.join(out, r["clip"]), audio[r["start"]:r["end"]], SR)
            f.write(json.dumps(r) + "\n")
            print(f"{r['session'][:6]} {r['hms']} {r['dur']:.2f}s cost {r['cost']:.3f} ({r['sample']})")
    print(f"{len(kept)} positives from {len(found)} listed times")


if __name__ == "__main__":
    main(*sys.argv[1:5])
