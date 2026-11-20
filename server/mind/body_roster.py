"""GET /bodies (owner 2026-11-20: the Mac and the StackChan are two bodies of one robot): every kind of body hello
has (server/common/bodies.py: abilities, tuning) and the bodies the ear has seen of each, from the ear's registry
(the `bodies` collection: last declared abilities, first / last seen, live hearing / vision links).
Until the two stacks share one database each stack sees only its own bodies; the kinds are always all of them."""
from . import runlog  # noqa: F401 - puts server/common on sys.path in the repository
import bodies

FIELDS = ("kind", "name", "abilities", "declared", "first_seen_ms", "last_seen_ms", "links", "device")


def _row(doc):
    out = {"id": doc.get("_id"), **{k: doc.get(k) for k in FIELDS}}
    links = out.get("links") if isinstance(out.get("links"), dict) else {}
    out["links"] = {k: int(links.get(k) or 0) for k in ("hearing", "vision")}
    out["online"] = any(out["links"].values())
    return out


def connected(db, kind="stackchan"):
    """True when any body of this kind (all of them, newest or not) has a live hearing or vision link. Read errors
    are raised: the caller decides (the 图库 treats "cannot tell" as connected)."""
    return any(_row(d)["online"] for d in db["bodies"].find({"kind": kind}))


def roster(db):
    seen = [_row(d) for d in db["bodies"].find({}).sort("last_seen_ms", -1).limit(50)]
    kinds = []
    for k in bodies.catalog():
        own = [r for r in seen if r["kind"] == k["kind"]]
        kinds.append({**k, "bodies": own, "online": any(r["online"] for r in own)})
    return {"robot": "hello", "database": getattr(db, "name", None), "kinds": kinds,
            "online": sum(1 for r in seen if r["online"]),
            "unknown": [r for r in seen if r["kind"] not in bodies.KINDS]}
