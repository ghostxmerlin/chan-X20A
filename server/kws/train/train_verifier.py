"""Stage-2 verifier for the keyword ear: a small conv net on the DTW-matched stretch (40 frames x 768 of the frame
model) plus the stage-1 numbers (score, mean, seg, span) -> P(keyword).

Honest test (Fable 2026-11-17): real positives leave-one-SESSION-out (all augmented copies of a saying stay on one
side); negatives split by DAY: 20261115 = validation (pick the threshold), 20261116 = test (reported once).
TTS clips and today's non-keyword audio are training only.

usage: train_verifier.py <set prefix> [--final <out.onnx>]
"""
import argparse
import json
import os

import numpy as np
import torch
import torch.nn as nn

VAL_DAY, TEST_DAY = "day:20261115", "day:20261116"
STAGE1 = 0.45            # stage 1 passes a look on to the verifier below this score
SCALARS = ("score", "mean", "seg", "span")


class Head(nn.Module):
    def __init__(self, d=768, h=64, s=len(SCALARS)):
        super().__init__()
        self.inp = nn.Sequential(nn.Dropout(0.2), nn.Conv1d(d, h, 3, padding=1), nn.ReLU(),
                                 nn.Conv1d(h, h, 3, padding=2, dilation=2), nn.ReLU(),
                                 nn.Conv1d(h, h, 3, padding=4, dilation=4), nn.ReLU())
        self.out = nn.Sequential(nn.Linear(2 * h + s, 32), nn.ReLU(), nn.Dropout(0.2), nn.Linear(32, 1))

    def forward(self, x, s):                       # x: [B, 40, 768], s: [B, 4]
        z = self.inp(x.transpose(1, 2))
        return self.out(torch.cat([z.mean(2), z.amax(2), s], 1)).squeeze(1)


def load(prefix):
    X = np.load(prefix + ".X.npy", mmap_mode="r")
    meta = [json.loads(l) for l in open(prefix + ".meta.jsonl")]
    S = np.array([[m[k] for k in SCALARS] for m in meta], np.float32)
    y = np.array([m["label"] for m in meta], np.float32)
    g = np.array([m["group"] for m in meta])
    return X, S, y, g, meta


def train(X, S, y, idx, epochs=25, seed=0):
    torch.manual_seed(seed)
    np.random.seed(seed)
    m = Head()
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3, weight_decay=1e-2)
    pos_w = torch.tensor((y[idx] == 0).sum() / max(1, (y[idx] == 1).sum()), dtype=torch.float32)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pos_w)
    for ep in range(epochs):
        m.train()
        perm = np.random.permutation(idx)
        for b in range(0, len(perm), 128):
            j = np.sort(perm[b:b + 128])
            xb = torch.from_numpy(np.asarray(X[j], np.float32))
            loss = lossf(m(xb, torch.from_numpy(S[j])), torch.from_numpy(y[j]))
            opt.zero_grad(); loss.backward(); opt.step()
    return m


def predict(m, X, S, idx):
    m.eval()
    out = []
    with torch.no_grad():
        for b in range(0, len(idx), 512):
            j = idx[b:b + 512]
            out.append(torch.sigmoid(m(torch.from_numpy(np.asarray(X[j], np.float32)), torch.from_numpy(S[j]))).numpy())
    return np.concatenate(out) if out else np.zeros(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix")
    ap.add_argument("--final")
    ap.add_argument("--epochs", type=int, default=25)
    a = ap.parse_args()
    torch.set_num_threads(int(os.environ.get("TORCH_THREADS", "3")))
    X, S, y, g, meta = load(a.prefix)
    passed = S[:, 0] < STAGE1
    print(f"{len(y)} examples, stage 1 (< {STAGE1}) passes: "
          f"pos {passed[y == 1].sum()}/{(y == 1).sum()}, neg {passed[y == 0].sum()}/{(y == 0).sum()}")
    folds = sorted({x for x in g if x.startswith("real:")})
    neg_train = np.where((y == 0) & (g != VAL_DAY) & (g != TEST_DAY))[0]
    val = np.where((g == VAL_DAY) & passed)[0]
    test = np.where((g == TEST_DAY) & passed)[0]
    print(f"negatives: train {len(neg_train)}, val day {len(val)} (after stage 1), test day {len(test)}")
    allp_test, alln_test = [], []
    for f in folds:
        tr = np.concatenate([np.where((y == 1) & (g != f))[0], neg_train])
        tr = tr[passed[tr] | (y[tr] == 0)]
        m = train(X, S, y, tr, a.epochs)
        held = np.where((g == f))[0]
        p_pos = predict(m, X, S, held[passed[held]])
        p_val = predict(m, X, S, val)
        p_test = predict(m, X, S, test)
        thr = max(0.5, float(np.sort(p_val)[-1]) + 1e-6) if len(p_val) else 0.5   # 0 false alarms on the val day
        rec = (p_pos >= thr).sum()
        fa = int((p_test >= thr).sum())
        n_copies = len(held)
        print(f"{f}: held-out copies {n_copies}, stage-1 pass {passed[held].sum()}, "
              f"verifier thr {thr:.3f} -> recall {rec}/{n_copies} ({rec / max(1, n_copies):.0%}), "
              f"test-day false alarms {fa}")
        allp_test.append((rec, n_copies)); alln_test.append(fa)
    tot = sum(r for r, _ in allp_test); n = sum(c for _, c in allp_test)
    print(f"\nall folds: recall {tot}/{n} ({tot / max(1, n):.0%}) of held-out augmented sayings, "
          f"test-day false alarms per fold {alln_test}")
    old = [(S[np.where(g == f)[0], 0] < 0.35).mean() for f in folds]
    print("for comparison, stage-1 alone (< 0.35) on the same held-out copies:", " ".join(f"{v:.0%}" for v in old))
    if a.final:
        tr = np.concatenate([np.where(y == 1)[0], np.where((y == 0) & (g != TEST_DAY))[0]])
        m = train(X, S, y, tr, a.epochs)
        torch.onnx.export(m, (torch.zeros(1, 40, 768), torch.zeros(1, len(SCALARS))), a.final,
                          input_names=["frames", "scalars"], output_names=["logit"],
                          dynamic_axes={"frames": {0: "b"}, "scalars": {0: "b"}}, opset_version=17, dynamo=False)
        p_test = predict(m, X, S, test)
        print(f"final model -> {a.final}; test-day scores above 0.5: {(p_test >= 0.5).sum()}/{len(test)}")


if __name__ == "__main__":
    main()
