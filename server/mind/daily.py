"""日常 (L4, 2026-11-18): reminders, a simple schedule, and watches ("帮忙盯着"), spoken by hello.

Mongo hello.reminders {_id, kind reminder|schedule, text/title, place, due (epoch ms), repeat none|daily|weekdays|
weekly, remind_before_min, start, end, state pending/held/done/missed/cancelled, by, at, said_at, tries}
Mongo hello.watches   {_id, what, who, sound, until, how, state active/fired/expired/stopped, by, at, fired_at, said}

Delivery (owner 2026-11-18: no notify channel yet): hello says it in Docking through the ear's say ask. When nobody
is there (no face, no known voice, no owner input for AWAY_MS) a due line is held and said when someone is back;
held longer than HOLD_MAX_MS it is missed. A busy ear (someone speaking, hello speaking) retries next tick.
Daily.tick() runs every TICK_S in its own thread (runner.py).
"""
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import tool_time
from .runlog import hello_log
from .tools import ToolError

TICK_S = 10
AWAY_MS = 5 * 60_000
HOLD_MAX_MS = 12 * 3600_000
RETRY_MAX_MS = 10 * 60_000
WATCH_KINDS = ("person_enter", "person_leave", "named_person", "sound", "scene_change", "owner_back")
WATCH_ZH = {"person_enter": "有人来", "person_leave": "人走了", "named_person": "某个人出现", "sound": "某种声音",
            "scene_change": "画面变化", "owner_back": "主人回来"}


def _id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Daily:
    def __init__(self, db, say=None, clock_ms=None, settings_fn=None, emit=None):
        self.db = db
        self.say = say                                  # say(text) -> True spoken / False not now (busy, not ready)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.settings = settings_fn or (lambda: {"tz": "Asia/Shanghai"})
        self.emit = emit or hello_log.emit
        self._watch_seen = {}                           # watch id -> what it saw last (faces in view, ...)
        self._lock = threading.Lock()
        for c, keys in (("reminders", [("state", 1), ("due", 1)]), ("watches", [("state", 1), ("until", 1)])):
            try:
                db[c].create_index(keys)
            except Exception:                           # noqa: BLE001
                pass

    def tz(self):
        return self.settings().get("tz") or "Asia/Shanghai"

    def label(self, ms):
        return tool_time.label(ms, self.tz())

    # ---- reminders ----
    def reminder_set(self, when, text, repeat="none", by=None):
        now = self.clock_ms()
        due = tool_time.future(when, now, self.tz())
        rid = _id("r")
        self.db["reminders"].insert_one({"_id": rid, "kind": "reminder", "text": text.strip(), "due": due,
                                         "repeat": repeat or "none", "state": "pending", "by": by, "at": now, "tries": 0})
        self.emit("call.daily", by=by, sum=f"提醒：{self.label(due)} {text[:30]}", extra={"id": rid, "action": "set"})
        return {"id": rid, "when": self.label(due), "text": text, "repeat": repeat or "none"}

    def schedule_add(self, start, title, end=None, place=None, remind_before_min=None, by=None):
        now = self.clock_ms()
        s = tool_time.future(start, now, self.tz())
        e = tool_time.future(end, now, self.tz()) if end else None
        if e is not None and e <= s:
            raise ToolError("bad_args", "结束时间要在开始之后")
        before = 10 if remind_before_min is None else int(remind_before_min)
        rid = _id("s")
        self.db["reminders"].insert_one({"_id": rid, "kind": "schedule", "title": title.strip(), "place": place,
                                         "start": s, "end": e, "remind_before_min": before,
                                         "due": max(now + 5_000, s - before * 60_000), "repeat": "none",
                                         "state": "pending", "by": by, "at": now, "tries": 0})
        self.emit("call.daily", by=by, sum=f"日程：{self.label(s)} {title[:30]}", extra={"id": rid, "action": "add"})
        return {"id": rid, "start": self.label(s), "end": self.label(e) if e else None, "title": title, "place": place,
                "remind": f"提前 {before} 分钟提醒" if before else "到点提醒"}

    def _span(self, word, now):
        """A list range word -> (start, end): past/today words via tool_time.parse, else a future day (明天, 周五)."""
        try:
            a, b = tool_time.parse(word, now, self.tz())
            return a, (b if b > a else a + 86400_000)
        except ToolError:
            ms = tool_time.future(word, now, self.tz())
            d = datetime.fromtimestamp(ms / 1000, ZoneInfo(self.tz())).replace(hour=0, minute=0, second=0, microsecond=0)
            return int(d.timestamp() * 1000), int((d + timedelta(days=1)).timestamp() * 1000)

    def _rows(self, kind, frm, to, states=("pending", "held")):
        now = self.clock_ms()
        rows = list(self.db["reminders"].find({"kind": kind, "state": {"$in": list(states)}}).sort("due", 1).limit(200))
        if frm or to:
            lo, hi = self._span(frm, now) if frm else (now - 86400_000, None)
            if to:
                hi = self._span(to, now)[1]
            elif not frm or hi is None:
                hi = now + 400 * 86400_000
            key = "start" if kind == "schedule" else "due"
            rows = [r for r in rows if lo <= r[key] < hi]
        return rows

    def reminder_list(self, frm=None, to=None):
        rows = self._rows("reminder", frm, to)
        return {"total": len(rows), "items": [{"id": r["_id"], "when": self.label(r["due"]), "text": r["text"],
                                               "repeat": r["repeat"], "state": r["state"]} for r in rows[:20]]}

    def schedule_list(self, frm=None, to=None):
        rows = sorted(self._rows("schedule", frm, to), key=lambda r: r["start"])
        return {"total": len(rows), "items": [{"id": r["_id"], "start": self.label(r["start"]),
                                               "end": self.label(r["end"]) if r.get("end") else None,
                                               "title": r["title"], "place": r.get("place")} for r in rows[:20]]}

    def reminder_cancel(self, rid=None, query=None, by=None):
        if not rid and not query:
            raise ToolError("bad_args", "要说取消哪一个（id）或按内容找（query）")
        flt = {"state": {"$in": ["pending", "held"]}}
        rows = [r for r in self.db["reminders"].find(flt)
                if (rid and r["_id"] == rid) or (query and query in (r.get("text") or r.get("title") or ""))]
        if not rows:
            raise ToolError("not_available", "没找到这个提醒")
        if len(rows) > 1 and not rid:
            return {"cancelled": 0, "ambiguous": [{"id": r["_id"], "what": r.get("text") or r.get("title"),
                                                   "when": self.label(r["due"])} for r in rows[:5]]}
        for r in rows[:1]:
            self.db["reminders"].update_one({"_id": r["_id"]}, {"$set": {"state": "cancelled", "decided_at": self.clock_ms()}})
            self.emit("call.daily", by=by, sum=f"取消：{(r.get('text') or r.get('title'))[:30]}",
                      extra={"id": r["_id"], "action": "cancel"})
        return {"cancelled": 1, "what": rows[0].get("text") or rows[0].get("title")}

    # ---- watches ----
    def watch_start(self, what, who=None, sound=None, minutes=None, how="say", by=None):
        if what not in WATCH_KINDS:
            raise ToolError("bad_args", "不认识要盯什么")
        if how == "notify":
            raise ToolError("not_available", "还没有通知渠道（主人 2026-11-18：先不做），只能当面说")
        if what == "scene_change":
            raise ToolError("not_available", "画面变化还盯不了（下一步）")
        if what == "named_person" and not who:
            raise ToolError("bad_args", "要说盯谁（who）")
        if what == "sound" and not sound:
            raise ToolError("bad_args", "要说盯什么声音（sound，比如 敲门、门铃、哭声）")
        now = self.clock_ms()
        wid = _id("w")
        until = now + int(minutes or 60) * 60_000
        self.db["watches"].insert_one({"_id": wid, "what": what, "who": who, "sound": sound, "until": until, "how": how,
                                       "state": "active", "by": by, "at": now})
        self.emit("call.daily", by=by, sum=f"盯着：{WATCH_ZH[what]}{('（' + (who or sound) + '）') if (who or sound) else ''}",
                  extra={"id": wid, "action": "watch"})
        return {"id": wid, "what": WATCH_ZH[what], "until": self.label(until)}

    def watch_list(self):
        rows = list(self.db["watches"].find({"state": "active"}).sort("at", -1).limit(20))
        return {"total": len(rows), "items": [{"id": r["_id"], "what": WATCH_ZH[r["what"]], "who": r.get("who"),
                                               "sound": r.get("sound"), "until": self.label(r["until"])} for r in rows]}

    def watch_stop(self, wid=None, by=None):
        flt = {"state": "active"} if not wid else {"_id": wid, "state": "active"}
        n = 0
        for r in list(self.db["watches"].find(flt)):
            self.db["watches"].update_one({"_id": r["_id"]}, {"$set": {"state": "stopped", "stopped_at": self.clock_ms()}})
            n += 1
        return {"stopped": n}

    # ---- the tick ----
    def present(self, now):
        """Someone is there: a face in view, a known voice or an owner input within AWAY_MS (ear's presence doc)."""
        p = self.db["switches"].find_one({"_id": "presence_status"}) or {}
        fresh = isinstance(p.get("at"), int) and now - p["at"] <= 150_000
        if fresh and (p.get("faces") or 0) > 0:
            return True
        return any(isinstance(p.get(k), int) and now - p[k] <= AWAY_MS for k in ("face_at", "voice_at", "owner_at"))

    def tick(self):
        with self._lock:
            now = self.clock_ms()
            self._due(now)
            self._watches(now)

    def _speak(self, text):
        try:
            return bool(self.say and self.say(text))
        except Exception:                               # noqa: BLE001 - the ear is down: try again later
            return False

    def _due(self, now):
        here = None
        for r in list(self.db["reminders"].find({"state": {"$in": ["pending", "held"]}, "due": {"$lte": now}}).sort("due", 1).limit(20)):
            if now - r["due"] > HOLD_MAX_MS:
                self.db["reminders"].update_one({"_id": r["_id"]}, {"$set": {"state": "missed"}})
                self.emit("call.daily", ok=False, sum=f"错过了：{(r.get('text') or r.get('title'))[:30]}",
                          extra={"id": r["_id"], "action": "missed"})
                self._next(r)
                continue
            here = self.present(now) if here is None else here
            if not here:
                if r["state"] != "held":
                    self.db["reminders"].update_one({"_id": r["_id"]}, {"$set": {"state": "held"}})
                continue
            line = self.line(r, now)
            if self._speak(line):
                self.db["reminders"].update_one({"_id": r["_id"]}, {"$set": {"state": "done", "said_at": now, "said": line}})
                self.emit("call.daily", sum=f"说了：{line[:40]}", extra={"id": r["_id"], "action": "said"})
                self._next(r)
            else:
                self.db["reminders"].update_one({"_id": r["_id"]}, {"$inc": {"tries": 1}})

    def line(self, r, now):
        late = now - r["due"] > 10 * 60_000
        if r["kind"] == "schedule":
            mins = max(0, round((r["start"] - now) / 60_000))
            when = "现在" if mins <= 1 else f"还有 {mins} 分钟" if mins < 120 else f"在 {self.label(r['start'])}"
            return f"日程提醒：{r['title']}，{when}" + (f"，在{r['place']}" if r.get("place") else "") + "。"
        return ("刚才没人在，现在补上提醒：" if late else "提醒一下：") + r["text"]

    def _next(self, r):
        rep = r.get("repeat") or "none"
        if rep == "none":
            return
        z = ZoneInfo(self.tz())
        d = datetime.fromtimestamp(r["due"] / 1000, z)
        step = {"daily": 1, "weekly": 7}.get(rep, 1)
        d += timedelta(days=step)
        if rep == "weekdays":
            while d.weekday() >= 5:
                d += timedelta(days=1)
        nxt = {k: v for k, v in r.items() if k not in ("_id", "said_at", "said", "state", "tries")}
        nxt.update({"_id": _id("r"), "due": int(d.timestamp() * 1000), "state": "pending", "tries": 0})
        self.db["reminders"].insert_one(nxt)

    def _watches(self, now):
        rows = list(self.db["watches"].find({"state": "active"}))
        if not rows:
            return
        p = self.db["switches"].find_one({"_id": "presence_status"}) or {}
        fresh = isinstance(p.get("at"), int) and now - p["at"] <= 150_000
        faces = (p.get("faces") or 0) if fresh else 0
        s = self.settings()
        owner = s.get("owner") or {}
        call = owner.get("display") or "主人"
        for w in rows:
            if now >= w["until"]:
                self.db["watches"].update_one({"_id": w["_id"]}, {"$set": {"state": "expired"}})
                continue
            prev = self._watch_seen.get(w["_id"], {"faces": faces, "since": max(w["at"], now - TICK_S * 1000)})
            hit = None
            if w["what"] == "person_enter" and prev["faces"] == 0 and faces > 0:
                hit = f"{call}，有人来了。"
            elif w["what"] == "person_leave" and prev["faces"] > 0 and faces == 0:
                hit = "刚才的人走了。"
            elif w["what"] in ("named_person", "owner_back"):
                names = self._face_names(w.get("who"), s) if w["what"] == "named_person" else \
                    set(owner.get("face_names") or [])
                seen = self.db["vision"].find_one({"event": "face_named", "name": {"$in": sorted(names)},
                                                   "at": {"$gte": prev["since"], "$lt": now + 1}}) if names else None
                if seen:
                    hit = f"{call}，{w['who']}来了。" if w["what"] == "named_person" else f"{call}，欢迎回来。"
            elif w["what"] == "sound":
                snd = self.db["sounds"].find_one({"t": {"$gte": prev["since"], "$lt": now + 1},
                                                  "label": {"$regex": re.escape(w["sound"]), "$options": "i"}})
                if snd:
                    hit = f"{call}，听到{snd.get('label') or w['sound']}了。"
            self._watch_seen[w["_id"]] = {"faces": faces, "since": now}
            if hit and self._speak(hit):
                self.db["watches"].update_one({"_id": w["_id"]}, {"$set": {"state": "fired", "fired_at": now, "said": hit}})
                self.emit("call.daily", sum=f"盯到了：{hit[:40]}", extra={"id": w["_id"], "action": "fired"})

    def _face_names(self, who, s):
        names = set()
        w = str(who or "").strip()
        owner = s.get("owner") or {}
        if w in ("主人", "owner", "我"):
            return set(owner.get("face_names") or [])
        for c in self.db["people_cards"].find({}):
            if w and (w == c.get("display") or w in (c.get("aliases") or [])):
                names |= set((c.get("links") or {}).get("face_names") or [])
        return names | ({w} if w else set())

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:                      # noqa: BLE001 - one bad tick
                print(f"mind daily: tick failed: {type(e).__name__}: {e}", flush=True)
            time.sleep(TICK_S)


def _is_past_word(w):
    return bool(re.search(r"前$|昨|前天|上周|刚才", str(w)))


def tools(daily):
    return {
        "reminder_set": lambda a: daily.reminder_set(a["when"], a["text"], a.get("repeat") or "none", by="tool"),
        "reminder_list": lambda a: daily.reminder_list(a.get("from"), a.get("to")),
        "reminder_cancel": lambda a: daily.reminder_cancel(a.get("id"), a.get("query"), by="tool"),
        "schedule_add": lambda a: daily.schedule_add(a["start"], a["title"], a.get("end"), a.get("place"),
                                                     a.get("remind_before_min"), by="tool"),
        "schedule_list": lambda a: daily.schedule_list(a.get("from"), a.get("to")),
        "watch_start": lambda a: daily.watch_start(a["what"], a.get("who"), a.get("sound"), a.get("minutes"),
                                                   a.get("how") or "say", by="tool"),
        "watch_list": lambda a: daily.watch_list(),
        "watch_stop": lambda a: daily.watch_stop(a.get("id"), by="tool"),
    }
