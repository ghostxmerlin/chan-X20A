"""Raw records for a time window, read-only, with the wall time each record is filed under.

speech / tingwu / sounds carry `pos` (16 kHz sample offsets in the session audio): their wall time
is session start + pos[0] / 16 ms, cross-checked against `at`. vision rows carry `at` already.
"""
AUDIO_COLLECTIONS = ("speech", "tingwu", "sounds")
TIME_FIELD = {"speech": "t", "tingwu": "at", "sounds": "t"}   # indexed epoch-ms fields; sounds rows have no `at`
SLACK_MS = 300_000            # `at` may differ from the wall time by seconds; query wider, then filter


def wall_ms(doc, session_start_ms):
    """(wall time used to file the record, at − wall in ms or None)."""
    pos = doc.get("pos")
    at = doc.get("at", doc.get("t"))
    if session_start_ms is not None and isinstance(pos, (list, tuple)) and pos and isinstance(pos[0], (int, float)):
        wall = int(session_start_ms + pos[0] / 16)
        return wall, (int(at - wall) if isinstance(at, (int, float)) else None)
    return int(at), None


class Records:
    def __init__(self, db):
        self.db = db

    def sessions(self, start_ms, end_ms):
        flt = {"start_ms": {"$lt": end_ms}, "$or": [{"end_ms": {"$exists": False}}, {"end_ms": {"$gt": start_ms}}]}
        return list(self.db["sessions"].find(flt, {"_id": 0}).sort("start_ms", 1))

    def window(self, start_ms, end_ms):
        out = {"sessions": self.sessions(start_ms, end_ms)}
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
                d["_wall"], d["_drift"] = wall_ms(d, starts.get(d.get("session")))
                if start_ms <= d["_wall"] < end_ms:
                    kept.append(d)
            kept.sort(key=lambda d: d["_wall"])
            out[name] = kept
        vision = list(self.db["vision"].find({"at": {"$gte": start_ms, "$lt": end_ms}}, {"_id": 0}).sort("at", 1))
        for d in vision:
            d["_wall"], d["_drift"] = int(d["at"]), None
        out["vision"] = vision
        return out
