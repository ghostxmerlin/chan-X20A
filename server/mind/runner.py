"""mind runner: every minute, build the minute package and strong elements for the minute that
ended PROCESS_DELAY_MS ago; catch up after downtime; re-check the last 10 minutes for late rows.

  python -m mind.runner                          run (also serves the read-only API)
  python -m mind.runner --rebuild FROM TO        rebuild a local-time range, e.g. 2026-11-10T00:00
"""
import argparse
import datetime
import os
import threading
import time
from zoneinfo import ZoneInfo

from . import package, settings, strong
from .records import Records
from .store import LANE, Store

MIN_MS = 60_000
PROCESS_DELAY_MS = 80_000
LOOKBACK_MS = 30 * MIN_MS
VISION_LOOKBACK_CAP_MS = 12 * 3600_000
RECHECK_MINUTES = 10


def lookback_start(records, minute_ms):
    """30 min back, or further to the start of a vision session still open at this minute (faces that
    sat still sent no events since they were found), at most 12 h."""
    start = minute_ms - LOOKBACK_MS
    for s in records.sessions(minute_ms, minute_ms + MIN_MS):
        if s.get("kind") == "vision" and s.get("start_ms") is not None:
            start = min(start, max(s["start_ms"], minute_ms - VISION_LOOKBACK_CAP_MS))
    return start


def process_minute(records, store, minute_ms, s, now_ms):
    end = minute_ms + MIN_MS
    window = records.window(minute_ms, end)
    lookback = records.window(minute_ms - LOOKBACK_MS, end, vision_start_ms=lookback_start(records, minute_ms))
    pkg = package.build(window, minute_ms, s, lookback)
    st = strong.evaluate(pkg, lookback, minute_ms, s, now_ms)
    return "written" if store.put(LANE, minute_ms, pkg, st) is not None else "same"


def tick(records, store, s, now_ms, max_catchup_ms=48 * 3600_000):
    """Process every ready minute after the cursor; returns how many new minutes were processed."""
    last_ready = settings.minute_floor(now_ms - MIN_MS - PROCESS_DELAY_MS)
    state = store.state()
    cursor = state.get("cursor_minute")
    first = max(cursor + MIN_MS, last_ready - max_catchup_ms) if cursor is not None else last_ready - 59 * MIN_MS
    if cursor is not None and last_ready <= cursor:
        return 0                                 # nothing new is ready: no re-check either (once per new minute)
    done = state.get("processed", 0)
    same = state.get("skipped_same", 0)
    count = 0
    try:
        if cursor is not None:
            for m in range(cursor - (RECHECK_MINUTES - 1) * MIN_MS, cursor + MIN_MS, MIN_MS):
                if process_minute(records, store, m, s, now_ms) == "same":
                    same += 1
        for m in range(first, last_ready + MIN_MS, MIN_MS):
            if process_minute(records, store, m, s, now_ms) == "same":
                same += 1
            count += 1
            done += 1
            store.set_state(cursor_minute=m, processed=done, skipped_same=same)
        store.set_state(last_run_ms=now_ms, last_error="", skipped_same=same)
    except Exception as e:                       # noqa: BLE001 - recorded, then the caller retries
        try:
            store.set_state(last_error=f"{type(e).__name__}: {str(e)[:200]}", last_run_ms=now_ms)
        finally:
            raise
    return count


def _parse_local(text, tz):
    return int(datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description="hello mind: minute packages and strong elements")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/aidea/agent/.env"))
    ap.add_argument("--settings", default=os.path.expanduser("~/soul-mind/settings.json"))
    ap.add_argument("--token-file")
    ap.add_argument("--api-port", type=int, default=52740)
    ap.add_argument("--rebuild", nargs=2, metavar=("FROM", "TO"))
    ap.add_argument("--catch-up-max-hours", type=float, default=48)
    a = ap.parse_args()
    from pymongo import MongoClient
    uri = settings.load_env(a.hubmix_env).get("MONGO_URI")
    if not uri:
        raise SystemExit("MONGO_URI missing in the hubmix env file")
    db = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["hello"]
    records, store = Records(db), Store(db)
    store.ensure_indexes()
    if a.rebuild:
        s, err = settings.load_checked(a.settings, None)
        if err:
            raise SystemExit(f"settings file does not parse: {err}")
        lo, hi = (_parse_local(t, s["tz"]) for t in a.rebuild)
        hi = min(hi, settings.minute_floor(int(time.time() * 1000) - MIN_MS - PROCESS_DELAY_MS) + MIN_MS)   # never the future
        written = sum(process_minute(records, store, m, s, int(time.time() * 1000)) == "written"
                      for m in range(settings.minute_floor(lo), hi, MIN_MS))
        print(f"rebuilt {a.rebuild[0]}–{a.rebuild[1]}: {written} new versions", flush=True)
        return
    token = open(a.token_file).read().strip() if a.token_file else ""
    from . import api
    threading.Thread(target=api.serve, args=(store, token, "127.0.0.1", a.api_port), daemon=True).start()
    cursor = store.state().get("cursor_minute")
    print(f"mind: lane {LANE}, cursor {cursor}, api 127.0.0.1:{a.api_port}", flush=True)
    good = None
    while True:
        try:
            good, err = settings.load_checked(a.settings, good)
            store.set_state(settings_error=err)
            n = tick(records, store, good, int(time.time() * 1000), int(a.catch_up_max_hours * 3600_000))
            if n:
                print(f"processed {n} minute(s), cursor {store.state().get('cursor_minute')}", flush=True)
            time.sleep(5)
        except Exception as e:                   # noqa: BLE001 - keep running; /state shows the error
            print(f"mind error: {type(e).__name__}: {str(e)[:200]}", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
