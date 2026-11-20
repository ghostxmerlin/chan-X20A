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
import threading
from collections import OrderedDict

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


FRESH_FRAME_MS = 120_000             # a photo received this recently = the robot is still sending


def frame_recent(db, now_ms, within=FRESH_FRAME_MS):
    """True when the newest frames record was received (or taken) within `within` ms of now."""
    doc = db["frames"].find_one({}, sort=[("at", -1)])
    t = max((v for v in ((doc or {}).get("received"), (doc or {}).get("at")) if isinstance(v, (int, float))),
            default=None)
    return t is not None and now_ms - t <= within


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


RUN_STEPS = ("eye", "fusion")         # mind_runs written from a minute's photos (eye.py, fusion.py); chat / say /
                                      # notes / model calls of the same minute are not (review I3)
# call.* rows of deleted photos stay with only these fields: the daily cap (hello_log_query.ledger) still counts
# their cost (review M3); ref / body / sum / extra / trace / key and anything else go
LOG_KEEP = ("_id", "kind", "at", "svc", "run", "seq", "ok", "ms", "model", "usage", "cost")
PLANS_KEEP = 64                       # plans remembered for delete's fp check (a restart forgets them: re-plan)
_plans, _plans_lock = OrderedDict(), threading.Lock()


class Stale(Exception):
    """The delete plan changed since the page showed it (new data came in): show the new plan first."""


def _remember(fp, keys):
    with _plans_lock:
        _plans[fp] = keys
        _plans.move_to_end(fp)
        while len(_plans) > PLANS_KEEP:
            _plans.popitem(last=False)


def _still_planned(fp, m):
    """True when every record of the current plan was in the plan the page showed (fp). Records gone since then
    (an earlier batch deleted a shared minute or segment) are fine; a record that was not shown is not (review M1)."""
    if fp == m["fp"]:
        return True
    with _plans_lock:
        planned = _plans.get(fp)
    return planned is not None and m["keys"] <= planned


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


def _segment_end(s):
    """Where a mind_segments row ends for the overlap test. A closed row: its end. An open row (end None) covers only
    [start, at), at = when its summary was written (it cannot hold scenes after that); without at, [start, start + 60 s).
    Production keeps hundreds of old open versions that were never closed: "open until now" matched them all."""
    if isinstance(s.get("end"), (int, float)):
        return s["end"]
    at = s.get("at")
    return at if isinstance(at, (int, float)) and at > s["start"] else s["start"] + 60000


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
    runs, months = [{"minute": {"$in": minutes}, "step": {"$in": list(RUN_STEPS)}}], []
    if minutes:
        first = minutes[0]
        near = db["mind_segments"].find({"start": {"$lt": minutes[-1] + 60000}, "$or": [
            {"end": {"$gt": first}}, {"end": None, "at": {"$gt": first}},
            {"end": {"$exists": False}, "at": {"$gt": first}},
            {"end": None, "at": None, "start": {"$gt": first - 60000}},
            {"end": {"$exists": False}, "at": {"$exists": False}, "start": {"$gt": first - 60000}},
            {"end": None, "at": {"$exists": False}, "start": {"$gt": first - 60000}}]})
        hit["mind_segments"] = [s for s in near if isinstance(s.get("start"), (int, float)) and any(
            s["start"] < m + 60000 and _segment_end(s) > m for m in minutes)]
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
    keys = frozenset(f"{c}:{d.get('_id')}" for c, docs in hit.items() for d in docs)
    looks = hit["vision_looks"]               # every row read from these photos: look, the live eye's scene, ...
    described = {d.get("minute") for d in hit["eye_minutes"] if d.get("result")} | \
        {d.get("minute") for d in hit["mind_scenes"]}
    return {"hit": hit, "paths": paths, "months": months, "keys": keys, "photo_minutes": minutes,
            "minutes": sorted(x for x in described if isinstance(x, (int, float))),
            "from": min(ats) if ats else None, "to": max(ats) if ats else None,
            "fp": fp, "fragments": _fragments(looks, hit["eye_minutes"], hit["mind_scenes"], hit["mind_segments"])}


def _logs(hit):
    return {c[4:]: docs for c, docs in hit.items() if c.startswith("log_")}


def plan(db, ids, *, spool_dir=None):
    """The delete plan the page confirms (read only). spool_dir: accepted for the route's symmetry with delete; the
    spool is only rewritten by delete."""
    m = _collect(db, _check_ids(ids))
    h = m["hit"]
    _remember(m["fp"], m["keys"])
    return {"fp": m["fp"], "minutes": m["minutes"], "frames": len(h["frames"]), "files": len(m["paths"]), "looks": len(h["vision_looks"]),
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


def _drop_log_rows(db, month, rows):
    """call.* rows are kept with LOG_KEEP only (their cost stays in the ledger); any other row is deleted. -> rows."""
    n = 0
    for r in rows:
        if str(r.get("kind") or "").startswith("call."):
            db["log_" + month].replace_one({"_id": r["_id"]}, {k: r[k] for k in LOG_KEEP if k in r})
            n += 1
        else:
            n += db["log_" + month].delete_many({"_id": r["_id"]}).deleted_count
    return n


def _drop_frames(root, rows, frames):
    """Per photo: the file first (real path inside root/frames), then its frames record, then the folders once
    empty. A file that cannot be removed (any OSError but "already gone") keeps its record, so the photo still
    shows and can be deleted again; a path outside root/frames is never touched (its record goes). -> counts."""
    out = {"frames": 0, "files": 0, "files_skipped": 0, "files_failed": 0}
    stop = os.path.realpath(os.path.join(root, "frames")) if root else None
    by_path = {}
    for r in rows:
        by_path.setdefault(r.get("path") if isinstance(r.get("path"), str) else None, []).append(r["_id"])
    for rel, ids in by_path.items():
        full = _inside(root, rel, "frames") if rel else None
        if rel and full is None:
            out["files_skipped"] += 1
        elif full is not None:
            try:
                os.remove(full)
                out["files"] += 1
            except FileNotFoundError:
                pass
            except OSError:
                out["files_failed"] += 1
                continue
            folder = os.path.dirname(full)
            while folder.startswith(stop + os.sep):
                try:
                    os.rmdir(folder)                             # only succeeds when empty
                except OSError:
                    break
                folder = os.path.dirname(folder)
        out["frames"] += frames.delete_many({"_id": {"$in": ids}}).deleted_count
    return out


LOG_BEFORE_MS = 3600_000              # other log rows (and their bodies) this long before the photos may quote them
LOG_AFTER_MS = 48 * 3600_000          # ... and this long after (the realtime context, chat, later segment calls)


def _near_bodies(db, month, lo, hi, skip):
    """The body shas referenced by the month's log rows with at in [lo, hi] that are not deleted (skip)."""
    rows = db["log_" + month].find({"at": {"$gte": lo, "$lte": hi}, "body": {"$exists": True},
                                    **({"_id": {"$nin": list(skip)}} if skip else {})}, {"body": 1})
    return {v for r in rows if isinstance(r.get("body"), dict) for v in r["body"].values() if isinstance(v, str)}


def prepare(db, ids, fp):
    """Everything a delete of these photos removes, if the plan is still the one the page showed (fp), else Stale;
    a bad id list is a ValueError. Read only: the API checks it before starting the delete job."""
    m = _collect(db, _check_ids(ids))
    if not _still_planned(fp, m):
        raise Stale("the delete plan changed")
    return m


def run(db, root, m, *, spool_dir, emit, step=None):
    """Delete what prepare() collected: texts first (bodies, copies elsewhere, the spool), then the log rows and
    records, each photo's file just before its frames record. step(name, counts) is called as each part starts.
    gallery.delete is written also when it fails part way (error = exception type, counts so far), then the error is
    raised again. -> what was actually deleted where."""
    h, logs, out = m["hit"], _logs(m["hit"]), {}
    frags, lo, hi = m["fragments"], m["from"], m["to"]
    gone = {month: [r["_id"] for r in rows] for month, rows in logs.items()}
    say = step or (lambda name, counts: None)
    try:
        say("bodies", out)
        out["bodies"] = sum(_drop_bodies(db, month, rows) for month, rows in logs.items())
        say("redact", out)
        out["bodies_redacted"] = out["sums_redacted"] = 0
        if lo is not None and frags:
            wlo, whi = lo - LOG_BEFORE_MS, hi + LOG_AFTER_MS
            for month in _months(wlo, whi):
                out["bodies_redacted"] += redact.redact(db["logbody_" + month], "text", frags,
                                                        only=_near_bodies(db, month, wlo, whi, gone.get(month)))
                out["sums_redacted"] += redact.redact(db["log_" + month], "sum", frags, gone.get(month, []),
                                                      at=(wlo, whi))
        out["runs_redacted"] = redact.redact_runs(db, frags, lo, hi, [d["_id"] for d in h["mind_runs"]])
        out["evidence_redacted"] = redact.redact_evidence(
            db, sorted({d["minute"] for d in h["eye_minutes"] if isinstance(d.get("minute"), (int, float))}
                       | set(m["photo_minutes"])), frags)
        out["neighbours_redacted"] = redact.redact_neighbours(db, m["photo_minutes"], frags,
                                                              {c: [d["_id"] for d in h[c]] for c in redact.NEIGHBOURS})
        say("spool", out)
        out["spool_redacted"] = redact.redact_spool(spool_dir, frags, lo, hi,
                                                    [i for ids in gone.values() for i in ids])
        say("logs", out)
        out["log_rows"] = sum(_drop_log_rows(db, month, rows) for month, rows in logs.items())
        say("records", out)
        for key, c in (("segments", "mind_segments"), ("scenes", "mind_scenes"), ("packages", "mind_packages"),
                       ("runs", "mind_runs"), ("eye_minutes", "eye_minutes"), ("looks", "vision_looks")):
            out[key] = db[c].delete_many({"_id": {"$in": [d["_id"] for d in h[c]]}}).deleted_count if h[c] else 0
        say("files", out)
        out.update(_drop_frames(root, h["frames"], db["frames"]))
    except Exception as e:
        _record(emit, m, {**out, "error": type(e).__name__})
        raise
    _record(emit, m, out)
    return out


def _record(emit, m, out):
    """The run log line of a delete: counts and the time span only, never a path or a text."""
    span = "" if m["from"] is None else "（{}–{}）".format(
        *(datetime.datetime.fromtimestamp(t / 1000, _BJ).strftime("%H:%M") for t in (m["from"], m["to"])))
    n = len(m["hit"]["frames"])
    text = f"删除 {out['frames']} 张照片{span}" if "error" not in out else f"删除 {n} 张照片{span}没删完（出错）"
    emit("gallery.delete", by="page", sum=text, extra={**out, "from": m["from"], "to": m["to"]})


def spool_backlog(spool_dir, m):
    """The spool files of the plan's days with records not shipped yet (they would put deleted rows back)."""
    return redact.spool_backlog(spool_dir, m["from"], m["to"])


def delete(db, root, ids, fp, *, spool_dir, emit, step=None):
    """prepare + run in one call. -> what was actually deleted where."""
    return run(db, root, prepare(db, ids, fp), spool_dir=spool_dir, emit=emit, step=step)


_redact_spool = redact.redact_spool              # the reviewer's spool bench calls it by this name
