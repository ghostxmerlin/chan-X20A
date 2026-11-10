"""mind tables in the hello database: mind_packages (versioned minute packages) and mind_state."""
import time

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

    def timeline(self, lane, from_ms, to_ms):
        latest = {}
        for d in self.packages.find({"lane": lane, "minute": {"$gte": from_ms, "$lt": to_ms}}, {"_id": 0}):
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

    def set_state(self, **fields):
        self.state_coll.update_one({"_id": LANE}, {"$set": fields}, upsert=True)
