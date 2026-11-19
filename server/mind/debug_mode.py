"""调试模式, mind's half (owner 2026-11-19, task 3 "帮我调试": hello collects what it hears, reminds me of problems, I
can cut in at any time; no length said = until I end it; reminders yes; at the end ask, do not sum up by itself).

The ear owns the mode (realtime_link.set_debug): it is in hello.switches {_id: rt_modes_status}.debug {since_ms,
until_ms, open} while on. This worker (one thread, every TICK_S):
  - on: one session doc per start in hello.debug_sessions {_id "dbg-<since_ms>", start_ms, end_ms, scanned_to, notes[],
    alerts[], topic, summary}; the speech heard since scanned_to (hello.speech, not hello's own lines) goes, every
    SCAN_S or once NEW_MIN sentences wait, to the model (luna, run_json) with the notes so far, the counters (how often
    each note key came, minutes since the last progress) and the material entries the error words match
    (docs_kb.keyword_hits, no embedding): it returns new notes {kind error|reading|attempt|result|complaint|plan, text,
    key, value?, unit?} and maybe one reminder {say, why repeat|stuck|mismatch|doc, refs}. A reminder goes to the
    ear (debug_alert; the ear says it when hello is quiet) at most every ALERT_S.
  - off: the open session gets end_ms.
Tools (model, via mind): debug_log {query?} -> the current (or last) session's notes / counts / alerts; debug_summary
-> a summary of the session (luna), kept on the session. The ear asks whether to sum up when the mode ends; only a yes
makes the model call debug_summary.
Money: luna calls (run_json, the run log's call.debug.* rows, the daily cap); the speech is free.
"""
import collections
import re
import threading
import time

from . import docs_kb, model_calls, pause, selfvoice
from .runlog import hello_log

COLL = "debug_sessions"
TICK_S = 20
SCAN_S = 150                   # the model looks at the new speech at most this often ...
NEW_MIN = 6                    # ... or sooner once this many sentences wait
ALERT_S = 300                  # reminders at most this often
STUCK_MIN = 15                 # minutes without a result / plan note: "stuck" is offered to the model
NOTES_KEPT = 200
KINDS = ("error", "reading", "attempt", "result", "complaint", "plan")
KIND_ZH = {"error": "报错", "reading": "读数", "attempt": "试了", "result": "结果", "complaint": "不行", "plan": "打算"}

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["topic", "notes", "alert"], "properties": {
    "topic": {"type": "string"},
    "notes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["kind", "text", "key", "value", "unit", "at"],
                                         "properties": {"kind": {"type": "string", "enum": list(KINDS)},
                                                        "text": {"type": "string"}, "key": {"type": "string"},
                                                        "value": {"type": "string"},
                                                        "unit": {"type": "string"},
                                                        "at": {"type": "string"}}}},
    "alert": {"type": "object", "additionalProperties": False, "required": ["say", "why", "refs"],
              "properties": {"say": {"type": "string"},
                             "why": {"type": "string", "enum": ["none", "repeat", "stuck", "mismatch", "doc", "other"]},
                             "refs": {"type": "array", "items": {"type": "string"}}}}}}

SYSTEM = (
    "你在帮主人记调试记录。主人在调试硬件或软件，边做边说话；你从他说的话里摘出调试线索，记成简短的条目：\n"
    "error 报错 / 现象；reading 读数（带数值和单位）；attempt 试了什么；result 结果（好了 / 没好）；complaint 「又挂了」「不行」"
    "这类抱怨；plan 接下来打算做什么。key 是同一件事的短名字（比如「I2C NACK」「电流」「复位」），同一件事每次都用同一个 key；"
    "at 写原话的时间 HH:MM:SS。闲聊、跟调试无关的话不记。text 用主人的说法，简短。\n"
    "value / unit 是读数的数值和单位（字符串，没有就空字符串）。\n"
    "然后判断要不要提醒主人一句（alert；大多数时候不用提醒：say 空字符串、why none）：只在真有用时提醒——同一个问题已经出现 3 次以上（repeat）；卡在同"
    "一件事上很久没有进展（stuck，给了「多少分钟没进展」）；同一个量前后读数对不上（mismatch，说出两个数）；资料库里有对得上的"
    "条目（doc，说条目号和一句结论）。say 是一句口语提醒，具体、短，不要客套；refs 写相关的条目号或 key。最近已经提醒过的"
    "事不要再提。topic 用几个字说主人在调什么。只输出 JSON。")


def _hms(ms):
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000))


def state(db):
    """The ear's debug mode now: {since_ms, until_ms, open} or None."""
    doc = db["switches"].find_one({"_id": "rt_modes_status"}) or {}
    d = doc.get("debug")
    if not isinstance(d, dict) or not isinstance(d.get("since_ms"), (int, float)):
        return None
    if not d.get("open") and isinstance(d.get("until_ms"), (int, float)) and d["until_ms"] < time.time() * 1000:
        return None
    return d


def counters(notes, now_ms):
    keys = collections.Counter(n.get("key") for n in notes if n.get("key"))
    progress = [n["at_ms"] for n in notes if n.get("kind") in ("result", "plan") and isinstance(n.get("at_ms"), (int, float))]
    last = max(progress) if progress else (notes[0]["at_ms"] if notes and isinstance(notes[0].get("at_ms"), (int, float)) else None)
    return {"by_key": dict(keys.most_common(8)), "minutes_since_progress": round((now_ms - last) / 60000) if last else None}


def doc_hints(db, notes):
    """Material entries that the error notes' identifier words match (exact words only: free)."""
    out = []
    for n in notes:
        if n.get("kind") != "error" or not docs_kb.words(n.get("text") or ""):
            continue
        for _id in docs_kb.keyword_hits(db, n["text"], limit=2):
            c = db[docs_kb.COLL].find_one({"_id": _id}, {"cid": 1, "title": 1, "project_title": 1})
            if c:
                out.append(f"{c.get('project_title')} {c.get('cid')}：{(c.get('title') or '')[:50]}")
    return list(dict.fromkeys(out))[:4]


class Worker:
    def __init__(self, db, env, store, settings_fn, ask=None, clock_ms=None, run_json=None):
        self.db, self.env, self.store, self.settings_fn, self.ask = db, env, store, settings_fn, ask
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.run_json = run_json or model_calls.run_json
        self.last_scan = 0
        self.last_alert = 0
        self.stats = collections.Counter()

    def session(self, since_ms):
        sid = f"dbg-{int(since_ms)}"
        doc = self.db[COLL].find_one({"_id": sid})
        if doc is None:
            doc = {"_id": sid, "start_ms": int(since_ms), "end_ms": None, "scanned_to": int(since_ms), "notes": [],
                   "alerts": [], "topic": None, "summary": None}
            self.db[COLL].replace_one({"_id": sid}, doc, upsert=True)
        return doc

    def tick(self):
        now = self.clock_ms()
        st = state(self.db)
        if st is None:
            for d in self.db[COLL].find({"end_ms": None}, {"_id": 1}):
                self.db[COLL].update_one({"_id": d["_id"]}, {"$set": {"end_ms": now}})
            return "off"
        doc = self.session(st["since_ms"])
        rows = list(self.db["speech"].find({"t": {"$gt": doc["scanned_to"], "$lte": now}},
                                           {"_id": 0, "t": 1, "text": 1, "speaker": 1, "self": 1}).sort("t", 1).limit(80))
        # hello's own voice is not what somebody said (as recent_speech does): `self` is {sounding, moving}, not a flag
        # (2026-11-19 17:00: every row was dropped as hello's own and the first session got no notes)
        rows = [r for r in rows if (r.get("text") or "").strip() and
                not selfvoice.is_hello(r.get("text") or "", bool((r.get("self") or {}).get("sounding")))]
        if not rows:
            return "quiet"
        if len(rows) < NEW_MIN and now - self.last_scan < SCAN_S * 1000:
            return "wait"
        self.last_scan = now
        if self.env is None or pause.budget_stop(self.db, now):
            return "no_model"
        heard = "\n".join(f"[{_hms(r['t'])}] {((r.get('speaker') or {}).get('name') or '有人')}：{r['text'][:200]}"
                          for r in rows)
        notes = doc.get("notes") or []
        recent = "\n".join(f"[{_hms(n['at_ms'])}] {KIND_ZH.get(n['kind'], n['kind'])} {n.get('key') or ''}：{n['text']}"
                           for n in notes[-40:] if isinstance(n.get("at_ms"), (int, float)))
        cnt = counters(notes, now)
        hints = doc_hints(self.db, notes[-10:])
        alerts = "；".join(a.get("say", "") for a in (doc.get("alerts") or [])[-3:])
        user = (f"调试从 {_hms(doc['start_ms'])} 开始，现在 {_hms(now)}。\n已有记录（旧的在前）：\n{recent or '（还没有）'}\n"
                f"各件事出现的次数：{cnt['by_key'] or '无'}；距离上次有进展：{cnt['minutes_since_progress']} 分钟"
                f"（超过 {STUCK_MIN} 分钟算卡住）。\n资料库里可能相关的条目：{'；'.join(hints) or '无'}\n"
                f"最近已经提醒过：{alerts or '无'}\n\n新听到的话：\n{heard}")
        out, _, err = self.run_json(self.env, self.store, self.settings_fn(), "debug_notes", "debug-v1", SCHEMA,
                                    [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], now)
        self.stats["scans"] += 1
        upd = {"scanned_to": rows[-1]["t"]}
        if out is None:
            self.db[COLL].update_one({"_id": doc["_id"]}, {"$set": upd})
            return f"error {err}"
        day = time.strftime("%Y-%m-%d ", time.localtime(now / 1000))
        new = []
        for n in out.get("notes") or []:
            try:
                at_ms = int(time.mktime(time.strptime(day + n.get("at", ""), "%Y-%m-%d %H:%M:%S")) * 1000)
            except (ValueError, OverflowError):
                at_ms = rows[-1]["t"]
            try:
                value = float(n.get("value")) if str(n.get("value") or "").strip() else None
            except ValueError:
                value = None
            new.append({"kind": n["kind"], "text": n["text"][:200], "key": (n.get("key") or "")[:30],
                        "value": value, "unit": (n.get("unit") or None), "at_ms": at_ms})
        upd["notes"] = (notes + new)[-NOTES_KEPT:]
        if out.get("topic"):
            upd["topic"] = out["topic"][:40]
        a = out.get("alert") or {}
        if (a.get("say") or "").strip() and a.get("why") != "none" and now - self.last_alert >= ALERT_S * 1000 and self.ask is not None:
            try:
                res = self.ask("debug_alert", {"text": a["say"][:200]})
                if (res or {}).get("result") == "queued":
                    self.last_alert = now
                    upd["alerts"] = (doc.get("alerts") or []) + [{"at_ms": now, "say": a["say"][:200], "why": a.get("why"),
                                                                  "refs": a.get("refs") or []}]
                    hello_log.emit("trigger.debug", by="mind", sum=f"调试提醒：{a['say'][:40]}",
                                   extra={"why": a.get("why"), "refs": a.get("refs")})
            except Exception as e:                # noqa: BLE001 - the ear away: the note is kept anyway
                self.stats["alert_failed"] += 1
                print(f"debug: alert not sent ({type(e).__name__})", flush=True)
        self.db[COLL].update_one({"_id": doc["_id"]}, {"$set": upd})
        return f"notes +{len(new)}"

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:                # noqa: BLE001 - one bad tick, the next one tries again
                print(f"debug worker: {type(e).__name__}: {str(e)[:160]}", flush=True)
            time.sleep(TICK_S)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self


def _current(db):
    """The open session, else the newest."""
    return db[COLL].find_one({"end_ms": None}, sort=[("start_ms", -1)]) or db[COLL].find_one({}, sort=[("start_ms", -1)])


def tools(db, env=None, store=None, settings_fn=None, clock_ms=None, run_json=None):
    from .tools import ToolError
    now_ms = clock_ms or (lambda: int(time.time() * 1000))
    run_json = run_json or model_calls.run_json

    def debug_log(args):
        doc = _current(db)
        if doc is None:
            raise ToolError("not_available", "还没有调试记录（说「进入调试模式」开始）")
        notes = doc.get("notes") or []
        q = (args.get("query") or "").strip()
        if q:
            ws = [w for w in re.split(r"\s+", q) if w]
            notes = [n for n in notes if any(w.lower() in (n.get("text", "") + " " + n.get("key", "")).lower() for w in ws)]
        end = doc.get("end_ms") or now_ms()
        rows = [{"at": _hms(n["at_ms"]), "kind": KIND_ZH.get(n["kind"], n["kind"]), "key": n.get("key"),
                 "text": n["text"], **({"value": n["value"], "unit": n.get("unit")} if n.get("value") is not None else {})}
                for n in notes[-30:] if isinstance(n.get("at_ms"), (int, float))]
        return {"topic": doc.get("topic"), "since": _hms(doc["start_ms"]), "minutes": round((end - doc["start_ms"]) / 60000),
                "on": doc.get("end_ms") is None, "counts": counters(doc.get("notes") or [], now_ms())["by_key"],
                "notes": rows, "alerts": [{"at": _hms(a["at_ms"]), "say": a["say"]} for a in (doc.get("alerts") or [])[-5:]],
                "summary": doc.get("summary"),
                "say": "按记录回答（时间、次数、读数照记录说）；记录里没有的就说没记到"}

    def debug_summary(args):
        doc = _current(db)
        if doc is None or not doc.get("notes"):
            raise ToolError("not_available", "这次调试还没记到什么")
        if env is None or store is None:
            raise ToolError("not_available", "总结用的模型没接上")
        now = now_ms()
        if pause.budget_stop(db, now):
            raise ToolError("budget", "今天的钱用到上限了")
        lines = "\n".join(f"[{_hms(n['at_ms'])}] {KIND_ZH.get(n['kind'], n['kind'])} {n.get('key') or ''}：{n['text']}"
                          for n in doc["notes"] if isinstance(n.get("at_ms"), (int, float)))
        schema = {"type": "object", "additionalProperties": False, "required": ["spoken", "tried", "open"],
                  "properties": {"spoken": {"type": "string"}, "tried": {"type": "array", "items": {"type": "string"}},
                                 "open": {"type": "array", "items": {"type": "string"}}}}
        out, _, err = run_json(env, store, settings_fn() if settings_fn else {}, "debug_summary", "debug-sum-v1", schema,
                               [{"role": "system", "content": "根据调试记录给主人做个总结。spoken：要说出口的总结，三四句口语，"
                                 "先说调的是什么、现在到哪一步，再说关键的发现；tried：试过的办法和结果，每条一句；open：还没解决"
                                 "的问题。只用记录里有的。只输出 JSON。"},
                                {"role": "user", "content": f"主题：{doc.get('topic') or '未知'}\n记录：\n{lines}"}], now)
        if out is None:
            raise ToolError("failed", f"总结没成功（{(err or '')[:30]}）")
        db[COLL].update_one({"_id": doc["_id"]}, {"$set": {"summary": {**out, "at_ms": now}}})
        return {"topic": doc.get("topic"), **out, "say": "照 spoken 说；主人要细节再说 tried / open"}

    return {"debug_log": debug_log, "debug_summary": debug_summary}
