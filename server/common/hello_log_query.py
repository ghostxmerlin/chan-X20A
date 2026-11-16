"""Read side of hello's run/audit log: flow records (log_YYYYMM) and bodies (logbody_YYYYMM).

Stdlib only; works on a pymongo Database (or anything with db[name].find(...).sort(...).limit(...)
and find_one). Used by mind's read-only API and by scripts. Table layout: see docs/hello-log.md.

    import hello_log_query as q
    rows = q.flows(db, start_ms, end_ms, kind_prefix="call.", has_cost=True, limit=50)
    text = q.body(db, rows[0]["at"], rows[0]["body"]["output"])
    q.ledger(db, "2026-11-16")     # cost per svc/model/currency for one Beijing day
    q.gaps(db, start_ms, end_ms)   # missing heartbeats and seq jumps

Times are ms since epoch; months and days are Beijing time (Asia/Shanghai, UTC+8, no DST).
"""
import re
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))
DAY_MS = 86400000
MAX_LIMIT = 1000


def month_of(at_ms):
    """'YYYYMM' of a ms timestamp in Beijing time."""
    return datetime.fromtimestamp(int(at_ms) / 1000, BJ).strftime("%Y%m")


def _months(start_ms, end_ms):
    """Month suffixes touched by [start_ms, end_ms), newest first."""
    last = datetime.fromtimestamp(max(start_ms, end_ms - 1) / 1000, BJ)
    y, m = last.year, last.month
    first = month_of(start_ms)
    out = []
    while True:
        s = f"{y:04d}{m:02d}"
        out.append(s)
        if s <= first or len(out) > 600:
            return out
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)


def _flow(db, month):
    return db["log_" + month]


def _find_flow(db, flow_id, months):
    for mo in months:
        doc = _flow(db, mo).find_one({"_id": flow_id})
        if doc is not None:
            return doc
    return None


def flows(db, start_ms, end_ms, *, kinds=None, kind_prefix=None, svc=None, trace=None, has_cost=None,
          limit=200, before_id=None):
    """Flow records with start_ms <= at < end_ms, newest first (at descending, then _id descending).
    Page by passing the last _id of the previous page as before_id (same start/end/filters).
    limit is capped at 1000."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    months = _months(start_ms, end_ms)
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
        if row is not None:
            after = (row["at"], before_id)
    out = []
    for mo in months:
        flt = dict(cond, at=dict(at_range))
        if after is not None:
            flt["at"]["$lte"] = after[0]           # ties at after[0] are cut by _id below
        for doc in _flow(db, mo).find(flt).sort([("at", -1), ("_id", -1)]):
            if after is not None and (doc["at"], doc["_id"]) >= after:
                continue
            out.append(doc)
            if len(out) >= limit:
                return out
    return out


def body(db, at_ms, body_id):
    """Full text of a body (flow["body"][name]); None when missing. at_ms picks the month table."""
    doc = db["logbody_" + month_of(at_ms)].find_one({"_id": body_id})
    return None if doc is None else doc.get("text")


def chain(db, trace, around_ms):
    """Every record of one trace within a day either side of around_ms, oldest first."""
    start, end = int(around_ms) - DAY_MS, int(around_ms) + DAY_MS
    out = []
    for mo in _months(start, end):
        out += list(_flow(db, mo).find({"trace": trace, "at": {"$gte": start, "$lt": end}}))
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
    for mo in _months(start, end):
        flt = {"cost": {"$exists": True}, "at": {"$gte": start, "$lt": end}}
        for r in _flow(db, mo).find(flt):
            c = r.get("cost") or {}
            g = groups.setdefault((r.get("svc"), r.get("model"), c.get("cur")),
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
        if cur is not None:
            total[cur] = round(total.get(cur, 0.0) + amount, 9)
        unknown += g["unknown_n"]
    return {"by": by, "total": total, "unknown_n": unknown}


def gaps(db, start_ms, end_ms, *, beat_ms=60000):
    """Holes in the logs inside [start_ms, end_ms): [{svc, run, from_ms, to_ms, why}].
    "seq_jump": the next record of a run skipped seq numbers (real loss), from_ms/to_ms = the two
    records' times. "no_beat": more than 2 * beat_ms between two svc.beat / svc.start / svc.stop of a
    service; run = the earlier one's. The stretch from a svc.stop to the next svc.start is an
    intentional stop and not a gap. Holes at the very edges of the range are not reported."""
    rows = []
    for mo in _months(start_ms, end_ms):
        rows += list(_flow(db, mo).find({"at": {"$gte": int(start_ms), "$lt": int(end_ms)}},
                                        {"svc": 1, "run": 1, "seq": 1, "at": 1, "kind": 1}))
    out = []
    runs = {}
    for r in rows:
        runs.setdefault((r["svc"], r["run"]), []).append(r)
    for (svc, run), items in runs.items():
        items.sort(key=lambda r: r["seq"])
        for a, b in zip(items, items[1:]):
            if b["seq"] > a["seq"] + 1:
                out.append({"svc": svc, "run": run, "from_ms": a["at"], "to_ms": b["at"], "why": "seq_jump"})
    alive = {}
    for r in rows:
        if r["kind"] in ("svc.beat", "svc.start", "svc.stop"):
            alive.setdefault(r["svc"], []).append(r)
    for svc, items in alive.items():
        items.sort(key=lambda r: (r["at"], r["seq"]))
        for a, b in zip(items, items[1:]):
            if b["at"] - a["at"] <= 2 * beat_ms:
                continue
            if a["kind"] == "svc.stop" and b["kind"] == "svc.start":
                continue
            out.append({"svc": svc, "run": a["run"], "from_ms": a["at"], "to_ms": b["at"], "why": "no_beat"})
    out.sort(key=lambda g: (g["from_ms"], g["svc"], g["why"]))
    return out
