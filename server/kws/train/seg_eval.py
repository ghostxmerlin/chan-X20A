"""Offline: does a per-segment DTW score separate the name from short grunts better than the whole-template mean?
(Fable, 2026-11-17: split each sample into K stretches along the path, score = the worst stretch; 嗯/Yeah have one
vowel and cannot match every stretch of デュ・ナ・メ・ス.)

Positives: train/pos (located sayings), each scored with samples from OTHER sessions only.
Negatives: the 10 past days' VAD segments that scored lowest before (hard) + a random sample of the rest.
usage: seg_eval.py <table> <pos dir> <past10.jsonl> [n_random=1500]
"""
import json
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import kws_tool  # noqa: E402
import spotter   # noqa: E402

SR = spotter.SR
REC = os.path.expanduser("~/soul-spike/recordings")


def path_costs(t, q):
    """Best subsequence path of t inside q -> per-template-frame cost along it, and the span ratio."""
    C, wp, mean = spotter.align(t, q)
    per = np.zeros(len(t))
    cnt = np.zeros(len(t))
    for i, j in wp:
        per[i] += C[i, j]
        cnt[i] += 1
    per /= np.maximum(cnt, 1)
    span = (wp[:, 1].max() - wp[:, 1].min() + 1) / len(t)
    return per, span, mean


def scores(samples, q, min_span=0.6):
    """-> dict of score kinds (min over samples; lower = more alike)."""
    best = {"mean": 9.0, "seg4max": 9.0, "seg5max": 9.0, "seg4top2": 9.0}
    for name, t in samples:
        per, span, mean = path_costs(t, q)
        pen = max(0.0, min_span - span)
        s4 = [c.mean() for c in np.array_split(per, 4)]
        s5 = [c.mean() for c in np.array_split(per, 5)]
        cand = {"mean": mean + pen, "seg4max": max(s4) + pen, "seg5max": max(s5) + pen,
                "seg4top2": float(np.mean(sorted(s4)[-2:])) + pen}
        for k, v in cand.items():
            best[k] = min(best[k], v)
    return best


def main(table, pos_dir, past, n_random=1500):
    import soundfile as sf
    sp = spotter.Spotter(spotter.Model(kws_tool.MODEL, threads=3), table)
    kw = sp.keywords[0]
    pos = [json.loads(l) for l in open(os.path.join(pos_dir, "positives.jsonl"))]
    rows = []
    for p in pos:
        x, _ = sf.read(os.path.join(pos_dir, p["clip"]), dtype="float32")
        pre = p["session"].split("-")[0]
        others = [(n, t) for n, t in kw.samples if not n.startswith(pre + "-")]
        rows.append(("pos", p["session"][:6] + " " + p["hms"], scores(others, sp.model.feats(x))))
    print(f"{len(rows)} positives scored", flush=True)
    neg = list(map(json.loads, open(past)))
    hard = sorted(neg, key=lambda r: r["kw"]["dynames"]["score"])[:300]
    random.seed(1)
    rest = random.sample(neg[:], min(n_random, len(neg)))
    picked = {(r["session"], r["id"]): r for r in hard + rest}
    by_sess = {}
    for (s, i), r in picked.items():
        by_sess.setdefault((r["day"], s), []).append(r)
    for (day, s), rs in by_sess.items():
        sdir = os.path.join(REC, day, s)
        audio = kws_tool.decode_opus(os.path.join(sdir, "audio.opus"))
        ends = {e["id"]: e for e in kws_tool.speech_ends(sdir)}
        for r in rs:
            e = ends.get(r["id"])
            if not e:
                continue
            a, b = e["pos"]
            rows.append(("neg", f"{day} {r['hms']} {r['dur']}s {r['text'][:12]!r}",
                         scores(kw.samples, sp.model.feats(audio[a:b]))))
    print(f"{sum(r[0] == 'neg' for r in rows)} negatives scored (300 hard + random)", flush=True)
    hours = sum(r["dur"] for r in neg) / 3600
    for k in ("mean", "seg4max", "seg5max", "seg4top2"):
        ps = sorted(r[2][k] for r in rows if r[0] == "pos")
        ns = sorted(r[2][k] for r in rows if r[0] == "neg")
        print(f"\n== {k}")
        for target in (0, 2, 5, 10):                 # negatives allowed under the threshold (of the hard+random set)
            thr = ns[target] if target < len(ns) else 9
            rec = sum(s < thr for s in ps)
            print(f"  allow {target:2d} negatives below -> threshold {thr:.3f}: recall {rec}/{len(ps)}")
        worst = sorted((r for r in rows if r[0] == "neg"), key=lambda r: r[2][k])[:5]
        print("  closest negatives:", "; ".join(f"{r[2][k]:.3f} {r[1]}" for r in worst))
        print("  positives:", " ".join(f"{s:.3f}" for s in ps))
    print(f"\n(the 10 days hold {len(neg)} segments, {hours:.1f} h of speech; the 300 hard ones are all the "
          f"segments that scored lowest before, so 'allow N' ~ N false alarms in {hours:.1f} h)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 1500)
