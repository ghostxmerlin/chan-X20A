"""mind's tool implementations (B1, 2026-11-18; framework: tools.py, rules: docs/hello-tools.md).

B1 wires the three tools that already had an interface: switches_status, mode_status, cost_today. Each returns only
what a model needs, in short keys with Chinese titles, and never a key, token or internal address.
"""
import time
from datetime import datetime, timedelta, timezone

from . import mode_table, segments, settings as settings_mod, tool_time
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


def mind_tools(*, switches, store, mode_store=None, ledger=None, clock_ms=None, settings_fn=None):
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

    return {"switches_status": switches_status, "mode_status": mode_status, "cost_today": cost_today,
            "scene_now": scene_now, "notes_search": notes_search, "review_period": review_period}
