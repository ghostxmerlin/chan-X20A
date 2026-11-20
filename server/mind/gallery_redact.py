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
SPOOL_BATCH = 2000                    # words per compiled bytes regex over the spool (no Mongo cap there)
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


ONLY_CHUNK = 2000                     # ids per {_id: {$in}} query of redact(only=...)


def redact(coll, field, frags, skip=(), *, only=None, at=None):
    """Replace the fragments in the string field of coll's documents that contain one (Mongo $regex). only: just
    these _ids (None = any); at: (lo, hi) just documents with at in [lo, hi]. -> count."""
    if not frags or (only is not None and not only):
        return 0
    scrub, done = scrubber(frags), set()
    ids = sorted(only) if only is not None else None
    chunks = [{"_id": {"$in": ids[i:i + ONLY_CHUNK]}} for i in range(0, len(ids), ONLY_CHUNK)] if ids is not None \
        else [{"_id": {"$nin": list(skip)}} if skip else {}]
    for pattern in patterns(frags):
        for chunk in chunks:
            flt = {field: {"$regex": pattern}, **chunk, **({"at": {"$gte": at[0], "$lte": at[1]}} if at else {})}
            for d in coll.find(flt, {field: 1}):
                new = scrub(d.get(field))
                if new != d.get(field):
                    coll.update_one({"_id": d["_id"]}, {"$set": {field: new}})
                    done.add(d["_id"])
    return len(done)


def _walk(v, scrub):
    if isinstance(v, dict):
        return {k: _walk(x, scrub) for k, x in v.items()}
    if isinstance(v, list):
        return [_walk(x, scrub) for x in v]
    return scrub(v)


def redact_runs(db, frags, lo, hi, skip):
    """Replace the fragments inside request / response / parsed of the mind runs within RUN_NEAR_MS of the photos
    that are not deleted themselves (e.g. a neighbour eye run that quoted the 看一眼 answer). -> runs changed."""
    if not frags or lo is None:
        return 0
    scrub, n = scrubber(frags), 0
    near = {"$gte": lo - RUN_NEAR_MS, "$lte": hi + RUN_NEAR_MS}
    for d in db["mind_runs"].find({"_id": {"$nin": list(skip)}, "$or": [{"at": near}, {"minute": near}]},
                                  {k: 1 for k in RUN_FIELDS}):
        changed = {k: new for k in RUN_FIELDS if k in d for new in [_walk(d[k], scrub)] if new != d[k]}
        if changed:
            db["mind_runs"].update_one({"_id": d["_id"]}, {"$set": changed})
            n += 1
    return n


def redact_evidence(db, minutes, frags):
    """mind_memory_evidence (memory_worker.py): a ref to a deleted eye minute ({c: eye_minutes, minute}) carries that
    minute's activity text as `excerpt` -> MARK; the statements (and other excerpts) of those documents lose the
    fragments. -> documents changed."""
    if not minutes:
        return 0
    gone, scrub, n = set(minutes), scrubber(frags), 0

    def mine(r):
        return isinstance(r, dict) and r.get("c") == "eye_minutes" and r.get("minute") in gone
    for d in db["mind_memory_evidence"].find({"observations.refs.minute": {"$in": sorted(gone)}},
                                             {"observations": 1}):
        obs = d.get("observations")
        if not isinstance(obs, list) or not any(isinstance(o, dict) and isinstance(o.get("refs"), list)
                                                and any(mine(r) for r in o["refs"]) for o in obs):
            continue
        new = []
        for o in obs:
            if not isinstance(o, dict):
                new.append(o)
                continue
            refs = o.get("refs") if isinstance(o.get("refs"), list) else None
            o = {**o, "statement": scrub(o.get("statement"))} if "statement" in o else dict(o)
            if refs is not None:
                o["refs"] = [{**r, "excerpt": MARK} if mine(r) and "excerpt" in r
                             else ({**r, "excerpt": scrub(r["excerpt"])} if isinstance(r, dict) and "excerpt" in r
                                   else r) for r in refs]
            new.append(o)
        if new != obs:
            db["mind_memory_evidence"].update_one({"_id": d["_id"]}, {"$set": {"observations": new}})
            n += 1
    return n


NEAR_MINUTE_MS = 3 * 60_000           # eye.recent_looks quotes the looks of the last 3 minutes into a minute's input
NEIGHBOURS = ("eye_minutes", "mind_packages", "mind_scenes")


def redact_neighbours(db, minutes, frags, skip):
    """The minute records within NEAR_MINUTE_MS of a deleted minute that are not deleted themselves lose the
    fragments in every string leaf. skip: {collection: ids being deleted}. -> documents changed."""
    if not minutes or not frags:
        return 0
    scrub, n = scrubber(frags), 0
    for c in NEIGHBOURS:
        flt = {"minute": {"$gte": minutes[0] - NEAR_MINUTE_MS, "$lte": minutes[-1] + NEAR_MINUTE_MS}}
        if skip.get(c):
            flt["_id"] = {"$nin": list(skip[c])}
        for d in db[c].find(flt):
            m = d.get("minute")
            if not isinstance(m, (int, float)) or not any(abs(m - x) <= NEAR_MINUTE_MS for x in minutes):
                continue
            changed = {k: new for k, v in d.items() if k != "_id" for new in [_walk(v, scrub)] if new != v}
            if changed:
                db[c].update_one({"_id": d["_id"]}, {"$set": changed})
                n += 1
    return n


def _trie(words):
    """bytes words -> one regex source matching any of them, longest first, as a byte trie (Python's re tries the
    alternatives of a plain a|b|c one by one at every position: ~20x slower over a spool file of Chinese text)."""
    root = {}
    for w in words:
        node = root
        for b in w:
            node = node.setdefault(b, {})
        node[None] = None

    def emit(node):
        alts = []
        for b in sorted(k for k in node if k is not None):
            child, chain = node[b], bytes([b])
            while len(child) == 1 and None not in child:         # a run without branches: one literal
                (b2, child), = child.items()
                chain += bytes([b2])
            alts.append(re.escape(chain) + emit(child))
        if not alts:
            return b""
        if len(alts) == 1 and None not in node:
            return alts[0]
        return b"(?:" + b"|".join(alts) + b")" + (b"?" if None in node else b"")
    return emit(root)


def _byte_regexes(words):
    """The words as compiled bytes regexes of at most SPOOL_BATCH words each (a trie; a plain alternation, longest
    first, if a batch is too deep for the regex compiler)."""
    out = []
    for i in range(0, len(words), SPOOL_BATCH):
        batch = sorted(words[i:i + SPOOL_BATCH], key=lambda w: (-len(w), w))
        try:
            out.append(re.compile(_trie(batch)))
        except (RecursionError, re.error, OverflowError):
            out.append(re.compile(b"|".join(re.escape(w) for w in batch)))
    return out


def _stars(v):
    """A JSON string value -> as many '*' as its encoded bytes (so the line keeps its length)."""
    return "*" * len(json.dumps(v, ensure_ascii=False)[1:-1].encode())


def _wipe(line, ids):
    """A deleted row's own spool line (its flow _id in ids) with every string value of flow and bodies starred, the
    _id kept (a replay stays a duplicate) and keys / numbers / structure as they were; None if this line is not
    one of them or cannot be rewritten at the same length."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("flow"), dict) or rec["flow"].get("_id") not in ids:
        return None
    if json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode() != line:
        return None

    def star(v):
        if isinstance(v, str):
            return _stars(v)
        if isinstance(v, dict):
            return {k: star(x) for k, x in v.items()}
        if isinstance(v, list):
            return [star(x) for x in v]
        return v
    out = {**rec, "flow": {k: (v if k == "_id" else star(v)) for k, v in rec["flow"].items()}}
    if "bodies" in rec:
        out["bodies"] = star(rec["bodies"])
    new = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()
    return new if len(new) == len(line) else None


def _line_at(data, start, end):
    """(line start, line end) of the line holding data[start:end]; end None when that line has no newline yet."""
    s = data.rfind(b"\n", 0, start) + 1
    e = data.find(b"\n", end)
    return s, (e if e >= 0 else None)


def redact_spool(spool_dir, frags, lo, hi, wipe_ids=()):
    """Overwrite the fragments' bytes with as many '*' in the spool files of the Beijing days [lo, hi + 1 day], in
    place: the size never changes, so the process appending to today's file and the .offset files are unaffected.
    The lines of the deleted log rows themselves (wipe_ids: their flow _id) get every string value starred, short
    ones too. One compiled bytes regex per batch of fragments, run over each file (finditer); a line is only changed
    when it is still valid JSON of the same length. -> lines changed."""
    ids = {i for i in wipe_ids if isinstance(i, str)}
    if not spool_dir or (not frags and not ids) or lo is None or not os.path.isdir(spool_dir):
        return 0
    day = lambda t: datetime.datetime.fromtimestamp(t / 1000, BJ).strftime("%Y%m%d")     # noqa: E731
    first, last = day(lo), day(hi + DAY_MS)
    regexes = _byte_regexes([json.dumps(f, ensure_ascii=False)[1:-1].encode() for f in frags])
    id_rx = _byte_regexes([b'"_id":' + json.dumps(i, ensure_ascii=False).encode() for i in sorted(ids)])
    star = lambda m: b"*" * len(m.group())                                                 # noqa: E731
    changed = 0
    for name in sorted(os.listdir(spool_dir)):
        hit = _SPOOL.match(name)
        if not hit or not first <= hit.group(1) <= last:
            continue
        with open(os.path.join(spool_dir, name), "r+b") as f:
            data = f.read()
            lines, wipe = {}, set()
            for rxs, mark in ((regexes, False), (id_rx, True)):
                for rx in rxs:
                    for m in rx.finditer(data):
                        s, e = _line_at(data, m.start(), m.end())
                        if e is None:                            # a line still being written: left alone
                            continue
                        lines[s] = e
                        if mark:
                            wipe.add(s)
            for s, e in sorted(lines.items()):
                line = data[s:e]
                new = _wipe(line, ids) if s in wipe else None
                if new is None:
                    new = line
                    for rx in regexes:
                        new = rx.sub(star, new)
                if new != line and len(new) == len(line) and valid(new):
                    f.seek(s)
                    f.write(new)
                    changed += 1
    return changed


def spool_backlog(spool_dir, lo, hi):
    """The spool files of the days a delete would rewrite that still hold records not shipped to Mongo (their
    .offset short of the file size): those would re-insert deleted rows later (review M6). -> file names."""
    if not spool_dir or lo is None or not os.path.isdir(spool_dir):
        return []
    day = lambda t: datetime.datetime.fromtimestamp(t / 1000, BJ).strftime("%Y%m%d")     # noqa: E731
    first, last, out = day(lo), day(hi + DAY_MS), []
    for name in sorted(os.listdir(spool_dir)):
        hit = _SPOOL.match(name)
        if not hit or not first <= hit.group(1) <= last:
            continue
        path = os.path.join(spool_dir, name)
        try:
            with open(path[:-len(".jsonl")] + ".offset") as f:
                offset = int(f.read().strip() or 0)
        except (OSError, ValueError):
            offset = 0
        if offset < os.path.getsize(path):
            out.append(name)
    return out


def valid(line):
    try:
        json.loads(line)
        return True
    except ValueError:
        return False
