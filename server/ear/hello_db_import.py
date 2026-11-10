"""Import the ear's existing recordings into MongoDB "hello" (2026-11-10). Safe to re-run.

  python hello_db_import.py [--recordings ~/soul-spike/recordings] [--hubmix-env ~/aidea/agent/.env] [--dry-run]

Reads every <recordings>/<YYYYMMDD>/<HHMMSS-source-cN>/ folder: meta.json → sessions,
events.jsonl → speech (speech end events) and sounds (sound end events). Rows are upserted
by (session, id), so running it again changes nothing. Audio files are referenced, not copied.
"""
import argparse
import json
import os
import sys

import hello_db
import tingwu


def read_session(folder):
    meta_path = os.path.join(folder, "meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    session = hello_db.session_key(folder)
    audio = meta.get("audio") or {}
    doc = {"session": session, "source": meta.get("source"), "peer": meta.get("peer"), "start": meta.get("start"),
           "start_ms": meta.get("start_ms"), "end": meta.get("end"), "end_ms": meta.get("end_ms"),
           "audio": {k: audio.get(k) for k in ("format", "sample_rate", "frame_ms", "framing")},
           "device": audio.get("device"), "recording": folder, "counts": meta.get("counts"),
           "models": (meta.get("server_hello") or {}).get("models"), "imported": True}
    speech, sounds = [], []
    events = os.path.join(folder, "events.jsonl")
    if os.path.exists(events):
        with open(events, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "speech" and ev.get("phase") == "end" and ev.get("id"):
                    speech.append(hello_db.speech_doc(session, ev))
                elif ev.get("type") == "sound" and ev.get("phase") == "end" and ev.get("id"):
                    sounds.append(hello_db.sound_doc(session, ev))
    return doc, speech, sounds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recordings", default=os.path.expanduser("~/soul-spike/recordings"))
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/aidea/agent/.env"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    folders = sorted(os.path.join(a.recordings, d, s) for d in os.listdir(a.recordings)
                     if os.path.isdir(os.path.join(a.recordings, d)) for s in os.listdir(os.path.join(a.recordings, d)))
    db = None
    if not a.dry_run:
        from pymongo import ReplaceOne
        uri = tingwu.load_env(a.hubmix_env).get("MONGO_URI")
        if not uri:
            sys.exit("MONGO_URI not found in the hubmix env file")
        db = hello_db.open_db(uri)
    totals = {"sessions": 0, "speech": 0, "sounds": 0}
    for folder in folders:
        got = read_session(folder)
        if not got:
            continue
        doc, speech, sounds = got
        totals["sessions"] += 1; totals["speech"] += len(speech); totals["sounds"] += len(sounds)
        if db is None:
            continue
        db.sessions.update_one({"session": doc["session"]}, {"$set": doc}, upsert=True)
        if speech:
            db.speech.bulk_write([ReplaceOne({"session": r["session"], "id": r["id"]}, r, upsert=True) for r in speech], ordered=False)
        if sounds:
            db.sounds.bulk_write([ReplaceOne({"session": r["session"], "id": r["id"]}, r, upsert=True) for r in sounds], ordered=False)
    print(("dry run: " if a.dry_run else "imported: ") + ", ".join(f"{k} {v}" for k, v in totals.items()))
    if db is not None:
        print("in database:", {c: db[c].count_documents({}) for c in ("sessions", "speech", "sounds", "tingwu")})


if __name__ == "__main__":
    main()
