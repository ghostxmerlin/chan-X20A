"""Raw records for a time window, read-only, with the wall time each record is filed under.

speech and sounds are filed by the ear's own clock (`t`, ≈ start of the utterance); session start +
pos[0] / 16 ms is only a cross-check, since pos falls behind real time when the robot's audio had
gaps (the drift warning then means "audio gaps before this point"). tingwu rows are filed by pos:
their `at` is when the result arrived, seconds after the sentence. vision rows carry `at` already.
"""
AUDIO_COLLECTIONS = ("speech", "tingwu", "sounds")
TIME_FIELD = {"speech": "t", "tingwu": "at", "sounds": "t"}   # indexed epoch-ms fields; sounds rows have no `at`
SLACK_MS = 300_000            # pos-filed rows may sit up to minutes from `at`; query wider, then filter


def wall_ms(doc, session_start_ms, collection="speech"):
    """(wall time used to file the record, clock − pos time in ms, or None)."""
    pos = doc.get("pos")
    clock = doc.get(TIME_FIELD.get(collection, "at"), doc.get("at", doc.get("t")))
    pos_wall = None
    if session_start_ms is not None and isinstance(pos, (list, tuple)) and pos and isinstance(pos[0], (int, float)):
        pos_wall = int(session_start_ms + pos[0] / 16)
    if collection == "tingwu":
        return (pos_wall if pos_wall is not None else int(clock)), None
    return int(clock), (int(clock - pos_wall) if pos_wall is not None else None)


class Records:
    def __init__(self, db):
        self.db = db

    def sessions(self, start_ms, end_ms):
        """Sessions overlapping the window. An open session (no end_ms: the ear restarted without closing
        it) that a newer session of the same source and kind replaced ends where the newer one starts
        (end_estimated); only the newest open one per source and kind may still be running."""
        flt = {"start_ms": {"$lt": end_ms}, "$or": [{"end_ms": {"$exists": False}}, {"end_ms": {"$gt": start_ms}}]}
        out = []
        for s in self.db["sessions"].find(flt, {"_id": 0}).sort("start_ms", 1):
            if s.get("end_ms") is None:
                newer = [o for o in self.db["sessions"].find({"source": s.get("source"), "start_ms": {"$gt": s.get("start_ms", 0)}},
                                                              {"_id": 0, "start_ms": 1, "kind": 1})
                         if o.get("kind") == s.get("kind")]
                if newer:
                    s["end_ms"], s["end_estimated"] = min(o["start_ms"] for o in newer), True
                    if s["end_ms"] <= start_ms:
                        continue
            out.append(s)
        return out

    def window(self, start_ms, end_ms, vision_start_ms=None):
        """Records in [start_ms, end_ms); vision (and sessions) may reach further back."""
        vstart = start_ms if vision_start_ms is None else min(start_ms, vision_start_ms)
        out = {"sessions": self.sessions(vstart, end_ms)}
        starts = {}
        for name in AUDIO_COLLECTIONS:
            field = TIME_FIELD[name]
            docs = list(self.db[name].find({field: {"$gte": start_ms - SLACK_MS, "$lt": end_ms + SLACK_MS}}, {"_id": 0}))
            missing = sorted({d["session"] for d in docs if d.get("session") not in starts})
            if missing:
                for s in self.db["sessions"].find({"session": {"$in": missing}}, {"_id": 0, "session": 1, "start_ms": 1}):
                    starts[s["session"]] = s.get("start_ms")
            kept = []
            for d in docs:
                d["_wall"], d["_drift"] = wall_ms(d, starts.get(d.get("session")), name)
                if start_ms <= d["_wall"] < end_ms:
                    kept.append(d)
            kept.sort(key=lambda d: d["_wall"])
            out[name] = kept
        vision = list(self.db["vision"].find({"at": {"$gte": vstart, "$lt": end_ms}}, {"_id": 0}).sort("at", 1))
        for d in vision:
            d["_wall"], d["_drift"] = int(d["at"]), None
        out["vision"] = vision
        out["eye"] = list(self.db["eye_minutes"].find({"minute": {"$gte": start_ms, "$lt": end_ms}}, {"_id": 0}))
        return out
