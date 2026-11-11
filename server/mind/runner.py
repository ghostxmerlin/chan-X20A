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

from . import fusion, package, segments, settings, strong
from .records import Records
from .settings_store import SettingsStore
from .store import LANE, Store

MIN_MS = 60_000
PROCESS_DELAY_MS = 30_000     # v3 (2026-11-11): first version 30 s after the minute; late records revise it
LOOKBACK_MS = 30 * MIN_MS
VISION_LOOKBACK_CAP_MS = 12 * 3600_000
RECHECK_MINUTES = 10
REVISE_MAX_PER_TICK = 3        # a builder or settings change can rewrite the whole recheck window: never a burst


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
    report = store.place_at(end - 1)
    if report:
        st["place"] = {"label": report["label"], "source": "owner_report", "at": report["at"]}
    return "written" if store.put(LANE, minute_ms, pkg, st) is not None else "same"


def day_start(ms, tz):
    t = settings.local_time(ms, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(t.timestamp() * 1000)


def fuse(env, store, s, minutes, now_ms, settings_version=0, lane=LANE, revise=False):
    """Model fusion for these minutes, in order (packages must exist), then segment summaries.
    Nothing is called while the mind switch is off; segment summaries only when a sense was on in
    one of these minutes (both senses off: no model call of any kind)."""
    if env is None or not s["fusion"]["enabled"] or not minutes or not store.enabled():
        return 0
    segs = segments.update(env, store, s, day_start(minutes[0], s["tz"]), now_ms, lane, summarize_now=False)
    titled = [{"start": g["start"], "end": g["end"], "cls": g["cls"], "title": (g.get("summary") or {}).get("title")}
              for g in segs if g["cls"] != "gap"]
    done, sensed = 0, False
    for m in minutes:
        doc = store.latest(lane, m)
        if not doc:
            continue
        cov = doc["strong"]["coverage"]
        sensed = sensed or cov["hearing_s"] + cov["vision_s"] > 0
        prev = store.latest(lane, m - MIN_MS)
        fusion.run_minute(env, store, s, settings_version, m, doc["package"], doc["strong"],
                          {"prev": prev["package"] if prev else None}, lane=lane, now_ms=now_ms,
                          segments=[g for g in titled if g["start"] < m], package_version=doc["version"], revise=revise)
        done += 1
    segments.update(env, store, s, day_start(now_ms, s["tz"]), now_ms, lane, summarize_now=sensed)
    store.set_state(cost_today=round(store.cost_today(s["tz"], now_ms), 4))
    return done


def tick(records, store, s, now_ms, max_catchup_ms=48 * 3600_000, env=None, settings_version=0):
    """Process every ready minute after the cursor; fuse the new ones that are recent enough.
    Returns how many new minutes were processed."""
    last_ready = settings.minute_floor(now_ms - MIN_MS - PROCESS_DELAY_MS)
    state = store.state()
    cursor = state.get("cursor_minute")
    first = max(cursor + MIN_MS, last_ready - max_catchup_ms) if cursor is not None else last_ready - 59 * MIN_MS
    if cursor is not None and last_ready <= cursor:
        return 0                                 # nothing new is ready: no re-check either (once per new minute)
    done = state.get("processed", 0)
    same = state.get("skipped_same", 0)
    count, new, rewritten = 0, [], []
    try:
        if cursor is not None:
            for m in range(cursor - (RECHECK_MINUTES - 1) * MIN_MS, cursor + MIN_MS, MIN_MS):
                if process_minute(records, store, m, s, now_ms) == "same":
                    same += 1
                else:
                    rewritten.append(m)          # late records: a revision if the content changed
        for m in range(first, last_ready + MIN_MS, MIN_MS):
            if process_minute(records, store, m, s, now_ms) == "same":
                same += 1
            count += 1
            done += 1
            new.append(m)
            store.set_state(cursor_minute=m, processed=done, skipped_same=same)
        store.set_state(last_run_ms=now_ms, last_error="", skipped_same=same)
        age_limit = s["fusion"]["max_age_min"] * MIN_MS + MIN_MS + PROCESS_DELAY_MS
        fuse(env, store, s, [m for m in rewritten if now_ms - m <= age_limit][-REVISE_MAX_PER_TICK:], now_ms,
             settings_version, revise=True)
        fuse(env, store, s, [m for m in new if now_ms - m <= age_limit], now_ms, settings_version)
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
    ap.add_argument("--fusion", action="store_true", help="with --rebuild: also run the model over the range")
    ap.add_argument("--catch-up-max-hours", type=float, default=48)
    ap.add_argument("--recordings", default=os.path.expanduser("~/soul-spike/recordings"),
                    help="the ear's recordings folder (body-camera frames live under frames/)")
    a = ap.parse_args()
    from pymongo import MongoClient
    hub = settings.load_env(a.hubmix_env)
    uri = hub.get("MONGO_URI")
    env = {"base_url": hub.get("BASE_URL", "https://aihubmix.com/v1"), "api_key": hub["API_KEY"]} if hub.get("API_KEY") else None
    if not uri:
        raise SystemExit("MONGO_URI missing in the hubmix env file")
    db = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["hello"]
    records, store = Records(db), Store(db)
    store.ensure_indexes()
    settings_store = SettingsStore(db, a.settings)
    if a.rebuild:
        s, version = settings_store.current()
        lo, hi = (_parse_local(t, s["tz"]) for t in a.rebuild)
        hi = min(hi, settings.minute_floor(int(time.time() * 1000) - MIN_MS - PROCESS_DELAY_MS) + MIN_MS)   # never the future
        minutes = list(range(settings.minute_floor(lo), hi, MIN_MS))
        written = sum(process_minute(records, store, m, s, int(time.time() * 1000)) == "written" for m in minutes)
        print(f"rebuilt {a.rebuild[0]}–{a.rebuild[1]}: {written} new versions", flush=True)
        if a.fusion and not store.enabled():
            print("mind is switched off: no model calls (turn 情景 on first)", flush=True)
        elif a.fusion:
            before = store.cost_today(s["tz"], int(time.time() * 1000))
            n = fuse(env, store, s, minutes, int(time.time() * 1000), version)
            print(f"fused {n} minutes, cost ${store.cost_today(s['tz'], int(time.time() * 1000)) - before:.4f}", flush=True)
        return
    token = open(a.token_file).read().strip() if a.token_file else ""
    from . import api
    threading.Thread(target=api.serve, args=(store, token, "127.0.0.1", a.api_port,
                                             {"env": env, "settings_store": settings_store,
                                                                         "recordings_root": a.recordings}), daemon=True).start()
    cursor = store.state().get("cursor_minute")
    print(f"mind: lane {LANE}, cursor {cursor}, api 127.0.0.1:{a.api_port}", flush=True)
    while True:
        try:
            good, version = settings_store.current()      # settings are versioned in the database (phase 1)
            store.set_state(settings_version=version)
            n = tick(records, store, good, int(time.time() * 1000), int(a.catch_up_max_hours * 3600_000), env, version)
            if n:
                print(f"processed {n} minute(s), cursor {store.state().get('cursor_minute')}", flush=True)
            time.sleep(5)
        except Exception as e:                   # noqa: BLE001 - keep running; /state shows the error
            print(f"mind error: {type(e).__name__}: {str(e)[:200]}", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
