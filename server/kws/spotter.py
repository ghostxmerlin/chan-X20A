"""Keyword ear core: finds keywords in a speech segment by comparing its sound with recorded samples of each
keyword (query by example). No text and no API: a local speech model (chinese-hubert-base cut to 9 layers,
ONNX) turns audio into frame features, and subsequence DTW finds the best match of each sample inside the segment.

Keyword table (a folder, kept outside the repo because the samples are people's voices):
  <table>/<keyword>/keyword.json   {"label": "Dynames", "threshold": 0.35, "near": 0.05, "min_span": 0.6}
  <table>/<keyword>/*.wav          16 kHz mono samples of the keyword said on its own
Score: mean cosine distance per sample frame along the best path (lower = more alike), plus a penalty when the
whole keyword would be squeezed into a much shorter sound (the false alarms were short grunts like 嗯/可以).
Validated offline 2026-11-17 (spikes/kws): 8/9 of the owner's sayings at 0 false alarms in 1079 segments.
"""
import glob
import json
import os

import numpy as np

SR = 16000
DEFAULTS = {"threshold": 0.35, "near": 0.05, "min_span": 0.6}


class Model:
    """The frame feature model: float audio at 16 kHz -> [frames, 768] (20 ms per frame)."""

    def __init__(self, path, threads=2):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])

    def feats(self, x):
        x = np.asarray(x, dtype=np.float32)
        x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)       # what the model's feature extractor does
        return self.session.run(None, {"audio": x[None, :]})[0][0]


def trim(x):
    """Drop leading/trailing silence from a sample (not from the segments being searched)."""
    import librosa
    y, _ = librosa.effects.trim(np.asarray(x, dtype=np.float32), top_db=30, frame_length=400, hop_length=160)
    return y if len(y) > SR * 0.15 else x


def align(t, q):
    """Best alignment of sample frames t inside segment frames q -> (C, path [(i in t, j in q)], cost per t frame).
    librosa's subsequence DTW silently swaps the two when q is shorter than t (it then finds q inside t, and the
    cost / len(t) came out small: the short-grunt false alarms of 2026-11-17), so a short q is aligned whole."""
    import librosa
    tn = t / (np.linalg.norm(t, axis=1, keepdims=True) + 1e-9)
    qn = q / (np.linalg.norm(q, axis=1, keepdims=True) + 1e-9)
    C = 1.0 - tn @ qn.T
    if len(q) >= len(t):
        D, wp = librosa.sequence.dtw(C=C, subseq=True, backtrack=True)
        cost = float(D[-1, :].min() / len(t))
    else:
        D, wp = librosa.sequence.dtw(C=C, subseq=False, backtrack=True)
        cost = float(D[-1, -1] / len(t))
    return C, wp, cost


def sdtw(t, q, min_span, mode="mean"):
    """Cost of the best match of sample features t anywhere inside segment features q (lower = more alike).
    mode "mean": mean cost along the path. "seg4max" (Fable 2026-11-17): the path's cost per sample frame, split
    into 4 stretches, the worst stretch counts, so a sound must match every part of the word (嗯 / Yeah cannot):
    on the owner's 24 sayings vs 1800 past-day segments it separated 24/24 at 0 false alarms (mean: 23/24)."""
    if len(q) < 2:
        return 9.0
    C, wp, cost = align(t, q)
    if mode == "seg4max":
        per = np.zeros(len(t))
        cnt = np.zeros(len(t))
        np.add.at(per, wp[:, 0], C[wp[:, 0], wp[:, 1]])
        np.add.at(cnt, wp[:, 0], 1)
        per /= np.maximum(cnt, 1)
        cost = max(float(c.mean()) for c in np.array_split(per, 4))
    span = (wp[:, 1].max() - wp[:, 1].min() + 1) / len(t)
    if span < min_span:
        cost += min_span - span
    return float(cost)


def read_wav(path):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    if sr != SR:
        raise ValueError(f"{path}: {sr} Hz, samples must be 16 kHz")
    return x.mean(axis=1)


class Keyword:
    def __init__(self, name, conf, samples):
        self.name = name
        self.label = conf.get("label", name)
        self.threshold = float(conf.get("threshold", DEFAULTS["threshold"]))
        self.near = float(conf.get("near", DEFAULTS["near"]))
        self.min_span = float(conf.get("min_span", DEFAULTS["min_span"]))
        self.mode = conf.get("mode", "mean")
        self.samples = samples          # [(file name, features)]


class Spotter:
    def __init__(self, model, table_dir):
        self.model = model
        self.table_dir = table_dir
        self.keywords = []
        self.reload()

    def reload(self):
        kws = []
        for conf_path in sorted(glob.glob(os.path.join(self.table_dir, "*", "keyword.json"))):
            kdir = os.path.dirname(conf_path)
            with open(conf_path) as f:
                conf = json.load(f)
            samples = [(os.path.basename(w), self.model.feats(trim(read_wav(w))))
                       for w in sorted(glob.glob(os.path.join(kdir, "*.wav")))]
            if samples:
                kws.append(Keyword(os.path.basename(kdir), conf, samples))
        self.keywords = kws
        return kws

    def score_feats(self, q, skip=()):
        out = {}
        for kw in self.keywords:
            best, which = 9.0, None
            for name, t in kw.samples:
                if name in skip:
                    continue
                c = sdtw(t, q, kw.min_span, kw.mode)
                if c < best:
                    best, which = c, name
            out[kw.name] = {"label": kw.label, "score": round(best, 4), "sample": which,
                            "hit": best < kw.threshold, "near": kw.threshold <= best < kw.threshold + kw.near}
        return out

    def score(self, x, skip=()):
        """Segment audio -> {keyword: {"score", "sample", "hit", "near"}}. skip: sample file names to leave out
        (scoring a sample against itself, in tests)."""
        return self.score_feats(self.model.feats(x), skip)


WINDOW_S = 1.5      # each look covers the last 1.5 s: a name (0.4-0.8 s said) fits whole in at least one
HOP_S = float(os.environ.get("KWS_HOP_S", "0.25"))   # one look every 0.25 s (0.5 s missed a clear one, 16:57:03)
GATE_DB = 4.0       # a look only when its loudest 100 ms is this far above the floor (quiet name: about +4..+8)
FLOOR_S = 15        # the floor: 20th percentile of 100 ms levels over the last 15 s
HOLD_S = 1.0        # near-misses of one saying are merged: reported 1 s after the last near look
# A hit is reported at once (live 2026-11-17 17:39: holding hits for the best score cost 1.6-2.7 s). Looks of the
# same saying that come after it are dropped: a new hit counts only when its look does not overlap the reported one.


def level_db(x):
    return 20.0 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)


class Stream:
    """Keyword spotting on a continuous 16 kHz stream, without the ear's sentence cutting (VAD): the owner's quiet
    sayings that VAD skipped (2026-11-17 16:56) were found this way. feed() takes audio in order with its absolute
    sample position; it returns finished reports [{keyword, label, score, sample, hit, near, start, end}]
    (start/end = absolute samples of the best look). Silence (the robot's own sound arrives as zeros) never passes
    the gate. A gap in positions starts over."""

    def __init__(self, sp, skip=None):
        self.sp = sp
        self.skip = skip or (lambda start, end: ())   # offline tests: samples to leave out for a look
        self.win = int(WINDOW_S * SR)
        self.hop = int(HOP_S * SR)
        self.buf = np.zeros(0, np.float32)
        self.buf_start = 0                              # absolute sample of buf[0]
        self.next_end = None                            # absolute end of the next look
        self.levels = []                                # (absolute end, 100 ms level)
        self.open = {}                                  # keyword -> best near look so far of a saying in progress
        self.taken = {}                                 # keyword -> end of the last reported hit's look
        self.looks = self.gated = 0

    def floor(self):
        if len(self.levels) < 20:
            return None
        return float(np.percentile([v for _, v in self.levels], 20))

    def feed(self, x, pos):
        x = np.asarray(x, dtype=np.float32)
        end_of_buf = self.buf_start + len(self.buf)
        if self.next_end is None or pos != end_of_buf:
            self.buf, self.buf_start, self.open, self.taken = np.zeros(0, np.float32), pos, {}, {}
            self.next_end = pos + self.win
        self.buf = np.concatenate([self.buf, x])
        n100 = SR // 10
        done = (len(self.buf) - len(x)) // n100 * n100          # 100 ms levels of the new audio
        for i in range(done, len(self.buf) - n100 + 1, n100):
            a = self.buf_start + i
            if not self.levels or a + n100 > self.levels[-1][0]:
                lv = level_db(self.buf[i:i + n100])
                if lv > -90:                                    # exact zeros (robot sounding) are not the floor
                    self.levels.append((a + n100, lv))
        cut = self.buf_start + len(self.buf) - FLOOR_S * SR
        while self.levels and self.levels[0][0] < cut:
            self.levels.pop(0)
        out = []
        while self.next_end <= self.buf_start + len(self.buf):
            out += self._look(self.next_end - self.win, self.next_end)
            self.next_end += self.hop
        keep = self.next_end - self.win                          # oldest sample a future look needs
        if keep > self.buf_start:
            self.buf = self.buf[keep - self.buf_start:]
            self.buf_start = keep
        return out

    def _look(self, start, end):
        x = self.buf[start - self.buf_start:end - self.buf_start]
        out = self._close(end)
        f = self.floor()
        n100 = SR // 10
        loud = max(level_db(x[i:i + n100]) for i in range(0, len(x) - n100 + 1, n100 // 2))
        if f is None or loud < f + GATE_DB:
            self.gated += 1
            return out
        self.looks += 1
        res = self.sp.score_feats(self.sp.model.feats(x), self.skip(start, end))
        for name, r in res.items():
            if not (r["hit"] or r["near"]) or start < self.taken.get(name, -1):
                continue                                # nothing, or the saying already reported
            if r["hit"]:
                self.open.pop(name, None)               # a near look of this saying gives way to the hit
                self.taken[name] = end
                out.append({"keyword": name, **r, "start": start, "end": end})
                continue
            best = self.open.get(name)
            if best is None or r["score"] < best["score"]:
                self.open[name] = {"keyword": name, **r, "start": start, "end": end, "last": end}
            else:
                best["last"] = end
        return out

    def _close(self, now):
        out = []
        for name in list(self.open):
            if self.open[name]["start"] < self.taken.get(name, -1):
                del self.open[name]                     # a hit of the same saying came after it
            elif now - self.open[name]["last"] > HOLD_S * SR:
                r = self.open.pop(name)
                r.pop("last")
                out.append(r)
        return out

    def flush(self):
        out = [dict(r) for r in self.open.values()]
        for r in out:
            r.pop("last", None)
        self.open = {}
        return out
