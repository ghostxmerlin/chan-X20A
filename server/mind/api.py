"""HTTP API of mind on the server's loopback only (the Mac reaches it over ssh). Every call needs
"Authorization: Bearer <token>". POST bodies: application/json, at most 16 KB. POSTs write only
mind tables (settings versions, labels/marks, rerun lane) — never raw records, never the live
scenes. Sentence text only leaves through this API; nothing is logged.

GET  /state /timeline /minute /now /segments /scene /settings /names /prompts /labels /stats /switches /frames/latest /eye/latest /eye /notes /roles /utterances /voices
POST /settings /mark /label /rerun /place /switch /switches /say /say/stop /voice/try
"""
import base64
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import fusion, prompts, segments, settings as settings_mod, speak
from .settings_store import MODELS
from .store import LANE

MAX_RANGE_MS = 24 * 3600_000
MAX_BODY = 16 * 1024
MARKS = ("arrive", "leave", "meeting", "others_near", "alone")
VERDICTS = ("right", "wrong", "unsure")
FIELDS = ("owner", "people", "activity", "topic", "summary", "other")


class BadRequest(Exception):
    pass


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def _int(q, name, required=True):
    values = q.get(name)
    if not values:
        if required:
            raise BadRequest(f"{name} required")
        return None
    try:
        return int(values[0])
    except ValueError:
        raise BadRequest(f"{name} must be an integer") from None


def _range(q):
    lo, hi = _int(q, "from"), _int(q, "to")
    if hi <= lo or hi - lo > MAX_RANGE_MS:
        raise BadRequest("range must be positive and at most 24 h")
    return lo, hi


def _str(v, n):
    return str(v)[:n] if v is not None else ""


def make_server(store, token, host="127.0.0.1", port=52740, ctx=None):
    ctx = ctx or {}
    db = store.db

    def current_settings():
        ss = ctx.get("settings_store")
        return ss.current() if ss else (settings_mod.load(None), 0)

    def names():
        voice, face = [], []
        for d in db["speech"].find({"speaker.band": "known"}, {"speaker.name": 1}).limit(5000):
            n = (d.get("speaker") or {}).get("name")
            if n and n not in voice:
                voice.append(n)
        for d in db["vision"].find({"name": {"$exists": True}}, {"name": 1}).limit(5000):
            if d.get("name") and d["name"] not in face:
                face.append(d["name"])
        return {"voice": voice[:50], "face": face[:50]}

    def stats(days):
        s, _ = current_settings()
        now = int(time.time() * 1000)
        start = now - days * 86400_000
        rows = {}
        for d in db["mind_runs"].find({"at": {"$gte": start}}, {"at": 1, "step": 1, "model": 1, "usage": 1, "cost_usd": 1, "error": 1, "lane": 1}):
            day = settings_mod.local_time(d["at"], s["tz"]).strftime("%Y-%m-%d")
            key = (day, d.get("step"), d.get("model"), d.get("lane"))
            r = rows.setdefault(key, {"day": day, "step": d.get("step"), "model": d.get("model"), "lane": d.get("lane"), "calls": 0,
                                      "failures": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
            u = d.get("usage") or {}
            r["calls"] += 1
            r["failures"] += 1 if d.get("error") else 0
            r["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
            r["completion_tokens"] += u.get("completion_tokens", 0) or 0
            r["cost_usd"] = round(r["cost_usd"] + (d.get("cost_usd") or 0), 6)
        out = sorted(rows.values(), key=lambda r: (r["day"], r["step"] or "", r["model"] or ""), reverse=True)
        today = settings_mod.local_time(now, s["tz"]).strftime("%Y-%m-%d")
        return {"days": days, "today": today, "rows": out,
                "today_cost_usd": round(sum(r["cost_usd"] for r in out if r["day"] == today), 6),
                "today_calls": sum(r["calls"] for r in out if r["day"] == today)}

    SWITCHES = ("tingwu", "eye", "notes")    # spec 2026-11-11 §3
    MOUTH_MODES = ("off", "clips", "tts")    # 嘴: emotion clips (default) or soul-tts, never both (user 2026-11-11)

    def mouth_mode():
        m = (db["switches"].find_one({"_id": "mouth"}) or {}).get("mode")
        return m if m in MOUTH_MODES else "clips"

    def switches():
        now = int(time.time() * 1000)
        out = {}
        for name in SWITCHES:
            sw = db["switches"].find_one({"_id": name}) or {}
            st = db["switches"].find_one({"_id": name + "_status"}) or {}
            out[name] = {"on": sw.get("on") is True, "at": sw.get("at")}
            out[name + "_status"] = {"running": st.get("running") or [], "available": st.get("available"), "at": st.get("at"),
                                     "stale": not isinstance(st.get("at"), int) or now - st["at"] > 90_000}
        out["mouth"] = {"mode": mouth_mode(), "at": (db["switches"].find_one({"_id": "mouth"}) or {}).get("at")}
        return out

    def latest_frame():
        """Newest body-camera frame the ear stored (hello.frames + the file under recordings/)."""
        doc = db["frames"].find_one({}, sort=[("at", -1)])
        if not doc:
            raise NotFound("no frames yet")
        now = int(time.time() * 1000)
        tz = current_settings()[0]["tz"]
        day0 = int(settings_mod.local_time(now, tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        out = {k: doc.get(k) for k in ("at", "session", "seq", "diff", "bytes", "w", "h")}
        out["today"] = db["frames"].count_documents({"at": {"$gte": day0}})
        out["jpeg_b64"], out["missing"] = None, True
        root = ctx.get("recordings_root")
        if root and isinstance(doc.get("path"), str):
            base = os.path.realpath(root)
            full = os.path.realpath(os.path.join(base, doc["path"]))
            if full.startswith(base + os.sep) and os.path.isfile(full):
                with open(full, "rb") as f:
                    out["jpeg_b64"], out["missing"] = base64.b64encode(f.read()).decode(), False
        return out

    def get(path, q):
        if path == "/state":
            return {**store.state(), "enabled": store.enabled(), "lane": LANE, "now_ms": int(time.time() * 1000)}
        if path == "/timeline":
            return store.timeline(LANE, *_range(q))
        if path == "/minute":
            t, version = _int(q, "t"), _int(q, "version", required=False)
            doc = store.get(LANE, t, version) if version is not None else store.latest(LANE, t)
            if not doc:
                raise NotFound("no such minute")
            return {**doc, "versions": store.versions(LANE, t)}
        if path == "/now":
            s, _ = current_settings()
            scene = store.latest_scene_any(LANE)
            pkg = store.latest(LANE, scene["minute"]) if scene else None
            now = int(time.time() * 1000)
            segs = segments.update(None, store, s, now - 24 * 3600_000 if not scene else scene["minute"] - 12 * 3600_000, now,
                                   LANE, summarize_now=False)
            return {"minute": scene["minute"] if scene else None, "scene": scene, "strong": pkg["strong"] if pkg else None,
                    "conv": (pkg["package"].get("conv") if pkg else None),
                    "segment": segs[-1] if segs else None, "state": {**store.state(), "enabled": store.enabled()}}
        if path == "/segments":
            s, _ = current_settings()
            lo, hi = _range(q)
            return segments.update(None, store, s, lo, hi, LANE, summarize_now=False)
        if path == "/scene":
            t = _int(q, "t")
            lane = (q.get("lane") or [LANE])[0]
            if lane not in (LANE, "rerun"):
                raise BadRequest("lane must be live or rerun")
            versions = store.scene_versions(lane, t)
            if not versions:
                raise NotFound("no scene for this minute")
            runs = [store.get_run(v["run_id"]) for v in versions if v.get("run_id")]
            return {"versions": versions, "runs": [r for r in runs if r]}
        if path == "/settings":
            s, v = current_settings()
            ss = ctx.get("settings_store")
            return {"settings": s, "version": v, "history": ss.history() if ss else []}
        if path == "/names":
            return names()
        if path == "/prompts":
            return {"versions": prompts.OFFERED, "models": list(MODELS)}
        if path == "/labels":
            lo, hi = _range(q)
            return list(db["mind_labels"].find({"at": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("at", 1))
        if path == "/switches":
            return switches()
        if path == "/frames/latest":
            return latest_frame()
        if path == "/eye/latest":
            doc = db["eye_minutes"].find_one({}, {"_id": 0}, sort=[("minute", -1), ("version", -1)])
            if not doc:
                raise NotFound("no eye result yet")
            return doc
        if path == "/roles":
            return [{k: r.get(k) for k in ("id", "button", "name", "voice")} for r in speak.load_roles(ctx.get("roles_dir") or "").values()]
        if path == "/voices":
            tts = ctx.get("tts")
            if not tts:
                raise Conflict("soul-tts not configured")
            return speak.voice_list(tts)
        if path == "/utterances":
            lo, hi = _range(q)
            return list(db["utterances"].find({"at": {"$gte": lo, "$lt": hi}}, {"_id": 0, "context": 0}).sort("at", 1).limit(200))
        if path == "/notes":
            lo, hi = _range(q)
            return list(db["conversations"].find({"start_ms": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("start_ms", 1).limit(500))
        if path == "/eye":
            lo, hi = _range(q)
            return list(db["eye_minutes"].find({"minute": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("minute", 1).limit(1440))
        if path == "/stats":
            days = _int(q, "days", required=False) or 7
            return stats(max(1, min(days, 3650)))
        raise NotFound("not found")

    def post(path, body):
        now = int(time.time() * 1000)
        if path == "/settings":
            ss = ctx.get("settings_store")
            if not ss:
                raise BadRequest("settings are not configurable here")
            try:
                return {"version": ss.save(body.get("settings"), _str(body.get("note"), 200))}
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == "/say":
            tts = ctx.get("tts")
            if not tts or not ctx.get("env"):
                raise Conflict("soul-tts or model access not configured")
            roles = speak.load_roles(ctx.get("roles_dir") or "")
            if body.get("role") not in roles:
                raise BadRequest(f"role must be one of {', '.join(sorted(roles))}")
            if mouth_mode() != "tts":
                raise Conflict("嘴没切到 TTS 模式")
            s, _ = current_settings()
            try:
                rec = speak.say(ctx["env"], store, s, roles, body["role"], tts, now)
            except speak.NotConnected as e:
                raise Conflict(str(e)) from None
            return {k: v for k, v in rec.items() if k != "context"}
        if path == "/voice/try":
            tts = ctx.get("tts")
            if not tts:
                raise Conflict("soul-tts not configured")
            if not isinstance(body.get("voice"), str) or body["voice"] not in {v["id"] for v in speak.voice_list(tts)}:
                raise BadRequest("voice must be one of the soul-tts voices")
            if mouth_mode() != "tts":
                raise Conflict("嘴没切到 TTS 模式")
            try:
                rec = speak.try_voice(store, tts, body["voice"], now)
            except speak.NotConnected as e:
                raise Conflict(str(e)) from None
            return {k: v for k, v in rec.items() if k != "_id"}
        if path == "/say/stop":
            tts = ctx.get("tts")
            return {"cancelled": tts.stop() if tts else []}
        if path == "/switches" and body.get("name") == "mouth":
            if body.get("mode") not in MOUTH_MODES:
                raise BadRequest(f"mouth mode must be one of {'/'.join(MOUTH_MODES)}")
            was = mouth_mode()
            db["switches"].update_one({"_id": "mouth"}, {"$set": {"mode": body["mode"], "at": now, "by": "page"}}, upsert=True)
            if was == "tts" and body["mode"] != "tts" and ctx.get("tts"):
                try:
                    ctx["tts"].stop()                    # leaving TTS: stop what is still playing
                except Exception:                        # noqa: BLE001 - the switch itself is saved
                    pass
            return switches()
        if path == "/switches":
            if body.get("name") not in SWITCHES or not isinstance(body.get("on"), bool):
                raise BadRequest(f"name ({'/'.join(SWITCHES)}) and on (true/false) required")
            db["switches"].update_one({"_id": body["name"]}, {"$set": {"on": body["on"], "at": now, "by": "page"}}, upsert=True)
            return switches()
        if path == "/switch":
            if not isinstance(body.get("enabled"), bool):
                raise BadRequest("enabled (true/false) required")
            store.set_state(enabled=body["enabled"], switched_at=now)
            return {"enabled": store.enabled(), "at": now}
        if path == "/mark":
            if body.get("kind") not in MARKS:
                raise BadRequest(f"kind must be one of {', '.join(MARKS)}")
            db["mind_labels"].insert_one({"lane": LANE, "kind": "mark", "mark": body["kind"], "at": now, "note": _str(body.get("note"), 300)})
            return {"ok": True, "at": now}
        if path == "/place":
            label = body.get("label")
            if not isinstance(label, str) or not (1 <= len(label.strip()) <= 40):
                raise BadRequest("label: 1–40 characters")
            db["mind_labels"].insert_one({"lane": LANE, "kind": "place", "label": label.strip(), "at": now})
            return {"ok": True, "at": now}
        if path == "/label":
            minute = body.get("minute")
            if not isinstance(minute, int) or body.get("verdict") not in VERDICTS or body.get("field") not in FIELDS:
                raise BadRequest(f"minute (int), verdict ({'/'.join(VERDICTS)}) and field ({'/'.join(FIELDS)}) required")
            scene = store.latest_scene(LANE, minute)
            if not scene:
                raise NotFound("no live scene for this minute")
            db["mind_labels"].insert_one({"lane": LANE, "kind": "label", "minute": minute, "scene_version": scene["version"],
                                          "verdict": body["verdict"], "field": body["field"], "correct": _str(body.get("correct"), 300),
                                          "note": _str(body.get("note"), 300), "at": now})
            return {"ok": True}
        if path == "/rerun":
            minute, prompt, model = body.get("minute"), body.get("prompt"), body.get("model")
            if not isinstance(minute, int) or prompt not in prompts.OFFERED or model not in MODELS:
                raise BadRequest("minute (int), prompt (known version) and model (known model) required")
            if not ctx.get("env"):
                raise BadRequest("no model access configured")
            if not store.enabled():
                raise Conflict("mind is switched off: no model calls")
            doc = store.latest(LANE, minute)
            if not doc:
                raise NotFound("no package for this minute")
            s, v = current_settings()
            live = store.latest_scene(LANE, minute)
            ss = ctx.get("settings_store")
            if live and ss and live.get("settings_version") is not None and ss.get(live["settings_version"]):
                v = live["settings_version"]          # same inputs as the live run: only prompt / model differ
                s = ss.get(v)
            segs = segments.update(None, store, s, minute - 12 * 3600_000, minute, LANE, summarize_now=False)
            titled = [{"start": g["start"], "end": g["end"], "cls": g["cls"], "title": (g.get("summary") or {}).get("title")}
                      for g in segs if g["cls"] != "gap" and g["start"] < minute]
            prev = store.latest(LANE, minute - 60_000)
            return fusion.run_minute(ctx["env"], store, s, v, minute, doc["package"], doc["strong"],
                                     {"prev": prev["package"] if prev else None}, lane="rerun",
                                     force_prompt=prompt, force_model=model, now_ms=now, segments=titled,
                                     package_version=doc["version"])
        raise NotFound("not found")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            supplied = self.headers.get("Authorization", "")
            supplied = supplied[7:] if supplied.startswith("Bearer ") else ""
            return bool(token) and hmac.compare_digest(supplied.encode(), token.encode())

        def _run(self, fn):
            try:
                self._send(200, fn())
            except BadRequest as e:
                self._send(400, {"error": str(e)})
            except NotFound as e:
                self._send(404, {"error": str(e)})
            except Conflict as e:
                self._send(409, {"error": str(e)})
            except Exception as e:               # noqa: BLE001 - database trouble shows as 503
                self._send(503, {"error": f"{type(e).__name__}"})

        def do_GET(self):
            if not self._authorized():
                self._send(401, {"error": "bad token"})
                return
            url = urlsplit(self.path)
            self._run(lambda: get(url.path, parse_qs(url.query)))

        def do_POST(self):
            if not self._authorized():
                self._send(401, {"error": "bad token"})
                return
            url = urlsplit(self.path)
            if url.path not in ("/settings", "/mark", "/label", "/rerun", "/place", "/switch", "/switches", "/say", "/say/stop", "/voice/try"):
                if url.path in ("/state", "/timeline", "/minute", "/now", "/segments", "/scene", "/names", "/prompts", "/labels", "/stats"):
                    self._send(405, {"error": "read only"})
                else:
                    self._send(404, {"error": "not found"})
                return
            if not (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"):
                self._send(415, {"error": "application/json only"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413, {"error": "body too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "bad json"})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "body must be an object"})
                return
            self._run(lambda: post(url.path, body))

        def do_PUT(self):
            self._send(405, {"error": "use POST"})

        do_DELETE = do_PATCH = do_PUT

    return ThreadingHTTPServer((host, port), Handler)


def serve(store, token, host="127.0.0.1", port=52740, ctx=None):
    make_server(store, token, host, port, ctx).serve_forever()
