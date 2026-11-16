"""Read side of hello's run/audit log: flow records (log_YYYYMM) and bodies (logbody_YYYYMM).

Stdlib only; works on a pymongo Database (or anything with db[name].find(...).sort(...).limit(...)
and find_one). Used by mind's read-only API and by scripts. Table layout: see docs/hello-log.md.

    import hello_log_query as q
    rows = q.flows(db, start_ms, end_ms, kind_prefix="call.", has_cost=True, limit=50)
    text = q.body(db, rows[0]["at"], rows[0]["body"]["output"])
    q.ledger(db, "2026-11-16")     # cost per svc/model/currency for one Beijing day
    q.gaps(db, start_ms, end_ms)   # missing heartbeats, kills (no svc.stop before a restart), lost records
    q.last_seen(db, ["ear", "mind"], now_ms)   # each service's newest record (cheap; the 日志 page polls it)

Times are ms since epoch; months and days are Beijing time (Asia/Shanghai, UTC+8, no DST).
"""
import re
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))
DAY_MS = 86400000
MAX_LIMIT = 1000
QUERY_MS = 5000                   # max_time_ms of every find / find_one: the server gives up instead of the reader


def month_of(at_ms):
    """'YYYYMM' of a ms timestamp in Beijing time."""
    return datetime.fromtimestamp(int(at_ms) / 1000, BJ).strftime("%Y%m")


MAX_MONTHS = 60


def _months(db, start_ms, end_ms):
    """Month suffixes of the log_ tables that exist and are touched by [start_ms, end_ms), newest
    first. A span over 60 months is a ValueError."""
    last = datetime.fromtimestamp(max(start_ms, end_ms - 1) / 1000, BJ)
    y, m = last.year, last.month
    first = month_of(start_ms)
    wanted = []
    while True:
        s = f"{y:04d}{m:02d}"
        wanted.append(s)
        if s <= first:
            break
        if len(wanted) > MAX_MONTHS:
            raise ValueError(f"time span over {MAX_MONTHS} months")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    have = set(db.list_collection_names())
    return [mo for mo in wanted if "log_" + mo in have]


def _flow(db, month):
    return db["log_" + month]


def _find_flow(db, flow_id, months):
    for mo in months:
        doc = _flow(db, mo).find_one({"_id": flow_id}, max_time_ms=QUERY_MS)
        if doc is not None:
            return doc
    return None


def flows(db, start_ms, end_ms, *, kinds=None, kind_prefix=None, svc=None, trace=None, has_cost=None,
          limit=200, before_id=None):
    """Flow records with start_ms <= at < end_ms, newest first (at descending, then _id descending).
    Page by passing the last _id of the previous page as before_id (same start/end/filters).
    limit is capped at 1000."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    months = _months(db, start_ms, end_ms)
    at_range = {"$gte": int(start_ms), "$lt": int(end_ms)}
    cond = {}
    if kinds:
        cond["kind"] = {"$in": list(kinds)}
    if kind_prefix:
        cond["kind"] = {"$regex": "^" + re.escape(kind_prefix)}
    if svc:
        cond["svc"] = svc
    if trace:
        cond["trace"] = trace
    if has_cost is not None:
        cond["cost"] = {"$exists": bool(has_cost)}
    after = None                                   # (at, _id) of the row to continue below
    if before_id:
        row = _find_flow(db, before_id, months)
        if row is None:
            return []
        after = (row["at"], before_id)
    out = []
    for mo in months:
        flt = dict(cond, at=dict(at_range))
        if after is not None:
            flt["$or"] = [{"at": {"$lt": after[0]}}, {"at": after[0], "_id": {"$lt": after[1]}}]
        cur = _flow(db, mo).find(flt, max_time_ms=QUERY_MS).sort([("at", -1), ("_id", -1)]).limit(limit - len(out))
        out += list(cur)
        if len(out) >= limit:
            break
    return out


def body(db, at_ms, body_id):
    """Full text of a body (flow["body"][name]); None when missing. at_ms picks the month table."""
    doc = db["logbody_" + month_of(at_ms)].find_one({"_id": body_id}, max_time_ms=QUERY_MS)
    return None if doc is None else doc.get("text")


def chain(db, trace, around_ms):
    """Every record of one trace within a day either side of around_ms, oldest first."""
    start, end = int(around_ms) - DAY_MS, int(around_ms) + DAY_MS
    out = []
    for mo in _months(db, start, end):
        out += list(_flow(db, mo).find({"trace": trace, "at": {"$gte": start, "$lt": end}}, max_time_ms=QUERY_MS))
    out.sort(key=lambda d: (d["at"], d.get("seq", 0), d["_id"]))
    return out


def ledger(db, day):
    """Cost of one Beijing day ('YYYY-MM-DD'): {"by": [{svc, model, cur, amount, n, unknown_n}],
    "total": {cur: amount}, "unknown_n": k}. A record whose cost.amount is None counts into
    unknown_n and adds nothing to amount."""
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BJ)
    start = int(d0.timestamp() * 1000)
    end = int((d0 + timedelta(days=1)).timestamp() * 1000)
    groups = {}
    for mo in _months(db, start, end):
        flt = {"cost": {"$exists": True}, "at": {"$gte": start, "$lt": end}}
        for r in _flow(db, mo).find(flt, max_time_ms=QUERY_MS):
            c = r.get("cost") or {}
            g = groups.setdefault((r.get("svc"), r.get("model"), c.get("cur") or "?"),
                                  {"amount": 0.0, "n": 0, "unknown_n": 0})
            g["n"] += 1
            if c.get("amount") is None:
                g["unknown_n"] += 1
            else:
                g["amount"] += c["amount"]
    by, total, unknown = [], {}, 0
    for (s, m, cur), g in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        amount = round(g["amount"], 9)
        by.append({"svc": s, "model": m, "cur": cur, "amount": amount, "n": g["n"], "unknown_n": g["unknown_n"]})
        total[cur] = round(total.get(cur, 0.0) + amount, 9)
        unknown += g["unknown_n"]
    return {"by": by, "total": total, "unknown_n": unknown}


LAST_RECENT_MS = 5 * 60000
LAST_MONTHS_BACK = 2
LIFE_KINDS = ("svc.beat", "svc.start", "svc.stop")


def _newest(col, flt):
    return next(iter(col.find(flt, {"at": 1, "kind": 1, "run": 1}, max_time_ms=QUERY_MS).sort("at", -1).limit(1)),
                None)


def last_seen(db, svcs, now_ms, *, recent_ms=LAST_RECENT_MS, months_back=LAST_MONTHS_BACK):
    """{svc: {at, kind, run} | None}: each service's newest record at or before now_ms, kept cheap for a page that
    polls it. Any kind only within the last recent_ms (the at index, a few minutes of rows); else the newest
    svc.beat / svc.start / svc.stop (the (kind, at) index), newest month first, existing tables only, stopping at
    the first month with a hit, at most months_back months before now_ms's month. None: nothing found."""
    now = int(now_ms)
    have = set(db.list_collection_names())
    d = datetime.fromtimestamp(now / 1000, BJ)
    y, m, months = d.year, d.month, []
    for _ in range(months_back + 1):
        months.append(f"{y:04d}{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months = [mo for mo in months if "log_" + mo in have]
    recent_months = _months(db, now - recent_ms, now + 1)
    out = {}
    for svc in svcs:
        best = None
        for mo in recent_months:
            r = _newest(_flow(db, mo), {"svc": svc, "at": {"$gte": now - recent_ms, "$lte": now}})
            if r is not None and (best is None or r["at"] > best["at"]):
                best = r
        if best is None:
            for mo in months:
                best = _newest(_flow(db, mo), {"svc": svc, "kind": {"$in": list(LIFE_KINDS)}, "at": {"$lte": now}})
                if best is not None:
                    break
        out[svc] = None if best is None else {"at": best.get("at"), "kind": best.get("kind"), "run": best.get("run")}
    return out


def _counted(row):
    """Records a svc.beat says were queued since the previous lifecycle row of its run: the sum of
    extra.counts without svc.start / svc.stop (those are lifecycle rows themselves). 0 when missing."""
    counts = (row.get("extra") or {}).get("counts") or {}
    if not isinstance(counts, dict):
        return 0
    return sum(v for k, v in counts.items() if k not in LIFE_KINDS and isinstance(v, int) and not isinstance(v, bool))


def gaps(db, start_ms, end_ms, *, beat_ms=60000, now_ms=None):
    """Holes in the logs near [start_ms, end_ms): [{svc, run, from_ms, to_ms, why}] (+ lost for seq_jump).

    Reads only svc.beat / svc.start / svc.stop rows ((kind, at) index; at, svc, run, seq, kind and
    extra.counts), never the business records, so it stays cheap over a long range.

    "seq_jump" (real loss): two consecutive lifecycle rows A, B of one run (by seq) where B is a
    svc.beat and B.seq - A.seq - 1 > the records B's extra.counts says were queued since A (its
    counts without svc.start / svc.stop). hello_log takes seq, the counts and the enqueue under one
    lock, so the two agree exactly unless records were lost (dropped at a full queue, a lost beat,
    rejected beats, …); lost = the difference. from_ms/to_ms = A's and B's times. A record queued
    and counted but lost later (e.g. rejected) is not seen here; the beat's dropped / rejected say so.
    "no_stop": a service's lifecycle row that is not svc.stop is followed by a svc.start of another
    run: the process was killed or crashed and restarted (however quickly); from_ms = that last row,
    to_ms = the new start, run = the killed one.
    "no_beat": more than 2 * beat_ms between two lifecycle rows of a service (run = the earlier row's).
    The stretch from a svc.stop to the next svc.start is an intentional stop, not a gap.

    Edges: rows from 2 * beat_ms before start_ms are read too, so a hole that began before the range
    and ends inside it is reported (to_ms >= start_ms); a run that started earlier is not a gap.
    A service whose last lifecycle row is not a svc.stop and is more than 2 * beat_ms before the
    range end (end_ms, or now_ms when that is earlier) gets a no_beat gap from that row to the end:
    the service died. A service with no row at all in the window is unknown, not reported."""
    start, end = int(start_ms), int(end_ms)
    lookback = start - 2 * beat_ms
    limit_ms = end if now_ms is None else min(end, int(now_ms))
    rows = []
    for mo in _months(db, lookback, end):
        rows += list(_flow(db, mo).find({"kind": {"$in": list(LIFE_KINDS)}, "at": {"$gte": lookback, "$lt": end}},
                                        {"svc": 1, "run": 1, "seq": 1, "at": 1, "kind": 1, "extra.counts": 1},
                                        max_time_ms=QUERY_MS))
    out = []
    runs = {}
    for r in rows:
        runs.setdefault((r["svc"], r["run"]), []).append(r)
    for (svc, run), items in runs.items():
        items.sort(key=lambda r: r["seq"])
        for a, b in zip(items, items[1:]):
            if b["kind"] != "svc.beat":
                continue
            lost = b["seq"] - a["seq"] - 1 - _counted(b)
            if lost > 0:
                out.append({"svc": svc, "run": run, "from_ms": a["at"], "to_ms": b["at"], "why": "seq_jump",
                            "lost": lost})
    alive = {}
    for r in rows:
        alive.setdefault(r["svc"], []).append(r)
    for svc, items in alive.items():
        items.sort(key=lambda r: (r["at"], r["seq"]))
        for a, b in zip(items, items[1:]):
            if b["kind"] == "svc.start" and b["run"] != a["run"] and a["kind"] != "svc.stop":
                out.append({"svc": svc, "run": a["run"], "from_ms": a["at"], "to_ms": b["at"], "why": "no_stop"})
            elif b["at"] - a["at"] > 2 * beat_ms and not (a["kind"] == "svc.stop" and b["kind"] == "svc.start"):
                out.append({"svc": svc, "run": a["run"], "from_ms": a["at"], "to_ms": b["at"], "why": "no_beat"})
        last = items[-1]
        if last["kind"] != "svc.stop" and limit_ms - last["at"] > 2 * beat_ms:
            out.append({"svc": svc, "run": last["run"], "from_ms": last["at"], "to_ms": limit_ms, "why": "no_beat"})
    out = [g for g in out if g["to_ms"] >= start]
    out.sort(key=lambda g: (g["from_ms"], g["svc"], g["why"]))
    return out
