"""图库 reads (2026-11-20): the body camera's photos in hello.frames (files under recordings_root) browsed by day / hour /
minute, with what the mind wrote about each one: the 看一眼 text (hello.vision_looks, same path), the minute's eye
description (hello.eye_minutes) and the minute's scene (hello.mind_scenes, live lane).

The only writes: plan / delete, the owner's complete, irreversible delete of chosen photos (docs/superpowers/specs/
2026-11-20-photo-library-design.md): the photo, every record written from it or its minute, its run log rows and
their bodies, and the sentences derived from it wherever else they were copied (other log bodies, the log spool)."""
import base64
import datetime
import hashlib
import json
import os
import re

from . import gallery_redact as redact
from .gallery_redact import MARK, REGEX_BYTES  # noqa: F401 - the page's mark; tests
from .runlog import hello_log

try:
    from bson import ObjectId
except ImportError:                                       # pragma: no cover - bson ships with pymongo
    ObjectId = None

MAX_SPAN_MS = 3600_000
MAX_IMAGES = 60
LANE = "live"
LOOK_KINDS = ("look", "scene")        # vision_looks rows read from one photo: 看一眼 and the live eye's 实时识别
# texts(): keys whose values are ids, codes or machine settings, never a description of the picture (review I8)
_SKIP_KEYS = {"key", "op", "dimension", "confidence", "kind", "lane", "id", "_id", "code", "source", "model", "prompt",
              "version", "who_id", "subject_id", "run_id"}
_CODE = re.compile(r"^[A-Za-z0-9_.:-]+$")


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
    """The descriptive string leaves of a nested eye result / scene, depth first: strings of at least 2 characters
    that are not under an id / code key (_SKIP_KEYS) and are not code-like themselves (only ASCII letters, digits and
    _.:- : ids, names like "me", enum values)."""
    out = []

    def walk(v):
        if isinstance(v, str):
            if len(v.strip()) >= 2 and not _CODE.match(v.strip()):
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
    looked = {d.get("path") for d in db["vision_looks"].find({"kind": {"$in": list(LOOK_KINDS)}, "path": {"$in": paths}})}
    eyed = {d["minute"] for d in db["eye_minutes"].find({"minute": {"$in": minutes}, "result": {"$ne": None}})}
    scened = {d["minute"] for d in db["mind_scenes"].find({"minute": {"$in": minutes}, "lane": LANE})}
    return [{"id": str(r["_id"]), "at": r["at"], "session": r.get("session"), "snap": "snap" in r,
             "look": r.get("path") in looked, "eye": _minute(r["at"]) in eyed, "scene": _minute(r["at"]) in scened}
            for r in rows]


def _inside(root, rel, sub=""):
    """The real path of root/rel when it lies inside root/sub (symlinks and .. resolved), else None."""
    if not root or not isinstance(rel, str) or not rel:
        return None
    top = os.path.realpath(root)
    base = os.path.realpath(os.path.join(top, sub)) if sub else top
    full = os.path.realpath(os.path.join(top, rel))
    return full if full.startswith(base + os.sep) else None


def read_file(root, rel):
    """A file under root (realpath-checked) -> bytes, or None."""
    full = _inside(root, rel)
    if full is None or not os.path.isfile(full):
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
    looks = sorted(db["vision_looks"].find({"kind": {"$in": list(LOOK_KINDS)}, "path": doc.get("path")}),
                   key=lambda d: d.get("at") or 0) \
        if doc.get("path") else []
    eye = _latest([d for d in db["eye_minutes"].find({"minute": minute}) if d.get("result")])
    scene = _latest([d for d in db["mind_scenes"].find({"minute": minute, "lane": LANE}) if d.get("scene")])
    return {"id": str(doc["_id"]), "at": at, "session": doc.get("session"), "snap": "snap" in doc,
            "jpeg_b64": _b64(root, doc.get("path")),
            "looks": [{"at": d.get("at"), "kind": d.get("kind"), "text": d.get("text")} for d in looks],
            "eye": {"minute": minute, "text": "\n".join(texts(eye["result"]))} if eye else None,
            "scene": {"minute": minute, "text": "\n".join(texts(scene["scene"]))} if scene else None}


# ---------------------------------------------------------------- complete delete (owner-triggered, irreversible)

MAX_DELETE = 500
MIN_FRAGMENT = 8
_BJ, _DAY_MS = redact.BJ, redact.DAY_MS
_SPLIT = re.compile(r"[。！？；\n]")


class Stale(Exception):
    """The delete plan changed since the page showed it (new data came in): show the new plan first."""


def _check_ids(ids):
    if not isinstance(ids, list) or len(ids) > MAX_DELETE:
        raise ValueError(f"at most {MAX_DELETE} ids")
    return [str(i) for i in ids]


def _months(lo, hi):
    """The log_ month suffixes (Beijing time) of every day in [lo, hi]."""
    out, t = set(), lo
    while t < hi:
        out.add(hello_log.month_of(t))
        t += _DAY_MS
    out.add(hello_log.month_of(hi))
    return sorted(out)


def _fragments(looks, eyes, scenes, segs):
    """The sentences (≥ 8 characters) of everything the mind wrote from these photos, longest first."""
    strings = [d.get("text") for d in looks]
    strings += [t for d in eyes for t in texts(d.get("result"))]
    strings += [t for d in scenes for t in texts(d.get("scene"))]
    strings += [(d.get("summary") or {}).get(k) for d in segs for k in ("title", "text")]
    out = {p.strip() for s in strings if isinstance(s, str) for p in _SPLIT.split(s)}
    return sorted((p for p in out if len(p) >= MIN_FRAGMENT), key=lambda p: (-len(p), p))


def _collect(db, ids):
    """Everything a delete of these photos removes: {collection: [docs]}, paths, minutes, fragments, fp."""
    rows = list(db["frames"].find({"_id": _ids(ids)}))
    paths = sorted({r["path"] for r in rows if r.get("path")})
    ats = [r["at"] for r in rows if isinstance(r.get("at"), (int, float))]
    minutes = sorted({_minute(a) for a in ats})
    hit = {"frames": rows,
           "vision_looks": list(db["vision_looks"].find({"path": {"$in": paths}})) if paths else [],
           "eye_minutes": list(db["eye_minutes"].find({"$or": [{"minute": {"$in": minutes}},
                                                               {"frames.path": {"$in": paths}}]})),
           "mind_segments": []}
    for c in ("mind_packages", "mind_scenes"):
        hit[c] = list(db[c].find({"minute": {"$in": minutes}}))
    runs, months = [{"minute": {"$in": minutes}}], []
    if minutes:
        near = db["mind_segments"].find({"start": {"$lt": minutes[-1] + 60000}, "$or": [
            {"end": None}, {"end": {"$exists": False}}, {"end": {"$gt": minutes[0]}}]})
        hit["mind_segments"] = [s for s in near if any(
            s["start"] < m + 60000 and (s.get("end") is None or s["end"] > m) for m in minutes)]
        # a segment's summary run and its call.segment row are keyed by the segment's start + lane (segments.py)
        runs += [{"step": "segment", "lane": s.get("lane"), "minute": s["start"]} for s in hit["mind_segments"]]
        ors = [{"kind": {"$in": ["call.eye", "call.fusion"]}, "ref.minute": {"$in": minutes}}]
        ors += [{"kind": "call.look", "ref.path": {"$in": paths}}] if paths else []
        ors += [{"kind": "call.segment", "ref.minute": s["start"], "ref.lane": s.get("lane")}
                for s in hit["mind_segments"]]
        months = _months(min(minutes + [s["start"] for s in hit["mind_segments"]]), minutes[-1] + _DAY_MS)
        for month in months:
            found = list(db["log_" + month].find({"$or": ors}))
            if found:
                hit["log_" + month] = found
    hit["mind_runs"] = list(db["mind_runs"].find({"$or": runs}))
    fp = hashlib.sha256(json.dumps({c: sorted(str(d.get("_id")) for d in docs) for c, docs in hit.items()},
                                   sort_keys=True).encode()).hexdigest()[:16]
    looks = hit["vision_looks"]               # every row read from these photos: look, the live eye's scene, ...
    return {"hit": hit, "paths": paths, "months": months,
            "from": min(ats) if ats else None, "to": max(ats) if ats else None,
            "fp": fp, "fragments": _fragments(looks, hit["eye_minutes"], hit["mind_scenes"], hit["mind_segments"])}


def _logs(hit):
    return {c[4:]: docs for c, docs in hit.items() if c.startswith("log_")}


def plan(db, ids, *, spool_dir=None):
    """The delete plan the page confirms (read only). spool_dir: accepted for the route's symmetry with delete; the
    spool is only rewritten by delete."""
    m = _collect(db, _check_ids(ids))
    h = m["hit"]
    return {"fp": m["fp"], "frames": len(h["frames"]), "files": len(m["paths"]), "looks": len(h["vision_looks"]),
            "eye_minutes": len(h["eye_minutes"]), "packages": len(h["mind_packages"]), "scenes": len(h["mind_scenes"]),
            "segments": [{"start": s.get("start"), "end": s.get("end"), "title": (s.get("summary") or {}).get("title")}
                         for s in h["mind_segments"]],
            "runs": len(h["mind_runs"]), "log_rows": sum(len(v) for v in _logs(h).values()),
            "from": m["from"], "to": m["to"]}


def _drop_bodies(db, month, rows):
    """Delete the bodies of these log rows that no other row of the month still references under any name. -> count."""
    shas = {v for r in rows for v in (r.get("body") or {}).values() if isinstance(v, str)}
    if not shas:
        return 0
    others = db["log_" + month].find({"_id": {"$nin": [r["_id"] for r in rows]}, "body": {"$exists": True}},
                                     {"body": 1})
    used = {v for r in others if isinstance(r.get("body"), dict) for v in r["body"].values()}
    gone = sorted(shas - used)
    return db["logbody_" + month].delete_many({"_id": {"$in": gone}}).deleted_count if gone else 0


def _drop_files(root, paths):
    """Delete the photo files whose real path is inside root/frames, then their folders once empty."""
    done = skipped = 0
    stop = os.path.realpath(os.path.join(root, "frames")) if root else None
    for rel in paths:
        full = _inside(root, rel, "frames")
        if full is None:
            skipped += 1
            continue
        try:
            os.remove(full)
            done += 1
        except FileNotFoundError:
            continue
        folder = os.path.dirname(full)
        while folder.startswith(stop + os.sep):
            try:
                os.rmdir(folder)                                 # only succeeds when empty
            except OSError:
                break
            folder = os.path.dirname(folder)
    return done, skipped


def delete(db, root, ids, fp, *, spool_dir, emit):
    """Delete the photos and everything written from them, if the plan is still the one the page showed (fp), else
    Stale. Texts first, then records, files last. -> what was actually deleted where."""
    m = _collect(db, _check_ids(ids))
    if m["fp"] != fp:
        raise Stale("the delete plan changed")
    h, logs = m["hit"], _logs(m["hit"])
    out = {"bodies": sum(_drop_bodies(db, month, rows) for month, rows in logs.items())}
    first_month = hello_log.month_of(m["from"]) if m["from"] is not None else "000000"
    out["bodies_redacted"] = redact.redact_bodies(db, m["fragments"], first_month)
    out["sums_redacted"] = sum(redact.redact(db["log_" + month], "sum", m["fragments"],
                                       [r["_id"] for r in logs.get(month, [])]) for month in m["months"])
    out["runs_redacted"] = redact.redact_runs(db, m["fragments"], m["from"], m["to"], [d["_id"] for d in h["mind_runs"]])
    out["spool_redacted"] = redact.redact_spool(spool_dir, m["fragments"], m["from"], m["to"])
    out["log_rows"] = sum(db["log_" + month].delete_many({"_id": {"$in": [r["_id"] for r in rows]}}).deleted_count
                          for month, rows in logs.items())
    for key, c in (("segments", "mind_segments"), ("scenes", "mind_scenes"), ("packages", "mind_packages"),
                   ("runs", "mind_runs"), ("eye_minutes", "eye_minutes"), ("looks", "vision_looks"),
                   ("frames", "frames")):
        out[key] = db[c].delete_many({"_id": {"$in": [d["_id"] for d in h[c]]}}).deleted_count if h[c] else 0
    out["files"], out["files_skipped"] = _drop_files(root, m["paths"])
    span = "" if m["from"] is None else "（{}–{}）".format(
        *(datetime.datetime.fromtimestamp(t / 1000, _BJ).strftime("%H:%M") for t in (m["from"], m["to"])))
    emit("gallery.delete", by="page", sum=f"删除 {out['frames']} 张照片{span}",
         extra={**out, "from": m["from"], "to": m["to"]})
    return out


_redact_spool = redact.redact_spool              # the reviewer's spool bench calls it by this name
