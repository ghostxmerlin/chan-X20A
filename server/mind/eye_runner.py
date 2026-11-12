"""soul-eye: runs eye.run_minute for each minute while hello.switches.eye is on (plan 2026-11-11-eye).

  python -m mind.eye_runner --settings ~/soul-mind/settings.json

A minute is processed 15 s after it ends, once (cursor in hello.eye_state). After downtime, or the
first time, at most the last 10 minutes are processed. While the switch is off the cursor only moves
on, so turning it on never calls the model for minutes seen while it was off. Status goes to
hello.switches.eye_status every 30 s (the page shows "unknown" when it is older than 90 s).
"""
import argparse
import os
import time

from . import eye, settings, speech_settings
from .settings_store import SettingsStore
from .store import Store

MIN_MS = 60_000
DELAY_MS = 15_000
CATCH_UP_MINUTES = 10


def switch_on(db):
    return (db["switches"].find_one({"_id": "eye"}) or {}).get("on") is True


def write_status(db, running, available, now_ms):
    db["switches"].update_one({"_id": "eye_status"}, {"$set": {"running": running, "available": available, "at": now_ms}},
                              upsert=True)


def tick(db, store, s, env, recordings_root, now_ms):
    """Minutes handed to eye.run_minute this time (with or without frames)."""
    last_ready = settings.minute_floor(now_ms - MIN_MS - DELAY_MS)
    cursor = (db["eye_state"].find_one({"_id": "live"}) or {}).get("cursor_minute")
    if not switch_on(db) or env is None:
        if cursor is None or cursor < last_ready:
            db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": last_ready}}, upsert=True)
        return []
    first = last_ready - (CATCH_UP_MINUTES - 1) * MIN_MS
    first = max(first, ((speech_settings.scope(s)['since'] + MIN_MS - 1) // MIN_MS) * MIN_MS)
    if cursor is not None:
        first = max(first, cursor + MIN_MS)
    done = []
    for m in range(first, last_ready + MIN_MS, MIN_MS):
        if not switch_on(db):                    # 关 pressed during a catch-up: stop before the next paid call
            break
        try:
            eye.run_minute(env, store, s, m, recordings_root, now_ms)
        except Exception as e:                   # noqa: BLE001 - a call may already be paid: never repeat the minute
            failures = (db["eye_state"].find_one({"_id": "live"}) or {}).get("failures", 0) + 1
            db["eye_state"].update_one({"_id": "live"}, {"$set": {
                "failures": failures, "last_error": f"{type(e).__name__}: {str(e)[:160]}",
                "backoff_until": now_ms + eye.BACKOFF_MIN[min(failures - 1, 3)] * MIN_MS}}, upsert=True)
            print(f"eye error in minute {m}: {type(e).__name__}: {str(e)[:160]}", flush=True)
        db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": m}}, upsert=True)
        done.append(m)
    return done


def main():
    ap = argparse.ArgumentParser(description="hello eye: per-minute look at the body-camera frames")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/aidea/agent/.env"))
    ap.add_argument("--settings", default=os.path.expanduser("~/soul-mind/settings.json"))
    ap.add_argument("--recordings", default=os.path.expanduser("~/soul-spike/recordings"))
    a = ap.parse_args()
    from pymongo import MongoClient
    hub = settings.load_env(a.hubmix_env)
    if not hub.get("MONGO_URI"):
        raise SystemExit("MONGO_URI missing in the hubmix env file")
    env = {"base_url": hub.get("BASE_URL", "https://aihubmix.com/v1"), "api_key": hub["API_KEY"]} if hub.get("API_KEY") else None
    db = MongoClient(hub["MONGO_URI"], serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["hello"]
    store = Store(db)
    db["eye_minutes"].create_index([("minute", 1), ("version", 1)], unique=True)
    settings_store = SettingsStore(db, a.settings)
    last_on, last_status = None, 0.0
    print(f"eye: model {eye.EYE_MODEL}, recordings {a.recordings}, api key {'set' if env else 'missing'}", flush=True)
    while True:
        try:
            on = switch_on(db)
            if on != last_on:
                print(f"eye: switch {'on' if on else 'off'}", flush=True)
                last_on, last_status = on, 0.0           # status right away when the switch flips
            if time.monotonic() - last_status >= 30:
                write_status(db, on and env is not None, env is not None, int(time.time() * 1000))
                last_status = time.monotonic()
            s, _ = settings_store.current()
            done = tick(db, store, s, env, a.recordings, int(time.time() * 1000))
            if done:
                print(f"eye: minutes {len(done)} up to {done[-1]}", flush=True)
            time.sleep(5)
        except Exception as e:                   # noqa: BLE001 - keep running; status shows staleness
            print(f"eye error: {type(e).__name__}: {str(e)[:200]}", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
