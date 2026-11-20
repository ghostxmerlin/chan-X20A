"""图库 reads (2026-11-20): the body camera's photos in hello.frames (files under recordings_root) browsed by day / hour /
minute, with what the mind wrote about each one: the 看一眼 text (hello.vision_looks, same path), the minute's eye
description (hello.eye_minutes) and the minute's scene (hello.mind_scenes, live lane). Read side only; no writes here."""
import base64
import datetime
import os

try:
    from bson import ObjectId
except ImportError:                                       # pragma: no cover - bson ships with pymongo
    ObjectId = None

MAX_SPAN_MS = 3600_000
MAX_IMAGES = 60
LANE = "live"
_SKIP_KEYS = {"key", "op", "dimension", "confidence"}


def oid(s):
    """24 hex characters -> bson.ObjectId; anything else is returned as it is (FakeDB tests use string ids)."""
    if ObjectId is not None and isinstance(s, str) and len(s) == 24:
        try:
            return ObjectId(s)
        except Exception:
            return s
    return s


def _ids(ids):
    return {"$in": [oid(i) for i in ids] + list(ids)}


def texts(obj):
    """Every string leaf of a nested eye result / scene, depth first, skipping id-like keys and strings under 2 chars."""
    out = []

    def walk(v):
        if isinstance(v, str):
            if len(v.strip()) >= 2:
                out.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in _SKIP_KEYS:
                    walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(obj)
    return out


def _minute(at):
    return at // 60000 * 60000


def _local(at, tz):
    return datetime.datetime.fromtimestamp(at / 1000, tz)


def days(db, tz):
    counts = {}
    for d in db["frames"].find({"at": {"$exists": True}}, {"at": 1}):
        if isinstance(d.get("at"), (int, float)):
            day = _local(d["at"], tz).strftime("%Y-%m-%d")
            counts[day] = counts.get(day, 0) + 1
    return [{"day": k, "n": counts[k]} for k in sorted(counts, reverse=True)]


def hours(db, day, tz):
    try:
        start = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz)
    except (ValueError, TypeError):
        raise ValueError("day must be YYYY-MM-DD")
    lo = int(start.timestamp() * 1000)
    hi = int((start + datetime.timedelta(days=1)).timestamp() * 1000)
    counts = {}
    for d in db["frames"].find({"at": {"$gte": lo, "$lt": hi}}, {"at": 1}):
        h = _local(d["at"], tz).hour
        counts[h] = counts.get(h, 0) + 1
    return [{"hour": h, "n": counts[h]} for h in sorted(counts)]


def frames(db, lo, hi):
    if not isinstance(lo, int) or not isinstance(hi, int) or hi < lo or hi - lo > MAX_SPAN_MS:
        raise ValueError("range must be at most 1 hour")
    rows = sorted(db["frames"].find({"at": {"$gte": lo, "$lt": hi}}), key=lambda d: d["at"])
    if not rows:
        return []
    paths = [r["path"] for r in rows if r.get("path")]
    minutes = sorted({_minute(r["at"]) for r in rows})
    looked = {d.get("path") for d in db["vision_looks"].find({"kind": "look", "path": {"$in": paths}})}
    eyed = {d["minute"] for d in db["eye_minutes"].find({"minute": {"$in": minutes}, "result": {"$ne": None}})}
    scened = {d["minute"] for d in db["mind_scenes"].find({"minute": {"$in": minutes}, "lane": LANE})}
    return [{"id": str(r["_id"]), "at": r["at"], "session": r.get("session"), "snap": "snap" in r,
             "look": r.get("path") in looked, "eye": _minute(r["at"]) in eyed, "scene": _minute(r["at"]) in scened}
            for r in rows]


def read_file(root, rel):
    """A file under root (realpath-checked) -> bytes, or None."""
    if not root or not isinstance(rel, str) or not rel:
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return None
    with open(full, "rb") as f:
        return f.read()


def _b64(root, rel):
    data = read_file(root, rel)
    return None if data is None else base64.b64encode(data).decode()


def images(db, root, ids):
    if not isinstance(ids, list) or len(ids) > MAX_IMAGES:
        raise ValueError(f"at most {MAX_IMAGES} ids")
    ids = [str(i) for i in ids]
    paths = {str(d["_id"]): d.get("path") for d in db["frames"].find({"_id": _ids(ids)})}
    return {i: _b64(root, paths.get(i)) for i in ids}


def _latest(rows):
    rows = sorted(rows, key=lambda d: d.get("version") or 0)
    return rows[-1] if rows else None


def detail(db, root, id):
    doc = db["frames"].find_one({"_id": _ids([str(id)])})
    if not doc:
        return None
    at, minute = doc["at"], _minute(doc["at"])
    looks = sorted(db["vision_looks"].find({"kind": "look", "path": doc.get("path")}), key=lambda d: d.get("at") or 0) \
        if doc.get("path") else []
    eye = _latest([d for d in db["eye_minutes"].find({"minute": minute}) if d.get("result")])
    scene = _latest([d for d in db["mind_scenes"].find({"minute": minute, "lane": LANE}) if d.get("scene")])
    return {"id": str(doc["_id"]), "at": at, "session": doc.get("session"), "snap": "snap" in doc,
            "jpeg_b64": _b64(root, doc.get("path")),
            "looks": [{"at": d.get("at"), "text": d.get("text")} for d in looks],
            "eye": {"minute": minute, "text": "\n".join(texts(eye["result"]))} if eye else None,
            "scene": {"minute": minute, "text": "\n".join(texts(scene["scene"]))} if scene else None}
