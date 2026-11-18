"""Evaluate server/ear/intent.py on real Docking sentences (run on the server).
1. distinct heard sentences of realtime_turns (kind speech) of the last DAYS days;
2. reference labels from qwen3.8-flash (offline, batched, cached in labels.json next to this script);
3. rules + local embedding classifier -> accuracy, the 'tools needed or not' split, a confusion table, ms/sentence.
usage: python eval.py <dir with intent.py and skill_examples.json> [days]"""
import collections, json, os, sys, time, urllib.request
sys.path.insert(0, sys.argv[1])
import intent
DAYS = float(sys.argv[2]) if len(sys.argv) > 2 else 4
LABELER = os.environ.get("LABELER", "gpt-6.1-terra")      # a stronger model than the classifier's own fallback
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"labels-{LABELER}.json")
env = {}
for l in open(os.path.expanduser("~/soul-spike/hello.env")):
    if "=" in l and not l.startswith("#"):
        k, v = l.strip().split("=", 1); env[k] = v.strip().strip('"')
from pymongo import MongoClient
db = MongoClient(env["MONGO_URI"], serverSelectionTimeoutMS=5000)["hello"]
since = int(time.time() * 1000) - int(DAYS * 86400_000)
seen, texts, answered = set(), [], set()
for d in db["realtime_turns"].find({"kind": "speech", "at": {"$gte": since}, "heard": {"$nin": [None, ""]}}, {"heard": 1, "state": 1}).sort("at", 1):
    t = str(d["heard"]).strip()
    if d.get("state") == "done":
        answered.add(t)
    if 2 <= len(t) <= 80 and t not in seen:
        seen.add(t); texts.append(t)
CLASSES = "none（闲聊、附和、打招呼、不需要任何工具）、look（要看画面、问谁在、刚才的声音）、recall（问过去聊过/发生过的事、某人说过什么、记不记得）、world（要查价格天气汇率新闻或上网搜）、daily（提醒、日程、帮忙盯着）、body（转头点头摇头表情音量、问电量）、manner（要它说详细/简单、安静、别插话、扮演角色）、self（问它为什么那么做、能做什么、花费开关）、note（要它记住某事、改开关）"
labels = json.load(open(OUT)) if os.path.exists(OUT) else {}
todo = [t for t in texts if t not in labels]
hub = {}
for l in open(os.path.expanduser("~/soul-spike/hello.env")):
    if "=" in l and not l.startswith("#"):
        k, v = l.strip().split("=", 1); hub[k] = v.strip().strip('"')
for i in range(0, len(todo), 25):
    batch = todo[i:i + 25]
    body = {"model": LABELER, "temperature": 0, "reasoning_effort": "low", "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "这些是家里的桌面机器人听到的话。判断机器人回应每句话时需要哪一类工具。类别：" + CLASSES +
                          "。只输出 JSON：{\"labels\": [类名, ...]}，顺序和数量与输入一致。"},
                         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}]}
    r = urllib.request.Request(hub.get("BASE_URL", "https://aihubmix.com/v1").rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                               headers={"Authorization": f"Bearer {hub['API_KEY']}", "Content-Type": "application/json"})
    try:
        out = json.loads(json.load(urllib.request.urlopen(r, timeout=60))["choices"][0]["message"]["content"])["labels"]
        if len(out) == len(batch):
            labels.update(zip(batch, out))
    except Exception as e:
        print("label batch failed", type(e).__name__, str(e)[:80])
json.dump(labels, open(OUT, "w"), ensure_ascii=False)
data = [(t, labels[t]) for t in texts if t in labels and labels[t] in ("none", "look", "recall", "world", "daily", "body", "manner", "self", "note")]
if os.environ.get("ANSWERED") == "1":
    data = [(t, r) for t, r in data if t in answered]
emb = intent.Embedder()
clf = intent.Classifier(emb, min_sim=float(os.environ.get("MIN_SIM", intent.MIN_SIM)), margin=float(os.environ.get("MARGIN", intent.MARGIN)))
t0 = time.monotonic()
pred = [(t, ref, *clf.classify(t)) for t, ref in data]
ms = (time.monotonic() - t0) * 1000 / max(1, len(pred))
need = lambda c: c not in (None, "none")
ok = sum(1 for t, ref, p, s, via in pred if (p or "none") == ref)
tp = sum(1 for t, ref, p, s, via in pred if need(ref) and need(p))
fp = sum(1 for t, ref, p, s, via in pred if not need(ref) and need(p))
fn = sum(1 for t, ref, p, s, via in pred if need(ref) and not need(p))
print(f"sentences {len(pred)} (labelled {len(labels)}), ms/sentence {ms:.1f}")
print("reference classes:", dict(collections.Counter(ref for _, ref in data)))
print(f"exact class agreement {ok}/{len(pred)} = {ok / max(1, len(pred)):.0%}")
print(f"tools needed: caught {tp}/{tp + fn} (recall {tp / max(1, tp + fn):.0%}); chit-chat given tools {fp}/{len(pred) - tp - fn} ")
print("by layer:", dict(collections.Counter(via for *_, via in pred)))
conf = collections.Counter((ref, p or "none") for t, ref, p, s, via in pred if (p or "none") != ref)
print("most common disagreements (reference -> predicted):", conf.most_common(10))
if os.environ.get("SHOW") == "1":
    for t, ref, p, s, via in pred:
        if (p or "none") != ref and need(ref) != need(p):
            print(f"  [{ref} -> {p or 'none'} via {via} {s}] {t[:40]}")
