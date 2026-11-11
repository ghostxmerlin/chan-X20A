"""soul-notes: conversation notes, ten seconds after each minute (plan 2026-11-11-conversation-notes).

  python -m mind.notes_runner --settings ~/soul-mind/settings.json

Each pass reads people's sentences up to the start of the current minute (re-scanning the last three
minutes for late rows; refs already handled are skipped), cuts them into conversations (a gap of more
than 60 s ends one) and updates the running one with one luna call. While hello.switches.notes is off,
the cursor still moves (nothing is caught up later), sentences are not collected, and open
conversations still close by rule; no model call is made.
"""
import argparse
import os
import time

from . import notes, selfvoice, settings
from .records import Records
from .settings_store import SettingsStore
from .store import Store

MIN_MS = 60_000
DELAY_MS = 5_000                 # notes for minute M are written by about M+15 s; mind builds M at M+20 s
CLOSE_MS = 60_000 + 30_000       # a row lands only after its utterance ends (up to 20 s): wait 30 s more to close
MAX_CALLS_PER_TICK = 3           # never a burst (after downtime the backlog is recorded, not summarised)
FRESH_MS = 10 * MIN_MS           # a conversation whose last sentence is older than this is recorded without a call
RESCAN_MS = 3 * MIN_MS
RECENT_MS = 2 * MIN_MS
SEEN_KEEP_MS = 15 * MIN_MS


def switch_on(db):
    return (db["switches"].find_one({"_id": "notes"}) or {}).get("on") is True


def write_status(db, running, available, now_ms):
    db["switches"].update_one({"_id": "notes_status"}, {"$set": {"running": running, "available": available, "at": now_ms}},
                              upsert=True)


def _open(db):
    found = [c for c in db["conversations"].find({"end_ms": None}, {"_id": 0})]
    return max(found, key=lambda c: c["start_ms"]) if found else None


def _close(db, conv):
    db["conversations"].update_one({"cid": conv["cid"]}, {"$set": {"end_ms": conv["last_ms"]}})


def _recent(db, records, conv):
    refs = {tuple(r) for r in conv["refs"]}
    got = notes.new_sentences(db, records, max(conv["start_ms"], conv["last_ms"] - RECENT_MS), conv["last_ms"] + 1, set())
    return [x for x in got if tuple(x["ref"]) in refs]


def _late_home(db, sentences, conv, since_ms):
    """Sentences that belong to an already closed conversation (a late row): (home, sentence) pairs."""
    closed = [c for c in db["conversations"].find({"start_ms": {"$gte": since_ms - 6 * 3600_000}}, {"_id": 0})
              if c.get("end_ms") is not None]
    out = []
    for x in sentences:
        if conv is not None and x["at"] >= conv["start_ms"]:
            continue
        home = next((c for c in closed if c["start_ms"] - notes.GAP_MS <= x["at"] <= c["end_ms"] + notes.GAP_MS), None)
        if home is not None:
            out.append((home, x))
    return out


def _attach(db, home, x):
    c = db["conversations"].find_one({"cid": home["cid"]}, {"_id": 0})
    c["refs"] = list(c["refs"]) + [list(x["ref"])]
    c["last_ms"], c["end_ms"] = max(c["last_ms"], x["at"]), max(c["end_ms"], x["at"])
    db["conversations"].replace_one({"cid": c["cid"]}, c, upsert=True)


def tick(db, store, records, s, env, now_ms):
    """How many model calls this pass made."""
    until = settings.minute_floor(now_ms - DELAY_MS)
    state = db["notes_state"].find_one({"_id": "live"}) or {}
    cursor = state.get("cursor_ms")
    if cursor is not None and until <= cursor:
        return 0
    seen_rows = [r for r in state.get("seen", []) if r[3] >= until - SEEN_KEEP_MS]
    seen = {tuple(r[:3]) for r in seen_rows}
    since = (cursor if cursor is not None else until - 10 * MIN_MS) - RESCAN_MS
    sentences = notes.new_sentences(db, records, since, until, seen)

    def handled(batch):          # saved at once: a crash later in this pass never sends them again
        seen_rows.extend(list(x["ref"]) + [x["at"]] for x in batch)
        db["notes_state"].update_one({"_id": "live"}, {"$set": {"seen": seen_rows}}, upsert=True)

    conv, calls = _open(db), 0
    for home, x in _late_home(db, sentences, conv, since):
        _attach(db, home, x)                     # a late row of a closed conversation: recorded, no call
        handled([x])
    late = {tuple(x["ref"]) for x in sentences if tuple(x["ref"]) in {tuple(r[:3]) for r in seen_rows}}
    sentences = [x for x in sentences if tuple(x["ref"]) not in late]
    if not switch_on(db) or env is None:
        handled(sentences)
        if conv and until - conv["last_ms"] > CLOSE_MS:
            _close(db, conv)
    else:
        def summarise(target, batch, **kw):
            nonlocal calls
            fresh = until - batch[-1]["at"] <= FRESH_MS and calls < MAX_CALLS_PER_TICK
            out = notes.update(env, store, s, target, batch, now_ms, until_ms=until, call=fresh, **kw)
            calls += 1 if fresh else 0
            handled(batch)
            return out

        seg = notes.segment(conv, sentences, until, close_after=CLOSE_MS)
        if seg["append"]:
            conv = summarise(conv, seg["append"], recent=_recent(db, records, conv))
        if seg["close"] and conv:
            _close(db, conv)
        for i, group in enumerate(seg["new"]):
            conv = summarise(None, group)
            if i < len(seg["new"]) - 1:
                _close(db, conv)
        conv = _open(db)
        if conv and until - conv["last_ms"] > CLOSE_MS:
            _close(db, conv)
    db["notes_state"].update_one({"_id": "live"}, {"$set": {"cursor_ms": until, "seen": seen_rows}}, upsert=True)
    return calls


def main():
    ap = argparse.ArgumentParser(description="hello notes: conversation notes every minute")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/aidea/agent/.env"))
    ap.add_argument("--settings", default=os.path.expanduser("~/soul-mind/settings.json"))
    ap.add_argument("--clip-texts", default=os.path.expanduser("~/soul-mind/clip_texts.json"))
    a = ap.parse_args()
    from pymongo import MongoClient
    hub = settings.load_env(a.hubmix_env)
    if not hub.get("MONGO_URI"):
        raise SystemExit("MONGO_URI missing in the hubmix env file")
    env = {"base_url": hub.get("BASE_URL", "https://aihubmix.com/v1"), "api_key": hub["API_KEY"]} if hub.get("API_KEY") else None
    db = MongoClient(hub["MONGO_URI"], serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["hello"]
    db["conversations"].create_index([("cid", 1)], unique=True)
    db["conversations"].create_index([("start_ms", 1)])
    db["conversation_versions"].create_index([("cid", 1), ("version", 1)])
    store, records = Store(db), Records(db)
    settings_store = SettingsStore(db, a.settings)
    print(f"notes: model {notes.NOTES_MODEL}, {selfvoice.load(a.clip_texts)} hello voice clips, "
          f"api key {'set' if env else 'missing'}", flush=True)
    last_on, last_status = None, 0.0
    while True:
        try:
            on = switch_on(db)
            if on != last_on:
                print(f"notes: switch {'on' if on else 'off'}", flush=True)
                last_on, last_status = on, 0.0
            if time.monotonic() - last_status >= 30:
                write_status(db, on and env is not None, env is not None, int(time.time() * 1000))
                last_status = time.monotonic()
            s, _ = settings_store.current()
            n = tick(db, store, records, s, env, int(time.time() * 1000))
            if n:
                print(f"notes: {n} update(s)", flush=True)
            time.sleep(5)
        except Exception as e:                   # noqa: BLE001 - keep running; status shows staleness
            print(f"notes error: {type(e).__name__}: {str(e)[:200]}", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
