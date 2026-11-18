"""Real sentences as examples (half examples, half test, 4 shuffles): rules first, then k nearest labelled sentences
(hand-written examples + the example half). Prints, per setting, the share of tool-needing sentences caught and of
chit-chat that would get a skill preloaded. usage: python knn_eval.py <dir> <labels.json>"""
import collections, json, os, random, sys, time
sys.path.insert(0, sys.argv[1])
import intent
import numpy as np
labels = json.load(open(sys.argv[2]))
KIND = ("none", "look", "recall", "world", "daily", "body", "manner", "self", "note")
data = [(t, l) for t, l in labels.items() if l in KIND]
emb = intent.Embedder()
t0 = time.monotonic()
V = np.stack([emb(t) for t, _ in data]); L = [l for _, l in data]
hand = [(s, k) for k, ss in intent.load_examples().items() for s in ss]
HV = np.stack([emb(s) for s, _ in hand]); HL = [k for _, k in hand]
print(f"{len(data)} sentences embedded in {time.monotonic() - t0:.1f} s")
need = lambda c: c not in (None, "none")
def run(k, min_vote, use_rules=True, shuffles=4):
    tp = fn = fp = tn = 0
    for seed in range(shuffles):
        idx = list(range(len(data))); random.Random(seed).shuffle(idx)
        ex, te = idx[: len(idx) // 2], idx[len(idx) // 2:]
        EV = np.concatenate([HV, V[ex]]); EL = HL + [L[i] for i in ex]
        for i in te:
            p = intent.rule(data[i][0]) if use_rules else None
            if p is None:
                sims = EV @ V[i]; top = np.argsort(-sims)[:k]
                votes = collections.Counter()
                for j in top:
                    votes[EL[j]] += float(sims[j])
                best, w = votes.most_common(1)[0]
                p = best if best != "none" and w >= min_vote * sum(votes.values()) else None
            r = L[i]
            if need(r) and need(p): tp += 1
            elif need(r): fn += 1
            elif need(p): fp += 1
            else: tn += 1
    return tp / max(1, tp + fn), fp / max(1, fp + tn)
for use_rules in (True, False):
    for k in (1, 3, 5, 9):
        for mv in (0.34, 0.5):
            rec, fpr = run(k, mv, use_rules)
            print(f"rules={use_rules!s:5s} k={k} vote>={mv}: tools needed caught {rec:.0%}, chit-chat preloaded {fpr:.0%}")
