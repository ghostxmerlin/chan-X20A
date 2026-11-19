"""Clone the robot's database for the Mac stand-in's own stack (owner 2026-11-19 night: "clone一份现在的数据给mac").
Runs ON THE SERVER with the ear's venv (pymongo): reads MONGO_URI from ~/soul-spike/hello.env, copies every collection
of `hello` into `hello_mac` server-side ($out), then the indexes, then makes the copy safe to run beside the robot:
  - every paid switch off (听悟 / 识别 / 纪要 / Docking), 情景 off, billing (全开) off;
  - pending reminders / active watches cancelled in the COPY (the robot's still fire; two stacks would say each twice).
The source is only read. Refuses to touch an existing non-empty target unless --replace. Prints counts only.

    ~/soul-spike/.venv/bin/python clone_db.py [--src hello] [--dst hello_mac] [--replace] [--dry-run]
"""
import argparse
import re
import time
from pathlib import Path

from pymongo import MongoClient

PAID_SWITCHES = ("tingwu", "eye", "notes", "realtime")
NOTE = "复制给 Mac 时停掉（2026-11-19；机器人那份照常）"


def env_uri():
    for line in (Path.home() / "soul-spike" / "hello.env").read_text().splitlines():
        m = re.match(r"\s*MONGO_URI=(.*)", line)
        if m:
            return m.group(1).strip().strip("'\"")
    raise SystemExit("MONGO_URI missing")


def main(a):
    client = MongoClient(env_uri(), serverSelectionTimeoutMS=8000)
    src, dst = client[a.src], client[a.dst]
    names = sorted(n for n in src.list_collection_names() if not n.startswith("system."))
    have = [n for n in dst.list_collection_names() if dst[n].estimated_document_count()]
    if have and not a.replace:
        raise SystemExit(f"{a.dst} already has {len(have)} non-empty collections (use --replace)")
    print(f"{a.src} -> {a.dst}: {len(names)} collections", flush=True)
    if a.dry_run:
        for n in names:
            print(f"  {n}: {src[n].estimated_document_count()}")
        return
    t0 = time.time()
    for n in names:
        if a.replace:
            dst[n].drop()
        src[n].aggregate([{"$match": {}}, {"$out": {"db": a.dst, "coll": n}}], allowDiskUse=True)
        for name, info in src[n].index_information().items():
            if name == "_id_":
                continue
            keys = info.pop("key")
            info.pop("v", None)
            info.pop("ns", None)
            dst[n].create_index(keys, name=name, **info)
        s, d = src[n].estimated_document_count(), dst[n].estimated_document_count()
        print(f"  {n}: {s} -> {d}{'' if s == d else '  (differs: written to meanwhile?)'}", flush=True)
    now = int(time.time() * 1000)
    for name in PAID_SWITCHES:
        dst["switches"].update_one({"_id": name}, {"$set": {"on": False, "at": now, "by": "clone"}}, upsert=True)
    dst["mode"].update_many({"billing": {"$exists": True}}, {"$set": {"billing": False}})
    dst["mind_state"].update_many({"enabled": {"$exists": True}}, {"$set": {"enabled": False}})
    r = dst["reminders"].update_many({"state": {"$in": ["pending", "held"]}},
                                     {"$set": {"state": "cancelled", "decided_at": now, "note": NOTE}})
    open_watch = [s for s in dst["watches"].distinct("state") if s not in ("done", "cancelled", "expired", "ended", "stopped")]
    w = dst["watches"].update_many({"state": {"$in": open_watch}}, {"$set": {"state": "cancelled", "note": NOTE}}) \
        if open_watch else None
    print(f"paid switches off, billing off, 情景 off; reminders cancelled in the copy: {r.modified_count}; "
          f"watches cancelled: {w.modified_count if w else 0} (states {open_watch}); {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="hello")
    ap.add_argument("--dst", default="hello_mac")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args())
