"""Merge the review (out_*.json, by Claude per GUIDE.md) into labels_reviewed.json, and give the sentences that were
not reviewed (clear chit-chat: draft none / none, no rule, the old head < 0.6) a draft 'none' in labels.json.
usage: python merge_review.py <scratch dir with intent_rows.json + intent_review/> <out dir>"""
import json, os, sys
src, out = sys.argv[1], sys.argv[2]
rows = json.load(open(os.path.join(src, "intent_rows.json")))
rv = [r for r in rows if r["dock"] or (r["draft"] not in (None, "none")) or r["rule"] or r["p"] >= 0.6]
parts = {"a": [r for r in rv if r["dock"]][:650], "b": [r for r in rv if r["dock"]][650:], "c": [r for r in rv if not r["dock"]]}
KIND = {"none", "look", "recall", "world", "daily", "body", "manner", "self", "note"}
reviewed = {}
for k, items in parts.items():
    got = {o["i"]: o["label"] for o in json.load(open(os.path.join(src, "intent_review", f"out_{k}.json")))}
    assert len(got) == len(items), (k, len(got), len(items))
    for i, r in enumerate(items):
        if got.get(i) in KIND:
            reviewed[r["t"]] = got[i]
# a bare Unit ASR command word (turn left, back, up, a digit ...) is carried out by the robot itself and Docking never
# answers it (ORDER_WORDS, owner 2026-11-18): none, whatever the reviewer said
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server", "ear"))
try:
    import realtime_link
    for t in list(reviewed):
        if realtime_link.order_word(t):
            reviewed[t] = "none"
except Exception as e:                                  # noqa: BLE001
    print("order words not checked:", type(e).__name__)
# Claude's own corrections after spot-checking the reviewers (2026-11-18)
OVERRIDE = (("分钟识图先打开", "none"), ("机身的图像上传", "none"), ("视觉现在调的帧", "none"),   # the owner talking to Claude
            ("换成胖张的声音", "manner"), ("声音变成", "manner"), ("换个声", "manner"),                # voice = how it speaks
            ("我是谁？你认识吗", "look"), ("我是谁？", "look"), ("你认识我吗", "look"))                # faces, not memory
for t in list(reviewed):
    for frag, lab in OVERRIDE:
        if t.startswith(frag) or t == frag:
            reviewed[t] = lab
drafts = {r["t"]: (r["draft"] or "none") for r in rows if r["t"] not in reviewed}
json.dump(reviewed, open(os.path.join(out, "labels_reviewed.json"), "w"), ensure_ascii=False)
json.dump(drafts, open(os.path.join(out, "labels_drafts_rest.json"), "w"), ensure_ascii=False)
changed = sum(1 for r in rv if r["t"] in reviewed and (r["draft"] or "none") != reviewed[r["t"]])
print(f"reviewed {len(reviewed)} (changed from draft {changed}), rest as drafts {len(drafts)}")
import collections; print(collections.Counter(reviewed.values()).most_common())
