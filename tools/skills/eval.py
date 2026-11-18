"""Skill acceptance test (2026-11-18): each case in its own Qwen realtime text session with Docking's persona and
context, the auto mode (the local classifier preloads the group's skills) or skills mode (directory only); tool calls
run for real through mind (POST /tool/call, caller qwen: C tools become proposals). Checks: the right skill, a right
tool called (and answered ok), the answer's text. Cleans up what the run created. Run on the server:
  ~/soul-spike/.venv/bin/python ~/soul-spike/skills-tools/eval.py [auto|skills] [case filter]"""
import asyncio, json, os, re, sys, time, urllib.request
EAR = os.path.expanduser("~/soul-spike")
sys.path.insert(0, EAR)
import realtime_link as rl, intent  # noqa: E402
import websockets  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "auto"
ONLY = sys.argv[2] if len(sys.argv) > 2 else None
HERE = os.path.dirname(os.path.abspath(__file__))
env = {}
for line in open(os.path.join(EAR, "hello.env")):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1); env[k] = v.strip().strip('"')
TOKEN = open(os.path.join(EAR, "token")).read().strip()


def mind(method, path, body=None, timeout=40):
    req = urllib.request.Request("http://127.0.0.1:52740" + path, method=method,
                                 data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


tools = rl.realtime_tools(mind("GET", "/tools")["tools"], rl.SKILL_TOOL_SKIP)
ALL = {t["name"]: t for t in tools}
cfg = rl.load_skills(os.path.join(EAR, "skills.json"))
SK = {s["id"]: s for s in cfg["skills"] if "*" in s["tools"] or any(n in ALL for n in s["tools"])}
OPEN = rl.open_skill_spec(list(SK.values()))
ctx = mind("GET", "/realtime/context")


def instr_for(opened):
    """As the hub: the open skills' full text in the instructions (not in the history)."""
    block = "\n\n".join(SK[k].get("guide") or "" for k in opened if k in SK) or None
    return rl.instructions(ctx.get("text"), skills=block)
auto = intent.Auto(intent.Embedder()) if MODE == "auto" else None
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-audio-3.1-realtime-flash"


def specs_for(opened):
    names = []
    for k in opened:
        want = list(ALL) if "*" in SK[k]["tools"] else SK[k]["tools"]
        names += [n for n in want if n in ALL and n not in names]
    return [OPEN] + [ALL[n] for n in names]


async def run(case):
    opened, preloaded = [], []
    if auto is not None:
        d = auto.decide(case["say"])
        preloaded = [sid for g in d["skills"] for sid, s in SK.items() if s["group"] == g]
        opened = list(preloaded)
    calls, text, tokens, t0 = [], "", 0, time.monotonic()
    async with websockets.connect(URL, additional_headers={"Authorization": f"Bearer {env['DASHSCOPE_API_KEY']}"},
                                  max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "modalities": ["text"], "instructions": instr_for(opened), "turn_detection": None,
            "tools": specs_for(opened), "tool_choice": "auto"}}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": case["say"]}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        for _ in range(8):
            out, fcs = [], []
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 60))
                t = m.get("type")
                if t == "response.text.delta":
                    out.append(m.get("delta") or "")
                if t == "response.function_call_arguments.done":
                    fcs.append(m)
                if t == "error":
                    return {"error": str(m.get("error"))[:120]}
                if t == "response.done":
                    tokens += ((m.get("response") or {}).get("usage") or {}).get("input_tokens") or 0
                    break
            text += "".join(out)
            if not fcs:
                break
            for fc in fcs:
                name = fc["name"]
                try:
                    args = json.loads(fc.get("arguments") or "{}")
                except ValueError:
                    args = {}
                n_calls = len([c for c in calls if c[0] != "open_skill"])
                if name != "open_skill" and n_calls >= rl.SKILL_DEPTH:      # as the hub: enough, answer now
                    res = {"ok": False, "error": {"code": "refused", "message": rl.TOOL_ENOUGH}}
                    calls.append((name, "refused: enough", False))
                elif name == "open_skill":
                    sid = args.get("skill")
                    if sid in SK and sid not in opened:
                        opened.append(sid)
                    res = {"ok": sid in SK, "data": {"skill": SK.get(sid, {}).get("title"), "tools": SK.get(sid, {}).get("tools"),
                                                     "next": "做法已经放进说明的【此刻打开的技能】里，照着做，直接调用这些工具来回答"}}
                    await ws.send(json.dumps({"type": "session.update", "session": {"tools": specs_for(opened),
                                                                                  "instructions": instr_for(opened)}}))
                    calls.append(("open_skill", sid, True))
                else:
                    env_ = mind("POST", "/tool/call", {"name": name, "args": args, "caller": "qwen", "trace": "skilleval"}, 60)
                    res = {"ok": env_.get("ok"), "data": env_.get("data"), "error": env_.get("error")}
                    calls.append((name, json.dumps(args, ensure_ascii=False)[:60], bool(env_.get("ok"))))
                await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": fc["call_id"],
                    "output": json.dumps(res, ensure_ascii=False)[:3000]}}))
            await ws.send(json.dumps({"type": "response.create"}))
    return {"opened": opened, "preloaded": preloaded, "calls": calls, "text": text.strip(), "tokens": tokens,
            "s": round(time.monotonic() - t0, 1)}


def judge(case, r):
    if "error" in r:
        return False, "error " + r["error"]
    used = [c[0] for c in r["calls"] if c[0] != "open_skill"]
    if case.get("none"):
        bad = [s for s in r["opened"] if s not in r["preloaded"]] + used
        if "[事件]" in r["text"] or "【" in r["text"]:
            bad.append("念出了标记")
        return (not bad), ("" if not bad else f"闲聊却用了 {bad}")
    why = []
    if not set(case["skill"]) & set(r["opened"]):
        why.append(f"没开对技能（开了 {r['opened'] or '无'}）")
    hit = [c for c in r["calls"] if c[0] in case["tools"]]
    if not hit:
        why.append(f"没调对工具（调了 {used or '无'}）")
    elif not any(c[2] for c in hit):
        why.append(f"工具出错 {hit}")
    if not r["text"]:
        why.append("没有回答")
    elif case.get("text") and not re.search(case["text"], r["text"]):
        why.append("回答缺要点")
    if "[事件]" in r["text"] or "【" in r["text"]:
        why.append("念出了标记")
    return not why, "；".join(why)


async def main():
    cases = json.load(open(os.path.join(HERE, "cases.json")))["cases"]
    if ONLY:
        cases = [c for c in cases if ONLY in c["say"] or ONLY in ",".join(c.get("skill", []))]
    t_start = int(time.time() * 1000)
    results = []
    for c in cases:
        r = None
        for attempt in range(3):                        # a rate limit ("Allocated quota exceeded"): wait, try again
            try:
                r = await run(c)
            except Exception as e:                      # noqa: BLE001
                r = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
            if "quota" not in str(r.get("error", "")).lower():
                break
            await asyncio.sleep(20 * (attempt + 1))
        ok, why = judge(c, r)
        results.append((c, r, ok, why))
        print(("PASS " if ok else "FAIL ") + c["say"][:22].ljust(22), "| skills", r.get("opened"), "| calls",
              [f"{n}{'' if okc else '!'}" for n, _, okc in r.get("calls", [])], "|", why, "|", (r.get("text") or "")[:60],
              f"| {r.get('tokens')}t {r.get('s')}s", flush=True)
        await asyncio.sleep(3)                          # the robot's rate limit (2 per 10 s per verb) and the model's
    n_ok = sum(1 for *_, ok, _ in results if ok)
    print(f"\n{MODE}: {n_ok}/{len(results)} passed")
    cleanup(t_start)
    json.dump([{"say": c["say"], "ok": ok, "why": why, **r} for c, r, ok, why in results],
              open(os.path.join(HERE, f"result-{MODE}.json"), "w"), ensure_ascii=False, indent=1)


def cleanup(since):
    """Undo what the run left: reminders / watches made, proposals by qwen, quiet / role / length, the head front."""
    from pymongo import MongoClient
    db = MongoClient(env["MONGO_URI"], serverSelectionTimeoutMS=5000)["hello"]
    r = db["reminders"].update_many({"at": {"$gte": since}, "state": {"$in": ["pending", "held"]}}, {"$set": {"state": "cancelled"}})
    w = db["watches"].update_many({"at": {"$gte": since}, "state": "active"}, {"$set": {"state": "stopped"}})
    n = 0
    for p in db["proposals"].find({"at": {"$gte": since}, "state": "pending", "by": "qwen"}):
        mind("POST", "/proposal/decide", {"id": p["_id"], "approve": False}); n += 1
    for name, args in (("set_quiet", {"level": "off"}), ("set_role", {}), ("set_length", {"mode": "normal"}),
                       ("face_front", {}), ("tracking", {"on": True})):
        try:
            mind("POST", "/tool/call", {"name": name, "args": args, "caller": "skilleval-cleanup"})
        except Exception:                               # noqa: BLE001
            pass
    print(f"cleanup: {r.modified_count} reminders, {w.modified_count} watches, {n} proposals; quiet / role / length reset")


asyncio.run(main())
