"""mind tables in the hello database: mind_packages (versioned minute packages) and mind_state."""
import datetime
import time
import uuid
from zoneinfo import ZoneInfo

LANE = "live"
SOUND_SHORT = {"typing": "打字"}


def summarize(pkg):
    """One timeline line by rules, e.g. "说话 5 句（me 1）· 打字 3 · 脸：me"; at most 40 characters."""
    parts = []
    if pkg["speech"]:
        names = {}
        for x in pkg["speech"]:
            if x["who"]["kind"] == "known":
                names[x["who"]["name"]] = names.get(x["who"]["name"], 0) + 1
        who = "（" + "，".join(f"{n} {c}" for n, c in names.items()) + "）" if names else ""
        parts.append(f"说话 {len(pkg['speech'])} 句{who}")
    for g in pkg["sounds"]:
        if g["kind"] not in ("silence", "speech"):
            parts.append(f"{SOUND_SHORT.get(g['kind'], g['label'] or g['kind'])} {g['count']}")
    named = list(dict.fromkeys(f["name"] for f in pkg["faces"] if f["name"]))
    if named:
        parts.append("脸：" + "、".join(named))
    elif pkg["faces"]:
        parts.append(f"脸 {len(pkg['faces'])} 张未认出")
    text = ""
    for part in parts:                      # a full-width bracket already spaces itself: "）· "
        text += ("" if not text else ("· " if text.endswith("）") else " · ")) + part
    text = text or "无记录"
    return text if len(text) <= 40 else text[:39] + "…"


class Store:
    def __init__(self, db):
        self.db = db
        self.packages = db["mind_packages"]
        self.state_coll = db["mind_state"]

    def ensure_indexes(self):
        from_pymongo = getattr(self.packages, "create_index")
        from_pymongo([("lane", 1), ("minute", 1), ("version", 1)], unique=True)
        from_pymongo([("lane", 1), ("minute", 1)])
        db = self.db
        db["mind_scenes"].create_index([("lane", 1), ("minute", 1), ("version", 1)], unique=True)
        db["mind_runs"].create_index([("run_id", 1)], unique=True)
        db["mind_runs"].create_index([("at", 1)])
        db["mind_runs"].create_index([("lane", 1), ("minute", 1), ("step", 1)])
        db["mind_segments"].create_index([("lane", 1), ("start", 1), ("version", 1)], unique=True)
        db["mind_labels"].create_index([("at", 1)])
        db["mind_settings"].create_index([("lane", 1), ("version", 1)], unique=True)
        # One-button talk show (2026-11-13); conversations by start time: speech_context's scan was the slow part.
        db["say_rounds"].create_index([("rid", 1)], unique=True)
        db["say_rounds"].create_index([("scope_id", 1), ("at", 1)])
        db["owner_signals"].create_index([("sid", 1)], unique=True)
        db["owner_signals"].create_index([("at", 1)])
        db["owner_signals"].create_index([("refers_to.rid", 1)])
        db["owner_signals"].create_index([("source.device", 1), ("source.boot", 1), ("source.seq", 1)])
        db["conversations"].create_index([("start_ms", 1)])
        from .library import Library
        Library(db).ensure_indexes()
        from .mode_store import ensure_indexes as mode_indexes      # hello.mode_log: by time and by device input
        mode_indexes(db)

    def latest(self, lane, minute):
        return self.packages.find_one({"lane": lane, "minute": minute}, {"_id": 0}, sort=[("version", -1)])

    def versions(self, lane, minute):
        return [d["version"] for d in self.packages.find({"lane": lane, "minute": minute}, {"version": 1}).sort("version", 1)]

    def get(self, lane, minute, version):
        return self.packages.find_one({"lane": lane, "minute": minute, "version": version}, {"_id": 0})

    def put(self, lane, minute, pkg, strong):
        """Write a new version unless the latest one has the same package fingerprint and the same strong
        elements (settings or rule changes alter only the latter); returns the version or None."""
        prev = self.latest(lane, minute)
        if prev and prev["fingerprint"] == pkg["fingerprint"] and prev.get("strong") == strong:
            return None
        version = (prev["version"] + 1) if prev else 1
        self.packages.insert_one({"lane": lane, "minute": minute, "version": version, "builder": pkg["builder"],
                                  "fingerprint": pkg["fingerprint"], "package": pkg, "strong": strong,
                                  "summary": summarize(pkg), "built_at": int(time.time() * 1000)})
        return version

    def segment_rows(self, lane, from_ms, to_ms):
        """Light rows for cutting segments: latest version per minute, owner state and coverage only."""
        latest = {}
        for d in self.packages.find({"lane": lane, "minute": {"$gte": from_ms, "$lt": to_ms}},
                                    {"_id": 0, "minute": 1, "version": 1, "strong.owner.state": 1, "strong.coverage": 1}):
            if d["minute"] not in latest or d["version"] > latest[d["minute"]]["version"]:
                latest[d["minute"]] = d
        return [{"minute": m, "owner_state": latest[m]["strong"]["owner"]["state"],
                 "hearing_s": latest[m]["strong"]["coverage"]["hearing_s"], "vision_s": latest[m]["strong"]["coverage"]["vision_s"]}
                for m in sorted(latest)]

    def count_model_scenes(self, lane, from_ms, to_ms):
        return len({d["minute"] for d in self.db["mind_scenes"].find(
            {"lane": lane, "minute": {"$gte": from_ms, "$lt": to_ms}, "source": "model"}, {"minute": 1})})

    TIMELINE_FIELDS = {"_id": 0, "minute": 1, "version": 1, "summary": 1, "strong.owner.state": 1, "strong.coverage": 1,
                       "package.speech.code": 1, "package.tingwu_only.code": 1, "package.self_speech.code": 1,
                       "package.sounds.count": 1, "package.faces.name": 1}

    def timeline(self, lane, from_ms, to_ms):
        latest = {}
        for d in self.packages.find({"lane": lane, "minute": {"$gte": from_ms, "$lt": to_ms}}, self.TIMELINE_FIELDS):
            if d["minute"] not in latest or d["version"] > latest[d["minute"]]["version"]:
                latest[d["minute"]] = d
        rows = []
        for minute in sorted(latest):
            d = latest[minute]
            p, st = d["package"], d["strong"]
            rows.append({"minute": minute, "version": d["version"], "owner_state": st["owner"]["state"],
                         "coverage": st["coverage"], "summary": d["summary"],
                         "counts": {"speech": len(p["speech"]), "tingwu_only": len(p["tingwu_only"]),
                                    "self": len(p["self_speech"]), "sounds": sum(g["count"] for g in p["sounds"]),
                                    "faces": len(p["faces"])},
                         "faces": list(dict.fromkeys(f["name"] for f in p["faces"] if f["name"]))})
        return rows

    def state(self):
        doc = self.state_coll.find_one({"_id": LANE}) or {}
        doc.pop("_id", None)
        return doc

    def enabled(self):
        """The mind switch (the page's 情景 switch). Off: packages are still built, no model call is made."""
        return self.state().get("enabled") is not False

    def set_state(self, **fields):
        self.state_coll.update_one({"_id": LANE}, {"$set": fields}, upsert=True)

    # ---- phase 1: scenes and model runs ----
    def put_scene(self, lane, minute, doc):
        prev = self.db["mind_scenes"].find_one({"lane": lane, "minute": minute}, {"version": 1}, sort=[("version", -1)])
        version = (prev["version"] + 1) if prev else 1
        self.db["mind_scenes"].insert_one({**doc, "lane": lane, "minute": minute, "version": version,
                                           "built_at": int(time.time() * 1000)})
        return version

    def latest_scene(self, lane, minute):
        return self.db["mind_scenes"].find_one({"lane": lane, "minute": minute}, {"_id": 0}, sort=[("version", -1)])

    def scene_versions(self, lane, minute):
        return list(self.db["mind_scenes"].find({"lane": lane, "minute": minute}, {"_id": 0}).sort("version", 1))

    def last_scene_before(self, lane, minute):
        for d in self.db["mind_scenes"].find({"lane": lane, "minute": {"$lt": minute}}, {"_id": 0}) \
                .sort([("minute", -1), ("version", -1)]).limit(1):
            return d
        return None

    def latest_scene_any(self, lane):
        for d in self.db["mind_scenes"].find({"lane": lane}, {"_id": 0}).sort([("minute", -1), ("version", -1)]).limit(1):
            return d
        return None

    def put_run(self, doc):
        self._run_seq = getattr(self, "_run_seq", 0) + 1
        run_id = f"r{int(time.time() * 1000)}-{uuid.uuid4().hex}"
        self.db["mind_runs"].insert_one({**doc, "run_id": run_id})
        return run_id

    def get_run(self, run_id):
        return self.db["mind_runs"].find_one({"run_id": run_id}, {"_id": 0})

    def cost_today(self, tz, now_ms):
        day = datetime.datetime.fromtimestamp(now_ms / 1000, ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int(day.timestamp() * 1000)
        return sum(d.get("cost_usd") or 0 for d in self.db["mind_runs"].find({"at": {"$gte": start, "$lt": start + 86400_000}},
                                                                               {"cost_usd": 1}))

    def place_at(self, ms):
        """The owner's latest place report at or before ms (phase 1: reported on the page, not Wi-Fi)."""
        for d in self.db["mind_labels"].find({"kind": "place", "at": {"$lte": ms}}, {"_id": 0}).sort("at", -1).limit(1):
            return d
        return None

    def scenes_range(self, lane, from_ms, to_ms):
        latest = {}
        for d in self.db["mind_scenes"].find({"lane": lane, "minute": {"$gte": from_ms, "$lt": to_ms}}, {"_id": 0}):
            if d["minute"] not in latest or d["version"] > latest[d["minute"]]["version"]:
                latest[d["minute"]] = d
        return [latest[m] for m in sorted(latest)]

    # ---- segments ----
    def segment_summary(self, lane, start):
        return self.db["mind_segments"].find_one({"lane": lane, "start": start}, {"_id": 0}, sort=[("version", -1)])

    def put_segment_summary(self, lane, start, end, summary, run_id, scenes_count, at):
        prev = self.segment_summary(lane, start)
        self.db["mind_segments"].insert_one({"lane": lane, "start": start, "end": end, "version": (prev["version"] + 1) if prev else 1,
                                             "summary": summary, "run_id": run_id, "scenes_count": scenes_count, "at": at})
