"""A tiny logistic-regression head on the local embeddings (numpy only): 'tools needed' yes / no, then the skill by
nearest labelled sentence. Half / half, 4 shuffles; threshold sweep. usage: python lr_eval.py <dir> <labels.json>"""
import json, random, sys
sys.path.insert(0, sys.argv[1])
import intent
import numpy as np
labels = json.load(open(sys.argv[2]))
KIND = ("none", "look", "recall", "world", "daily", "body", "manner", "self", "note")
data = [(t, l) for t, l in labels.items() if l in KIND]
emb = intent.Embedder()
V = np.stack([emb(t) for t, _ in data]); Y = np.array([0.0 if l == "none" else 1.0 for _, l in data])
R = np.array([0.0 if intent.rule(t) is None else 1.0 for t, _ in data])
X = np.concatenate([V, R[:, None]], axis=1)
def train(X, y, l2=1e-3, iters=600, lr=0.5):
    w = np.zeros(X.shape[1]); b = 0.0
    pos = y.mean(); cw = np.where(y > 0, 0.5 / pos, 0.5 / (1 - pos))      # balanced classes
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = (p - y) * cw
        w -= lr * (X.T @ g / len(y) + l2 * w); b -= lr * g.mean()
    return w, b
res = {th: [0, 0, 0, 0] for th in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)}
for seed in range(4):
    idx = list(range(len(data))); random.Random(seed).shuffle(idx)
    tr, te = idx[: len(idx) // 2], idx[len(idx) // 2:]
    w, b = train(X[tr], Y[tr])
    p = 1 / (1 + np.exp(-(X[te] @ w + b)))
    for th, c in res.items():
        for pi, yi in zip(p, Y[te]):
            c[0 if yi and pi >= th else 1 if yi else 2 if pi >= th else 3] += 1
for th, (tp, fn, fp, tn) in res.items():
    print(f"threshold {th}: tools needed caught {tp / (tp + fn):.0%}, chit-chat flagged {fp / (fp + tn):.0%}")
