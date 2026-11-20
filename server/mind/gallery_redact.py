"""图库 delete, the copies (2026-11-20; split from mind/gallery.py): the sentences derived from deleted photos are
replaced wherever else they were copied — other run log bodies and sums, nearby mind runs, the log spool files.
Pure helpers over the database / the spool folder; gallery.py decides what is deleted and calls these."""
import datetime
import json
import os
import re

MARK = "［图库已删除］"
REGEX_BATCH = 200
REGEX_BYTES = 16_000                  # Mongo refuses a $regex pattern over ~32 KB (error 51091)
RUN_NEAR_MS = 3 * 3600_000            # mind_runs this close to the photos may quote them (eye.recent_looks)
RUN_FIELDS = ("request", "response", "parsed")
BJ = datetime.timezone(datetime.timedelta(hours=8))
DAY_MS = 86400_000
_SPOOL = re.compile(r"^.+-(\d{8})\.jsonl$")


def patterns(frags):
    """The fragments as regex alternations of at most REGEX_BATCH fragments and REGEX_BYTES UTF-8 bytes each (a
    single longer fragment gets a pattern of its own)."""
    out, cur, size = [], [], 0
    for f in frags:
        e = re.escape(f)
        n = len(e.encode()) + 1
        if cur and (len(cur) >= REGEX_BATCH or size + n > REGEX_BYTES):
            out.append("|".join(cur))
            cur, size = [], 0
        cur.append(e)
        size += n
    return out + (["|".join(cur)] if cur else [])


def scrubber(frags):
    """text -> text with every fragment replaced by MARK (longest first); non-strings are returned as they are."""
    any_of = re.compile("|".join(re.escape(f) for f in frags)) if frags else None

    def scrub(text):
        if any_of is None or not isinstance(text, str) or not any_of.search(text):
            return text
        for f in frags:
            text = text.replace(f, MARK)
        return text
    return scrub


def redact(coll, field, frags, skip=()):
    """Replace the fragments in the string field of coll's documents that contain one (Mongo $regex). -> count."""
    scrub, done = scrubber(frags), set()
    for pattern in patterns(frags):
        flt = {field: {"$regex": pattern}, **({"_id": {"$nin": list(skip)}} if skip else {})}
        for d in coll.find(flt, {field: 1}):
            new = scrub(d.get(field))
            if new != d.get(field):
                coll.update_one({"_id": d["_id"]}, {"$set": {field: new}})
                done.add(d["_id"])
    return len(done)


def redact_bodies(db, frags, first_month):
    """Replace the fragments in every remaining log body from the photos' first month on. -> bodies changed."""
    names = sorted(n for n in db.list_collection_names() if re.fullmatch(r"logbody_\d{6}", n) and n[8:] >= first_month)
    return sum(redact(db[n], "text", frags) for n in names) if frags else 0


def redact_runs(db, frags, lo, hi, skip):
    """Replace the fragments inside request / response / parsed of the mind runs within RUN_NEAR_MS of the photos
    that are not deleted themselves (e.g. a neighbour eye run that quoted the 看一眼 answer). -> runs changed."""
    if not frags or lo is None:
        return 0
    scrub, n = scrubber(frags), 0

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return scrub(v)
    near = {"$gte": lo - RUN_NEAR_MS, "$lte": hi + RUN_NEAR_MS}
    for d in db["mind_runs"].find({"_id": {"$nin": list(skip)}, "$or": [{"at": near}, {"minute": near}]},
                                  {k: 1 for k in RUN_FIELDS}):
        changed = {k: new for k in RUN_FIELDS if k in d for new in [walk(d[k])] if new != d[k]}
        if changed:
            db["mind_runs"].update_one({"_id": d["_id"]}, {"$set": changed})
            n += 1
    return n


def redact_spool(spool_dir, frags, lo, hi):
    """Overwrite the fragments' bytes with as many '*' in the spool files of the Beijing days [lo, hi + 1 day], in
    place: the size never changes, so the process appending to today's file and the .offset files are unaffected. A
    line is only changed when it is still valid JSON afterwards. -> lines changed."""
    if not spool_dir or not frags or lo is None or not os.path.isdir(spool_dir):
        return 0
    day = lambda t: datetime.datetime.fromtimestamp(t / 1000, BJ).strftime("%Y%m%d")     # noqa: E731
    first, last = day(lo), day(hi + DAY_MS)
    needles = [json.dumps(f, ensure_ascii=False)[1:-1].encode() for f in frags]
    changed = 0
    for name in sorted(os.listdir(spool_dir)):
        hit = _SPOOL.match(name)
        if not hit or not first <= hit.group(1) <= last:
            continue
        with open(os.path.join(spool_dir, name), "r+b") as f:
            data = f.read()
            pos = 0
            while True:
                end = data.find(b"\n", pos)
                if end < 0:                                      # a line still being written: left alone
                    break
                line = new = data[pos:end]
                for n in needles:
                    new = new.replace(n, b"*" * len(n))
                if new != line and valid(new):
                    f.seek(pos)
                    f.write(new)
                    changed += 1
                pos = end + 1
    return changed


def valid(line):
    try:
        json.loads(line)
        return True
    except ValueError:
        return False
