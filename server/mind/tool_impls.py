"""mind's tool implementations (B1, 2026-11-18; framework: tools.py, rules: docs/hello-tools.md).

B1 wires the three tools that already had an interface: switches_status, mode_status, cost_today. Each returns only
what a model needs, in short keys with Chinese titles, and never a key, token or internal address.
"""
import time
from datetime import datetime, timedelta, timezone

import re

from . import mode_table, recall, segments, selfvoice, settings as settings_mod, tool_time
from .library import Library
from .store import LANE
from .tools import ToolError

BJ = timezone(timedelta(hours=8))
SWITCH_TITLES = {"tingwu": "听悟（第二路转写）", "eye": "识别（看画面）", "notes": "纪要", "realtime": "Docking（实时对话）",
                 "mind": "情景"}
MOUTH_TITLES = {"off": "不出声", "clips": "只放录好的台词", "tts": "合成说话"}
PAUSE_WHY = {"away": "没人在，暂停花钱的功能", "budget": "今天的钱用到上限了"}
DIGITS = {"idle": "4", "crowd": "5", "companion": "6", "subtitle": "7（中译英）/ 8（英译中）"}   # firmware mode_rules.h


SCENE_FRESH_MS = 5 * 60_000
LIVE_SCENE_MS = 3 * 60_000
SEG_CLASS = {"present": "主人在", "absent": "主人不在", "unknown": "不确定有没有人", "off": "情景关着", "gap": "没有数据"}


def _people(s, names):
    """M12 face names -> how hello calls them (owner's face names -> 主人's display; known people -> their display)."""
    owner = s.get("owner") or {}
    table = {n: owner.get("display") or "主人" for n in owner.get("face_names") or []}
    for p in s.get("people") or []:
        for n in p.get("face_names") or []:
            table[n] = p.get("display") or n
    out = []
    for n in names:
        who = table.get(n, n)
        if who not in out:
            out.append(who)
    return out


VERDICT_ZH = {
    "play": "说出来了", "muted_quiet": "千问只回了「嗯」，当成不是跟它说，没播", "muted_unclear": "是一句「没听清」的反问，没播",
    "muted_repeat": "和刚说过的一模一样，没播", "muted_blind": "没有新照片却说看不清，没播",
    "muted_wait_look": "先等照片看清了再答", "muted_wait_search": "先等联网搜索的结果再答", "muted_order": "那句是给机器人的口令，不回答",
    "ambient": "千问判断不是跟它说，没有回答", "refused_busy": "它想主动说时有人在说话，被千问拒了",
    "search_gated": "没去搜：没等到千问真的回答", "burst_gated": "没开视觉爆发：没等到千问真的回答",
    "called_answer": "被叫了，照样回答", "order": "是给机器人的直接口令，不回答"}
KIND_ZH_TURN = {"greet": "看到主人，主动打招呼", "greet_short": "看到主人，主动打招呼", "tap": "有人点了它，打招呼",
                "rescue": "补救：重新正经回答", "look_answer": "看了照片后回答", "search_answer": "联网查到后回答",
                "called": "被叫了，照样回答"}
WHY_KINDS = ("heard.asr", "heard.called", "gate.realtime", "gate.rescue", "call.realtime.turn", "trigger.task",
             "trigger.burst", "call.search", "trigger.length")


def mind_tools(*, switches, store, mode_store=None, ledger=None, clock_ms=None, settings_fn=None, flows=None,
               registry=None, research=None):
    """flows(lo, hi, kinds, limit) = the run log query (hello_log_query.flows bound to the db); registry = the tools
    registry (capabilities reads it)."""
    """name -> fn(args): switches = api's switches(); store.enabled() = the 情景 switch; ledger(db_day) = the run log's
    day ledger (hello_log_query.ledger bound to the db), None when the log query is not deployed."""
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    def switches_status(args):
        sw = switches()
        out = {name: {"title": SWITCH_TITLES[name], "on": bool((sw.get(name) or {}).get("on"))}
               for name in ("tingwu", "eye", "notes", "realtime")}
        out["mind"] = {"title": SWITCH_TITLES["mind"], "on": bool(store.enabled())}
        mouth = (sw.get("mouth") or {}).get("mode")
        p = sw.get("pause") or {}
        data = {"switches": out,
                "mouth": {"mode": mouth, "title": MOUTH_TITLES.get(mouth, mouth)},
                "pause": {"paused": bool(p.get("paused")), "why": PAUSE_WHY.get(p.get("why"), p.get("why")),
                          "spent_cny": p.get("spent_cny"), "cap_cny": p.get("cap_cny")}}
        if "billing" in sw:
            data["billing_on"] = bool(sw["billing"].get("on"))
        return data

    def mode_status(args):
        if mode_store is None:
            raise ToolError("not_available", "这里没有接模式")
        snap = mode_store.current()
        mode = snap.get("mode")
        robot = mode_store.robot()
        m = mode_table.MODES.get(mode, {})
        modes = []
        for mid in mode_table._IDS:
            d = mode_table.MODES[mid]
            how = []
            if d.get("enter"):
                how.append("对语音模块说 " + " / ".join(d["enter"]))
            if mid in DIGITS:
                how.append("说数字 " + DIGITS[mid])
            modes.append({"id": mid, "name": d["name"], "label": d["look"]["label"], "how_to_enter": "；".join(how) or "屏幕上选"})
        since = snap.get("since")
        return {"mode": mode, "name": m.get("name"), "label": (snap.get("look") or m.get("look") or {}).get("label"),
                "since": datetime.fromtimestamp(since / 1000, BJ).strftime("%Y-%m-%d %H:%M") if isinstance(since, int) else None,
                # mind learns the robot link from the ear's /mode/robot on a (re)connect: unknown right after mind restarts
                "robot_online": bool(robot.get("connected")) if robot.get("at") else None, "modes": modes}

    def cost_today(args):
        if ledger is None:
            raise ToolError("not_available", "账单查询没有部署")
        day = args.get("day") or datetime.fromtimestamp(now_ms() / 1000, BJ).strftime("%Y-%m-%d")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise ToolError("bad_args", "日期要写成 2026-11-18 这样") from None
        led = ledger(day)
        by = sorted(led.get("by", []), key=lambda r: -(r.get("amount") or 0))[:8]
        sw = switches()
        p = sw.get("pause") or {}
        return {"day": day, "total": led.get("total", {}),
                "top": [{"svc": r.get("svc"), "model": r.get("model"), "amount": round(r.get("amount") or 0, 4),
                         "cur": r.get("cur"), "calls": r.get("n")} for r in by],
                "unknown_calls": led.get("unknown_n", 0),
                "cap_cny": p.get("cap_cny"), "spent_cny_counted": p.get("spent_cny")}

    # ---- B2: 回顾 ----
    db = store.db
    settings = settings_fn or (lambda: settings_mod.load(None))

    def scene_now(args):
        s, now = settings(), now_ms()
        tz = s["tz"]
        out = {"time": tool_time.label(now, tz)}
        row = store.latest_scene_any(LANE)
        if row and row.get("scene"):
            sc = row["scene"]
            said = lambda dim: [e.get("text") for e in sc.get("entries") or [] if e.get("dimension") == dim and e.get("text")]
            out["scene"] = {"minute": tool_time.label(row["minute"], tz), "fresh": now - row["minute"] <= SCENE_FRESH_MS,
                            "doing": (sc.get("people_doing") or {}).get("text") or "", "summary": sc.get("summary") or "",
                            "topics": said("topic")[:3], "sound": "；".join(said("sound_scene")[:1])}
        eye = db["eye_minutes"].find_one({}, {"_id": 0}, sort=[("minute", -1), ("version", -1)])
        if eye and eye.get("result") and now - eye["minute"] <= SCENE_FRESH_MS:
            r = eye["result"]
            out["picture"] = {"minute": tool_time.label(eye["minute"], tz), "people_count": r.get("people_count"),
                              "activity": r.get("activity"), "scene": r.get("scene")}
        live = db["vision_looks"].find_one({"kind": "scene"}, {"_id": 0, "at": 1, "text": 1}, sort=[("at", -1)])
        if live and live.get("text") and now - live["at"] <= LIVE_SCENE_MS:
            out["camera_now"] = {"seconds_ago": int((now - live["at"]) / 1000), "text": live["text"]}
        segs = segments.update(None, store, s, now - 12 * 3600_000, now, LANE, summarize_now=False)
        if segs:
            g = segs[-1]
            out["segment"] = {"since": tool_time.label(g["start"], tz), "class": SEG_CLASS.get(g["cls"], g["cls"]),
                              "title": ((g.get("summary") or {}).get("title"))}
        if len(out) == 1:
            raise ToolError("not_available", "现在没有情景数据（情景或识别可能关着）")
        return out

    def notes_search(args):
        s, now = settings(), now_ms()
        lo, hi = tool_time.span(args.get("from"), args.get("to"), now, s["tz"])
        q = (args.get("query") or "").strip().lower()
        limit = args.get("limit") or 5
        rows = list(db["conversations"].find({"start_ms": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("start_ms", -1).limit(500))
        if q:
            rows = [r for r in rows if q in (str(r.get("title") or "") + " " + str(r.get("notes") or "")).lower()]
        return {"range": [tool_time.label(lo, s["tz"]), tool_time.label(hi, s["tz"])], "total": len(rows),
                "conversations": [{"start": tool_time.label(r["start_ms"], s["tz"]),
                                   "end": tool_time.label(r["end_ms"] or r.get("last_ms") or r["start_ms"], s["tz"], with_day=False),
                                   "title": r.get("title") or "", "notes": r.get("notes") or ""} for r in rows[:limit]]}

    def review_period(args):
        s, now = settings(), now_ms()
        tz = s["tz"]
        lo, hi = tool_time.span(args.get("from"), args.get("to"), now, tz)
        full = args.get("detail") == "full"
        cap = 60 if full else 25
        segs = []
        day = lo
        while day < hi:                                # segments.update reads a day at most in one go
            end = min(day + 86400_000, hi)
            segs += segments.update(None, store, s, day, end, LANE, summarize_now=False)
            day = end
        seg_out = []
        for g in segs:
            sm = g.get("summary") or {}
            if g["cls"] == "gap" and not sm:
                continue
            row = {"start": tool_time.label(g["start"], tz), "end": tool_time.label(g["end"] or hi, tz, with_day=False),
                   "class": SEG_CLASS.get(g["cls"], g["cls"]), "title": sm.get("title")}
            if full:
                row["text"] = sm.get("text")
            seg_out.append(row)
        convs = list(db["conversations"].find({"start_ms": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("start_ms", 1).limit(500))
        conv_out = [{"start": tool_time.label(r["start_ms"], tz), "title": r.get("title") or "",
                     **({"notes": r.get("notes") or ""} if full else {})} for r in convs
                    if full or r.get("title")]          # brief: untitled bits only counted (conversations_total)
        faces = db["vision"].distinct("name", {"at": {"$gte": lo, "$lt": hi}, "event": "face_named"}) \
            if hasattr(db["vision"], "distinct") else sorted({d.get("name") for d in db["vision"].find(
                {"at": {"$gte": lo, "$lt": hi}, "event": "face_named"}, {"name": 1}) if d.get("name")})
        said = db["speech"].count_documents({"t": {"$gte": lo, "$lt": hi}}) if hasattr(db["speech"], "count_documents") else None
        replies = db["realtime_turns"].count_documents({"at": {"$gte": lo, "$lt": hi}, "state": "done"}) \
            if hasattr(db["realtime_turns"], "count_documents") else None
        return {"range": [tool_time.label(lo, tz), tool_time.label(hi, tz)],
                "segments": seg_out[-cap:], "segments_total": len(seg_out),
                "conversations": conv_out[-cap:], "conversations_total": len(convs),
                "people_seen": _people(s, [n for n in faces if n]),
                "counts": {"sentences_heard": said, "hello_replies": replies}}

    # ---- B3: 认识的人、记忆、知识 ----
    lib = Library(db)
    KIND_ZH = {"owner": "主人", "family": "家人", "regular": "常见的人", "visitor": "访客", "unknown": "不认识"}

    def cards():
        return [c for c in lib.people() if c.get("status") != "retired"]

    def names_of(ids):
        table = {"unknown": "不认识的人", **{c["person_id"]: c.get("display") or c["person_id"] for c in cards()}}
        return [table.get(i, i) for i in ids or []]

    def resolve(who):
        """A name as people say it -> person_id: 主人 / owner, a card's display or alias (exact, then contained)."""
        w = str(who or "").strip()
        if not w:
            return None
        cs = cards()
        if w in ("主人", "owner", "我"):
            return "owner"
        low = w.lower()
        for c in cs:
            if low == str(c.get("display") or "").lower() or low in [str(a).lower() for a in c.get("aliases") or []]:
                return c["person_id"]
        for c in cs:
            if low in str(c.get("display") or "").lower() or str(c.get("display") or "").lower() in low:
                return c["person_id"]
        raise ToolError("bad_args", f"不认识「{w[:20]}」，可以先查认识哪些人")

    def memory_row(m, tz):
        sens = m.get("sensitivity") or []
        return {"statement": recall.prompt_text(m), "title": m.get("title") or "", "kind": m.get("kind"),
                "source": {"habit": "习惯", "library": "记忆库"}.get(m.get("source"), m.get("source")),
                "when": tool_time.label(m["at"], tz) if isinstance(m.get("at"), int) else None,
                "people": names_of(m.get("person_ids")),
                "only_for_owner": "private" in sens or "child" in sens}

    def memory_search(args):
        s, now = settings(), now_ms()
        pid = resolve(args.get("person")) if args.get("person") else None
        limit = args.get("limit") or 5
        pool = recall.load_pool(db, s, now)
        if pid:
            pool = [m for m in pool if pid in (m.get("person_ids") or [])]
        q = args["query"]
        hits = recall.related(pool, q, limit=limit, person_id=pid)
        seen = {m.get("id") for m in hits}
        for m in pool:                                 # words the bigram match missed: plain substring
            if len(hits) >= limit:
                break
            if m.get("id") not in seen and q in (m.get("text") or "") + (m.get("title") or ""):
                hits.append(m)
                seen.add(m.get("id"))
        return {"query": q, "person": names_of([pid])[0] if pid else None, "pool": len(pool),
                "memories": [memory_row(m, s["tz"]) for m in hits]}

    def knowledge_search(args):
        page = lib.knowledge(kind=args.get("kind"), q=args["query"], limit=8)
        return {"total": page.get("total"),
                "items": [{"name": k.get("name"), "kind": k.get("kind"), "statement": k.get("statement"),
                           "aliases": k.get("aliases") or [], "about": names_of(k.get("subjects"))} for k in page.get("items", [])]}

    def person_lookup(args):
        s = settings()
        pid = resolve(args["who"])
        try:
            p = lib.person(pid)
        except KeyError:
            raise ToolError("not_available", "记忆库里还没有这个人的卡片") from None
        card = p["card"]
        notes = [n for n in p["notes"] if n.get("status") == "active"]
        mems = sorted(p["memories"], key=lambda m: -(m.get("start_ms") or 0))[:3]
        tr = card.get("treatment") or {}
        return {"display": card.get("display"), "kind": KIND_ZH.get(card.get("kind"), card.get("kind")),
                "aliases": card.get("aliases") or [], "notes": [{"kind": n.get("kind"), "text": n.get("text")} for n in notes[:6]],
                "recent_memories": [{"statement": m.get("statement"), "when": tool_time.label(m["start_ms"], s["tz"])
                                     if isinstance(m.get("start_ms"), int) else None} for m in mems],
                "treatment": {"tease": tr.get("tease"), "quote": tr.get("quote")},
                "knows_by": {"voice": ((card.get("links") or {}).get("voice_names") or []),
                             "face": ((card.get("links") or {}).get("face_names") or [])}}

    def people_list(args):
        return {"people": [{"display": c.get("display"), "kind": KIND_ZH.get(c.get("kind"), c.get("kind")),
                            "notes": c.get("notes", 0)} for c in cards()]}

    # ---- B4: 说过的话（主人 2026-11-18：谁的话都能查） ----
    for coll, keys in (("speech", [("speaker.name", 1), ("t", 1)]), ("sounds", [("t", 1)]), ("utterances", [("at", 1)])):
        try:                                           # idempotent; the queries below would scan without them
            db[coll].create_index(keys)
        except Exception:                              # noqa: BLE001 - a missing index is slower, never wrong
            pass

    def voice_table(s):
        """voice name -> how hello calls the person (settings + library cards' voice links)."""
        owner = s.get("owner") or {}
        table = {n: owner.get("display") or "主人" for n in owner.get("voice_names") or []}
        for p in s.get("people") or []:
            for n in p.get("voice_names") or []:
                table[n] = p.get("display") or n
        for c in cards():
            for n in ((c.get("links") or {}).get("voice_names") or []):
                table[n] = c.get("display") or n
        return table

    def voices_of(s, who):
        pid = resolve(who)
        names = set()
        if pid == "owner":
            names |= set((s.get("owner") or {}).get("voice_names") or [])
        for c in cards():
            if c["person_id"] == pid:
                names |= set((c.get("links") or {}).get("voice_names") or [])
        if not names:
            raise ToolError("not_available", f"还没有「{who}」的声纹，认不出他的声音")
        return sorted(names)

    def speaker_of(d, table):
        sp = d.get("speaker") or {}
        if sp.get("band") == "known" and sp.get("name"):
            return table.get(sp["name"], sp["name"])
        if sp.get("band") == "unsure" and sp.get("best"):
            return f"不确定（像{table.get(sp['best'], sp['best'])}）"
        return "不认识的声音"

    def lines(lo, hi, who=None, query=None, limit=10, newest_first=True):
        s = settings()
        table = voice_table(s)
        flt = {"t": {"$gte": lo, "$lt": hi}, "text": {"$nin": ["", None]}}
        if who:
            flt["speaker.name"] = {"$in": voices_of(s, who)}
        if query:
            flt["text"] = {"$regex": re.escape(query), "$options": "i"}
        rows = list(db["speech"].find(flt, {"_id": 0, "t": 1, "text": 1, "speaker": 1, "session": 1, "pos": 1, "self": 1,
                                            "emotion": 1}).sort("t", -1 if newest_first else 1).limit(2000))
        out = []
        for d in rows:                                 # hello's own voice is not what somebody said
            me = d.get("self") or {}
            if selfvoice.is_hello(d.get("text") or "", bool(me.get("sounding"))):
                continue
            out.append({"time": tool_time.label(d["t"], s["tz"]), "who": speaker_of(d, table), "text": d.get("text"),
                        "session": d.get("session"), "pos": d.get("pos")})
        return {"total": len(out), "lines": out[:limit]}

    def speech_search(args):
        s, now = settings(), now_ms()
        if not (args.get("who") or args.get("query")):
            raise ToolError("bad_args", "要说查谁（who）或查什么词（query），至少一个")
        lo, hi = tool_time.span(args.get("from"), args.get("to"), now, s["tz"], default_ms=7 * 86400_000)
        return {"range": [tool_time.label(lo, s["tz"]), tool_time.label(hi, s["tz"])],
                **lines(lo, hi, args.get("who"), args.get("query"), args.get("limit") or 10)}

    def recent_speech(args):
        now = now_ms()
        minutes = args.get("minutes") or 5
        d = lines(now - minutes * 60_000, now, args.get("who"), None, 40, newest_first=False)
        return {"minutes": minutes, **d}

    def recent_sounds(args):
        s, now = settings(), now_ms()
        minutes = args.get("minutes") or 10
        rows = list(db["sounds"].find({"t": {"$gte": now - minutes * 60_000, "$lt": now}},
                                      {"_id": 0, "t": 1, "kind": 1, "label": 1, "dur": 1, "self": 1}).sort("t", 1).limit(500))
        rows = [r for r in rows if not (r.get("self") or {}).get("sounding")]     # not hello's own sound
        events = []                                    # back-to-back events of one kind: one entry with a count
        for r in rows:
            kind = r.get("label") or r.get("kind")
            dur = r["dur"] if isinstance(r.get("dur"), (int, float)) else 0
            if events and events[-1]["kind"] == kind and r["t"] - events[-1]["_last"] <= 60_000:
                e = events[-1]
                e["times"] += 1
                e["dur_s"] = round(e["dur_s"] + dur, 1)
                e["until"], e["_last"] = tool_time.label(r["t"], s["tz"], with_day=False), r["t"]
            else:
                t = tool_time.label(r["t"], s["tz"], with_day=False)
                events.append({"time": t, "until": t, "kind": kind, "times": 1, "dur_s": round(dur, 1), "_last": r["t"]})
        for e in events:
            e.pop("_last")
        return {"minutes": minutes, "total": len(rows), "events": events[-40:]}

    def hello_said(args):
        s, now = settings(), now_ms()
        lo, hi = tool_time.span(args.get("from"), args.get("to"), now, s["tz"])
        q = (args.get("query") or "").lower()
        out = []
        for d in db["realtime_turns"].find({"at": {"$gte": lo, "$lt": hi}, "state": "done"},
                                           {"_id": 0, "at": 1, "kind": 1, "heard": 1, "reply": 1}):
            out.append({"at": d["at"], "mode": "Docking", "heard": d.get("heard"), "said": d.get("reply")})
        for d in db["chat_turns"].find({"at": {"$gte": lo, "$lt": hi}}, {"_id": 0, "at": 1, "text": 1, "reply": 1}):
            if d.get("reply"):
                out.append({"at": d["at"], "mode": "对话", "heard": d.get("text"), "said": d.get("reply")})
        for d in db["utterances"].find({"at": {"$gte": lo, "$lt": hi}, "state": {"$in": ["sent", "played", "done"]}},
                                       {"_id": 0, "at": 1, "role": 1, "text": 1}):
            out.append({"at": d["at"], "mode": "台词", "heard": None, "said": d.get("text")})
        out = [o for o in out if o.get("said") and (not q or q in str(o["said"]).lower() or q in str(o.get("heard") or "").lower())]
        out.sort(key=lambda o: -o["at"])
        limit = args.get("limit") or 10
        return {"range": [tool_time.label(lo, s["tz"]), tool_time.label(hi, s["tz"])], "total": len(out),
                "lines": [{"time": tool_time.label(o["at"], s["tz"]), "mode": o["mode"], "heard": o["heard"], "said": o["said"]}
                          for o in out[:limit]]}

    # ---- B5: 解释自己 ----
    def why_last_turn(args):
        """The last turns' decision chains from the run log: what was heard, called or not, each gate's verdict and why,
        what was said or done. One entry per trace, newest first."""
        if flows is None:
            raise ToolError("not_available", "运行日志查询没有部署")
        s, now = settings(), now_ms()
        minutes = args.get("minutes") or 5
        rows = flows(now - minutes * 60_000, now, WHY_KINDS, 400)
        by = {}
        for r in sorted(rows, key=lambda r: r["at"]):
            key = r.get("trace") or r.get("_id")
            t = by.setdefault(key, {"at": r["at"], "heard": None, "called": None, "steps": [], "said": None})
            k, x = r.get("kind"), r.get("extra") or {}
            if k == "heard.asr":
                t["heard"] = t["heard"] or (r.get("sum") or "")
            elif k == "heard.called":
                t["called"] = r.get("sum")
            elif k == "gate.realtime":
                v = x.get("verdict") or ""
                t["steps"].append(VERDICT_ZH.get(v, v) or (r.get("sum") or ""))
                if not t["heard"] and x.get("heard"):
                    h = x["heard"]
                    t["heard"] = h if isinstance(h, str) else "；".join(i.get("text", "") for i in h if isinstance(i, dict))[:120] or None
            elif k == "gate.rescue":
                t["steps"].append(f"补救：{x.get('outcome')}")
            elif k == "call.realtime.turn":
                if x.get("state") == "done":                # played; a muted reply is in steps already
                    t["said"] = r.get("sum")
                kind = x.get("kind")
                if kind in KIND_ZH_TURN:
                    t["steps"].insert(0, KIND_ZH_TURN[kind])
            elif k in ("trigger.task", "trigger.burst", "call.search", "trigger.length"):
                t["steps"].append(r.get("sum") or k)
        turns = sorted(by.values(), key=lambda t: -t["at"])
        turns = [t for t in turns if t["steps"] or t["said"] or t["called"]] or turns
        out = []
        for t in turns[:8]:
            if not t["steps"] and not t["said"]:
                t["steps"] = ["没有回答：千问没有接这句（或声音没送上去）"]
            out.append({"time": tool_time.label(t["at"], s["tz"], with_day=False), "heard": t["heard"],
                        "called": t["called"], "steps": t["steps"][:6], "said": t["said"]})
        return {"minutes": minutes, "turns": out}

    def capabilities(args):
        reg = registry or {}
        groups = reg.get("groups", {})
        want = args.get("group")
        can, later = {}, []
        for t in reg.get("tools", []):
            if want and t["group"] != want:
                continue
            if t["name"] in impls and t.get("status") == "live":
                can.setdefault(groups.get(t["group"], t["group"]), []).append({"title": t["title"], "use": t["description"]})
            else:
                later.append(t["title"])
        sw = switches_status({})
        on = [v["title"] for v in sw["switches"].values() if v["on"]]
        off = [v["title"] for v in sw["switches"].values() if not v["on"]]
        return {"can_do": [{"group": g, "tools": ts} for g, ts in can.items()], "not_yet": later,
                "senses_on": on, "senses_off": off, "paused": sw["pause"]["paused"]}

    impls = {"switches_status": switches_status, "mode_status": mode_status, "cost_today": cost_today,
             "scene_now": scene_now, "notes_search": notes_search, "review_period": review_period,
             "memory_search": memory_search, "knowledge_search": knowledge_search, "person_lookup": person_lookup,
             "people_list": people_list, "speech_search": speech_search, "recent_speech": recent_speech,
             "recent_sounds": recent_sounds, "hello_said": hello_said, "why_last_turn": why_last_turn,
             "capabilities": capabilities}
    if research is not None:                     # 深入研究 (mind/research.py Runner): deep_research, research_status
        from . import research as research_mod
        impls.update(research_mod.tools(research))
    return impls


# ---- L1: the ear's tools (server/ear/ask_api.py, 127.0.0.1 only) ----
EAR_TOOLS = ("who_is_here", "look_now", "web_search", "say", "stop_speaking", "set_length", "set_quiet", "set_role")
EAR_ASK_URL = "http://127.0.0.1:52743/ask"


def ear_asker(token, url=EAR_ASK_URL, timeout=32):
    """ask(name, args) -> data; the ear's error -> ToolError."""
    import json
    import urllib.error
    import urllib.request

    def ask(name, args):
        req = urllib.request.Request(url, data=json.dumps({"name": name, "args": args}, ensure_ascii=False).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, ValueError):
            raise ToolError("not_available", "耳朵没连上（ear 服务没在跑）") from None
        if not out.get("ok"):
            e = out.get("error") or {}
            raise ToolError(e.get("code") or "failed", e.get("message") or "耳朵出错了")
        return out.get("data")
    return ask


def ear_tools(ask):
    return {name: (lambda args, _n=name: ask(_n, args)) for name in EAR_TOOLS}
