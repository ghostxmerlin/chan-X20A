"""Nightly retraining of the Docking auto mode (owner 2026-11-18): runs on the server (cron), never on the robot.
1. new heard sentences of Docking (realtime_turns kind speech, last DAYS days) -> reference labels from LABELER
   (aihubmix, batched; labels.json keeps every label ever made, so only new sentences cost money);
2. a logistic-regression head on [local embedding, rule hit] -> P(tools needed), class-balanced, numpy only;
3. a quick half / half check (caught / chit-chat flagged) before writing; a much worse head is not written;
4. head.json + labelled.json written atomically into ART_DIR (the ear reloads them); one run-log row
   call.intent.train (svc intent) with the labelling cost and the metrics.
usage: python train.py [--days 14] [--dry-run]"""
import argparse, json, os, random, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EAR = os.path.expanduser("~/soul-spike")
sys.path.insert(0, EAR)
import intent  # noqa: E402  (deployed next to ear_server.py)
import numpy as np  # noqa: E402

LABELER = "gpt-6.1-terra"
PRICE = (1.0, 5.0)                     # USD per M tokens, llm.PRICES (gpt-6.1-terra)
CLASSES = ("none（闲聊、附和、打招呼、不需要任何工具）、look（要看画面、问谁在、刚才的声音）、recall（问过去聊过/发生过的事、某人说过什么、记不记得）、"
           "world（要查价格天气汇率新闻或上网搜）、daily（提醒、日程、帮忙盯着）、body（转头点头摇头表情音量、问电量）、manner（要它说详细/简单、安静、"
           "别插话、扮演角色）、self（问它为什么那么做、能做什么、花费开关）、note（要它记住某事、改开关）")
KIND = ("none",) + intent.SKILL_IDS


def env_file(path):
    out = {}
    for line in open(path):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def label(batch, hub):
    body = {"model": LABELER, "temperature": 0, "reasoning_effort": "low", "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "这些是家里的桌面机器人听到的话。判断机器人回应每句话时需要哪一类工具。类别：" + CLASSES +
                          "。只输出 JSON：{\"labels\": [类名, ...]}，顺序和数量与输入一致。"},
                         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}]}
    r = urllib.request.Request(hub.get("BASE_URL", "https://aihubmix.com/v1").rstrip("/") + "/chat/completions",
                               data=json.dumps(body).encode(),
                               headers={"Authorization": f"Bearer {hub['API_KEY']}", "Content-Type": "application/json"})
    out = json.load(urllib.request.urlopen(r, timeout=90))
    u = out.get("usage") or {}
    cost = (u.get("prompt_tokens", 0) * PRICE[0] + u.get("completion_tokens", 0) * PRICE[1]) / 1e6
    got = json.loads(out["choices"][0]["message"]["content"])["labels"]
    return (got if len(got) == len(batch) else None), cost


def features(emb, texts):
    V = np.stack([emb(t) for t in texts])
    R = np.array([[1.0 if intent.rule(t) else 0.0] for t in texts])
    return np.concatenate([V, R], axis=1).astype(np.float32)


def fit(X, y, l2=1e-3, iters=800, lr=0.5):
    w, b = np.zeros(X.shape[1]), 0.0
    pos = max(1e-3, min(1 - 1e-3, y.mean()))
    cw = np.where(y > 0, 0.5 / pos, 0.5 / (1 - pos))
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = (p - y) * cw
        w -= lr * (X.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return w, b


def check(X, y, th, shuffles=4):
    tp = fn = fp = tn = 0
    for seed in range(shuffles):
        idx = list(range(len(y))); random.Random(seed).shuffle(idx)
        tr, te = idx[: len(idx) // 2], idx[len(idx) // 2:]
        w, b = fit(X[tr], y[tr])
        p = 1 / (1 + np.exp(-(X[te] @ w + b)))
        for pi, yi in zip(p, y[te]):
            if yi and pi >= th: tp += 1
            elif yi: fn += 1
            elif pi >= th: fp += 1
            else: tn += 1
    return tp / max(1, tp + fn), fp / max(1, fp + tn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    hub = env_file(os.path.join(EAR, "hello.env"))
    from pymongo import MongoClient
    db = MongoClient(hub["MONGO_URI"], serverSelectionTimeoutMS=5000)["hello"]
    import hello_log                                    # the ear's copy (server/common)
    hello_log.init("intent", db_uri=hub["MONGO_URI"], spool_dir=os.path.join(EAR, "log-spool"), lifecycle=False)
    os.makedirs(intent.ART_DIR, exist_ok=True)
    lab_path = os.path.join(intent.ART_DIR, "labels.json")
    labels = json.load(open(lab_path)) if os.path.exists(lab_path) else {}
    since = int(time.time() * 1000) - int(a.days * 86400_000)
    texts, seen = [], set()
    for d in db["realtime_turns"].find({"kind": "speech", "at": {"$gte": since}, "heard": {"$nin": [None, ""]}}, {"heard": 1}):
        t = str(d["heard"]).strip()
        if 2 <= len(t) <= 80 and t not in seen:
            seen.add(t); texts.append(t)
    todo = [t for t in texts if t not in labels]
    cost = 0.0
    for i in range(0, len(todo), 25):
        try:
            got, c = label(todo[i:i + 25], hub)
            cost += c
            if got:
                labels.update(zip(todo[i:i + 25], got))
        except Exception as e:                          # noqa: BLE001 - one batch lost, labelled next night
            print("label batch failed", type(e).__name__, flush=True)
    if not a.dry_run:
        json.dump(labels, open(lab_path + ".tmp", "w"), ensure_ascii=False)
        os.replace(lab_path + ".tmp", lab_path)
    data = [(t, l) for t, l in labels.items() if l in KIND]
    emb = intent.Embedder()
    X = features(emb, [t for t, _ in data])
    y = np.array([0.0 if l == "none" else 1.0 for _, l in data])
    caught, flagged = check(X, y, a.threshold)
    w, b = fit(X, y)
    version = time.strftime("%Y-%m-%d %H:%M")
    old = os.path.join(intent.ART_DIR, "head.json")
    prev = json.load(open(old)) if os.path.exists(old) else {}
    worse = prev.get("caught") is not None and caught < prev["caught"] - 0.15
    report = {"version": version, "sentences": len(data), "new_labels": len(todo), "positives": int(y.sum()),
              "caught": round(caught, 3), "flagged": round(flagged, 3), "label_cost_usd": round(cost, 4),
              "written": not (a.dry_run or worse)}
    if report["written"]:
        head = {"w": [float(x) for x in w], "b": float(b), "threshold": a.threshold, "version": version,
                "caught": report["caught"], "flagged": report["flagged"], "sentences": len(data), "model": "bge-small-zh-v1.5"}
        for name, obj in (("labelled.json", [[t, l] for t, l in data if l != "none"]), ("head.json", head)):
            p = os.path.join(intent.ART_DIR, name)
            json.dump(obj, open(p + ".tmp", "w"), ensure_ascii=False)
            os.replace(p + ".tmp", p)
    hello_log.emit("call.intent.train", ok=True, model=LABELER,
                   cost={"amount": round(cost, 6), "cur": "USD", "est": True, "basis": "train.py PRICE (llm.PRICES terra)"},
                   sum=f"自动档重新训练：{len(data)} 句，认出 {caught:.0%}，闲聊误判 {flagged:.0%}" + ("" if report["written"] else "（没写入）"),
                   extra=report)
    hello_log.get().close(timeout_s=10)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
