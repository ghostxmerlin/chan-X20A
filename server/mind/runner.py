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

from . import fusion, package, pause, runlog, segments, selfvoice, settings, speak, strong, speech_settings
from .records import Records
from .settings_store import SettingsStore
from .store import LANE, Store

MIN_MS = 60_000
PROCESS_DELAY_MS = 20_000     # first version 20 s after the minute (eye lands at 15–18 s); late records revise it
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
    if report:                                   # label None: set to unknown (place.py); by: robot / voice (page: none)
        st["place"] = {"label": report.get("label"), "source": "owner_report", "at": report["at"],
                       **({"by": report["by"]} if report.get("by") else {})}
    return "written" if store.put(LANE, minute_ms, pkg, st) is not None else "same"


def day_start(ms, tz):
    t = settings.local_time(ms, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(t.timestamp() * 1000)


def fuse(env, store, s, minutes, now_ms, settings_version=0, lane=LANE, revise=False, paused_fn=None):
    """Model fusion for these minutes, in order (packages must exist), then segment summaries.
    Nothing is called while the mind switch is off or hello is paused (paused_fn, mind/pause.py: nobody there or
    today's cap; those minutes are not caught up later); segment summaries only when a sense was on in one of these
    minutes (both senses off: no model call of any kind)."""
    s = speech_settings.effective(s)
    minutes = [m for m in minutes if m >= speech_settings.scope(s)['since']]
    if env is None or not s["fusion"]["enabled"] or not minutes or not store.enabled() or (paused_fn and paused_fn()):
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


def tick(records, store, s, now_ms, max_catchup_ms=48 * 3600_000, env=None, settings_version=0, paused_fn=None):
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
             settings_version, revise=True, paused_fn=paused_fn)
        fuse(env, store, s, [m for m in new if now_ms - m <= age_limit], now_ms, settings_version, paused_fn=paused_fn)
    except Exception as e:                       # noqa: BLE001 - recorded, then the caller retries
        try:
            store.set_state(last_error=f"{type(e).__name__}: {str(e)[:200]}", last_run_ms=now_ms)
        finally:
            raise
    return count


# ---- modes (step 1, plan task A4): the guard and the hooks around ModeStore / Billing -----------------------------
ROUND_GAP_MS = 10_000          # a robot / DualKey 说一轮 at least 10 s after the last round started (too_soon)


class RoundGate:
    """Rounds started from a mode input (forward, a tap in talk) run on a background thread. `running` covers that
    thread; `last` is when the last round started (the page's 说一轮 notes its start too)."""
    def __init__(self):
        self.running = False
        self.last = None

    def claim(self, now_ms):
        """Guard part for say_round (called under ModeStore's lock: memory only). None = go, the start is reserved."""
        if self.running or speak._SAY_LOCK.locked():
            return 'busy'
        if self.last is not None and now_ms - self.last < ROUND_GAP_MS:
            return 'too_soon'
        self.last = now_ms
        return None

    def note(self, now_ms):
        self.last = now_ms


class _BillingCtx:
    """What billing.Billing needs (see its docstring), on the live database: the same writes the page's switches do."""
    def __init__(self, db, store, settings_store, tts, say_worker, say_auto, mode_store, clock_ms):
        self.db, self.store, self.settings_store, self.tts = db, store, settings_store, tts
        self.say_worker, self.say_auto, self.mode_store, self.clock_ms = say_worker, say_auto, mode_store, clock_ms

    def set_switch(self, name, on):
        with runlog.switch(name, runlog.read_switch(self.db, name), bool(on), 'mode'):
            self.db['switches'].update_one({'_id': name}, {'$set': {'on': bool(on), 'at': self.clock_ms(), 'by': 'mode'}},
                                           upsert=True)

    def switch_on(self, name):
        return (self.db['switches'].find_one({'_id': name}) or {}).get('on') is True

    def set_mind(self, on):
        with runlog.switch('mind', runlog.value(self.store.enabled), bool(on), 'mode'):
            self.store.set_state(enabled=bool(on), switched_at=self.clock_ms())

    def set_mouth(self, mode):
        was = (self.db['switches'].find_one({'_id': 'mouth'}) or {}).get('mode')
        with runlog.switch('mouth', was, mode, 'mode'):
            self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': mode, 'at': self.clock_ms(), 'by': 'mode'}},
                                           upsert=True)
        if was == 'tts' and mode != 'tts' and self.tts:
            speak.cancel_pending(self.tts)

    def family_on(self):
        from . import say_round
        return bool(self.settings_store) and say_round.family_on(self.settings_store.current()[0])

    def set_family(self, on):
        """One settings version with speech.round.family changed, like the page's familySettingsBody."""
        import copy
        from .settings_store import SettingsConflict
        if not self.settings_store:
            return
        note = '家人声音 开' if on else '家人声音 关（进脱口秀）'
        for attempt in range(3):
            s, version = self.settings_store.current()
            s = copy.deepcopy(s)
            s['speech']['round'] = {**(s['speech'].get('round') or {}), 'family': bool(on)}
            try:
                self.settings_store.save(s, note, expected_version=version)
                break
            except SettingsConflict:
                if attempt == 2:
                    raise
        speak.cancel_pending()                   # as the page's settings save: a round in writing was for the other voice

    def cancel(self):
        speak.cancel_pending(self.tts)

    def mode(self):
        return self.mode_store.current()['mode']


def wire_modes(db, store, *, settings_store, tts, say_worker, say_auto, say_round_fn, speaking_fn=None,
               clock_ms=lambda: int(time.time() * 1000), log=print, chat=None):
    """Build ModeStore + Billing with the guard and the hook -> (mode_store, billing, round_gate). hello reports its own
    mode (ModeStore.report); the hook carries out the reported effects.

    say_round_fn(trigger, is_current) runs one 说一轮 (live: speak.say_round, today's pipeline); None = not here.
    chat (chat.Chat, chat v0): runs only while the mirrored mode is chat; entering chat starts it, leaving stops it and
    cancels its speech (its mode_fn reads this ModeStore).
    Startup (owner 2026-11-14 evening, "启动默认全开"): billing on and preset 9's server half: 听悟 / 识别 / 纪要 / 情景 on,
    嘴 TTS, 提前写 + 连续说 on when the mirrored mode is talk. Billing gates nothing; only the mode holds paid work.
    """
    from .billing import Billing
    from .mode_store import ModeStore
    gate = RoundGate()
    mouth = lambda: (db['switches'].find_one({'_id': 'mouth'}) or {}).get('mode', 'clips')
    holder = {}

    def guard(effect, meta):
        """Called under ModeStore's lock (talk_enter included: None, Billing.talk_enter decides itself and does nothing
        outside talk). meta['mode'] is the mode after the change (the reported one for hello's reports)."""
        ms = holder['ms']
        if effect in ('say_round', 'prefetch_on') and meta.get('mode') != 'talk':
            return 'only_talk'               # review I2: paid talk-show work never starts outside talk, whatever came in
        if effect == 'say_round':
            if say_round_fn is None:
                return 'not_implemented'
            if mouth() != 'tts':
                return 'mouth_not_tts'
            return gate.claim(clock_ms())
        if effect == 'prefetch_on' and say_worker is None:
            return 'not_implemented'
        return None

    if speaking_fn is None:
        speaking_fn = lambda: gate.running or speak.speaking()
    ms = ModeStore(db, guard=guard, speaking_fn=speaking_fn, clock_ms=clock_ms)
    holder['ms'] = ms
    billing = Billing(_BillingCtx(db, store, settings_store, tts, say_worker, say_auto, ms, clock_ms))

    def start_round(meta, is_current):
        trigger = {'source': meta.get('source'), 'input': meta.get('input'), 'sid': meta.get('sid')}
        gate.running = True

        def run():
            try:
                say_round_fn(trigger, is_current)
            except Exception as e:                # noqa: BLE001 - the input was answered already; say why here
                log(f'mind mode: say_round ({trigger["source"]}): {type(e).__name__}: {str(e)[:160]}', flush=True)
            finally:
                gate.running = False
        threading.Thread(target=run, daemon=True).start()

    def talk_off(now):
        if say_auto is not None:
            say_auto.set_enabled(False, now)
        if say_worker is not None:
            say_worker.set_enabled(False, now)            # drops the stock

    def prefetch_on(now, is_current):
        if say_worker is None or not is_current():
            return
        say_worker.set_enabled(True, now)
        if not is_current():                              # replaced meanwhile: the later input / a 0 wins
            say_worker.set_enabled(False, now)

    def on_change(old, new, effects, meta, now):
        """Carries the effects out, each on its own (one failing must not keep the rest, e.g. talk_leave). Then a
        safety net for hello's reports (step 1b): outside talk 提前写 / 连续说 are off whatever effects came along
        (a report that lost its talk_leave on the way must not leave them spending)."""
        is_current = lambda: ms.seq() == new['seq']
        # chat v0 first (review M2): leave() stops chat's speech, which must not cancel a round / talk_enter this same
        # report starts below.
        if chat is not None:                     # the worker lives exactly as long as the chat mode
            try:
                if new['mode'] == 'chat' and old['mode'] != 'chat':
                    chat.enter(now)
                elif old['mode'] == 'chat' and new['mode'] != 'chat':
                    chat.leave(now)
            except Exception as ex:           # noqa: BLE001 - logged; the mode change itself happened
                log(f'mind mode: chat {new["mode"]}: {type(ex).__name__}: {str(ex)[:160]}', flush=True)
        crowd_tap = new['mode'] == 'crowd' and old['mode'] == 'crowd' and meta.get('input') == 'tap'
        for e in effects:
            try:
                if e == 'stop_speech' and crowd_tap:
                    # 围观 spike (owner 2026-11-14): a tap in crowd means "look and greet" (the ear's realtime session
                    # does that from the same report), never "stop". hello's rules still attach stop_speech to a tap
                    # while she speaks; the firmware rules / vectors get aligned in a later build.
                    log('mind mode: crowd tap = greet, stop_speech not run', flush=True)
                elif e == 'stop_speech':
                    speak.cancel_pending(tts)
                elif e == 'talk_leave':
                    talk_off(now)
                elif e == 'talk_enter':
                    billing.talk_enter(now, is_current=is_current)
                elif e == 'say_round':
                    start_round(meta, is_current)
                elif e == 'prefetch_on':
                    prefetch_on(now, is_current)
                elif e == 'prefetch_off' and say_worker is not None:
                    say_worker.set_enabled(False, now)
            except Exception as ex:               # noqa: BLE001 - logged, the next effect still runs
                log(f'mind mode: effect {e} failed: {type(ex).__name__}: {str(ex)[:160]}', flush=True)
        on = (say_worker is not None and say_worker.enabled()) or (say_auto is not None and say_auto.enabled())
        if new['mode'] != 'talk' and on:
            log(f'mind mode: {new["mode"]} with 提前写 / 连续说 on (no talk_leave came): turned off', flush=True)
            talk_off(now)
    ms.on_change.append(on_change)
    if chat is not None:
        chat.mode_fn = lambda: ms.current()['mode']
        if ms.current()['mode'] == 'chat':
            chat.enter(clock_ms())               # mind restarted while hello was chatting

    # Owner 2026-11-14 evening "启动默认全开" is hello's power-on: since collector-0.4.96 the robot sends 9 itself at
    # power-on. Owner 2026-11-15 ("重启后保持原来的状态"): mind's own start (a deploy, a crash) changes no switch; 听悟 /
    # 识别 / 纪要 / 情景 / 围观 / 嘴 stay as stored. billing.on('boot') still keeps 围观 as stored if anything calls it.
    log('mind mode: start keeps every switch as it was', flush=True)
    return ms, billing, gate


def _parse_local(text, tz):
    return int(datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description="hello mind: minute packages and strong elements")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/soul-spike/hello.env"))
    ap.add_argument("--settings", default=os.path.expanduser("~/soul-mind/settings.json"))
    ap.add_argument("--token-file")
    ap.add_argument("--api-port", type=int, default=52740)
    ap.add_argument("--rebuild", nargs=2, metavar=("FROM", "TO"))
    ap.add_argument("--fusion", action="store_true", help="with --rebuild: also run the model over the range")
    ap.add_argument("--catch-up-max-hours", type=float, default=48)
    ap.add_argument("--roles", default=os.path.expanduser("~/soul-mind/roles"), help="hello's speaking roles (config/roles)")
    ap.add_argument("--persona", default=os.path.expanduser("~/soul-mind/persona.md"),
                    help="hello's persona file (config/persona, owner 2026-11-18); missing = the chat role's 性格")
    ap.add_argument("--tts-url", default="http://127.0.0.1:52750", help="soul-tts API on the loopback")
    ap.add_argument("--tts-token-file", default=os.path.expanduser("~/soul-tts/api.token"), help="soul-tts API token")
    ap.add_argument("--clip-texts", default=os.path.expanduser("~/soul-mind/clip_texts.json"),
                    help="hello's voice clip texts (selfvoice); missing: nothing is treated as her voice")
    ap.add_argument("--recordings", default=os.path.expanduser("~/soul-spike/recordings"),
                    help="the ear's recordings folder (body-camera frames live under frames/)")
    a = ap.parse_args()
    from pymongo import MongoClient
    hub = settings.load_env(a.hubmix_env)
    runlog.start("mind", hub, a)                 # run/audit log (svc.start / beat / stop); never stops the start
    uri = hub.get("MONGO_URI")
    env = {"base_url": hub.get("BASE_URL", "https://aihubmix.com/v1"), "api_key": hub["API_KEY"]} if hub.get("API_KEY") else None
    if not uri:
        raise SystemExit("MONGO_URI missing in the hubmix env file")
    db = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["hello"]
    print(f"mind: {selfvoice.load(a.clip_texts)} hello voice clips from {a.clip_texts}", flush=True)
    records, store = Records(db), Store(db)
    runlog.watch(db, store)
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
    from . import api, say_auto, say_prefetch, say_round, speech_context
    roles = speak.load_roles(a.roles)
    tts = speak.TtsHttp.from_file(a.tts_url, a.tts_token_file)
    mouth_fn = lambda: (db["switches"].find_one({"_id": "mouth"}) or {}).get("mode", "clips")
    say_worker = say_prefetch.Worker(
        store, settings_fn=lambda: settings_store.current()[0], mouth_fn=mouth_fn,
        ctx_fn=lambda s, now_ms: speech_context.build(db, store, s, now_ms),
        write_fn=lambda ctx, now_ms: say_round.write_round(env, store, settings_store.current()[0], roles, ctx, now_ms,
                                                           trigger={"source": "prefetch"}))
    # 连续说 (2026-11-14): every 2–3 minutes, play the high-score lines 提前写 already wrote.
    auto_worker = say_auto.Worker(
        store, prefetch=say_worker, settings_fn=lambda: settings_store.current()[0], mouth_fn=mouth_fn,
        play_fn=lambda s, now_ms, is_current: speak.auto_play(env, store, s, tts, now_ms, worker=say_worker, is_current=is_current))
    say_auto_live = auto_worker if env and tts else None

    def say_round_live(trigger, is_current):
        """说一轮 from a mode input (forward / a tap in talk): today's pipeline, as the page's /say/round runs it."""
        s, version = settings_store.current()
        return speak.say_round(env, store, s, speak.load_roles(a.roles), tts, int(time.time() * 1000), worker=say_worker,
                               trigger=trigger, settings_version=version,
                               is_current=lambda: is_current() and settings_store.current()[1] == version and mouth_fn() == 'tts')
    # Modes (step 1b): hello owns the mode and reports it (/mode/report); mind mirrors it and runs its effects
    # through the guard / hooks in wire_modes. The master switch and presets 0 / 9 stay on mind.
    # Chat v0 (plan 2026-11-14-chat-v0): the ear POSTs /chat/heard in chat mode; the worker runs only in chat.
    from . import chat as chat_mod
    chat = chat_mod.Chat(db, store, settings_fn=lambda: settings_store.current()[0],
                         roles_fn=lambda: speak.load_roles(a.roles), tts=tts, env=env, mouth_fn=mouth_fn,
                         persona_path=a.persona)
    chat.ensure_indexes()
    mode_store, billing, round_gate = wire_modes(db, store, settings_store=settings_store, tts=tts, say_worker=say_worker,
                                                 say_auto=say_auto_live, say_round_fn=say_round_live if env and tts else None,
                                                 chat=chat)
    say_worker.mode_fn = auto_worker.mode_fn = lambda: mode_store.current()["mode"]
    # The pause (2026-11-17, mind/pause.py): nobody there for pause.away_min minutes, or today's spend over the one
    # daily cap -> the paid items rest; the owner's switches stay as they are. mind owns it; the ear reports presence.
    ledger = None
    try:
        import hello_log_query as log_query      # ~/soul-mind/hello_log_query.py (mind's deploy ships it)
        ledger = lambda day: log_query.ledger(db, day)
    except ImportError:
        print("mind pause: hello_log_query not deployed, today's spend unknown (no budget pause)", flush=True)
    pause_ctl = pause.Controller(db, settings_fn=lambda: settings_store.current()[0], ledger_fn=ledger)
    say_worker.paused_fn = auto_worker.paused_fn = pause_ctl.paused
    threading.Thread(target=pause_ctl.run, daemon=True).start()
    # 日常 (L4 2026-11-18): reminders / schedule / watches, said by hello through the ear's say ask (Docking)
    from . import daily as daily_mod, tool_impls as _ti
    _ask = _ti.ear_asker(token, timeout=10)

    def _word(text, hold_s):
        try:
            _ask("show_word", {"text": text, "hold_s": hold_s})
        except Exception:                        # noqa: BLE001 - old firmware / robot away: no word
            pass

    def _daily_say(text):
        try:
            _ask("say", {"text": text})
            _word("提醒", 6)
            return True
        except Exception:                        # noqa: BLE001 - busy / not in Docking / ear down: try again later
            return False
    daily = daily_mod.Daily(db, say=_daily_say, settings_fn=lambda: settings_store.current()[0])
    threading.Thread(target=daily.run, daemon=True).start()
    # 深入研究 (2026-11-18, mind/research.py): background jobs on aihubmix sol; done -> hello says the short answer
    from . import research as research_mod

    def _research_report(job):
        ans = job.get("answer") or {}
        spoken = ans.get("spoken") if isinstance(ans, dict) else None
        if job.get("state") == "done" and spoken:
            _word("查好了", 600)                  # under the eyes until it is said (or 10 min)
            daily.announce(f"主人，你让我研究的「{str(job.get('topic'))[:20]}」查好了：{spoken}", by="research")
        else:
            daily.announce(f"主人，「{str(job.get('topic'))[:20]}」没研究出来，原因记在工具页里了。", by="research")

    def _day_left():
        v = pause_ctl.view() or {}
        cap, spent = v.get("cap_cny"), v.get("spent_cny")
        return (cap - spent) if isinstance(cap, (int, float)) and isinstance(spent, (int, float)) else None
    research = research_mod.Runner(db, env, settings_fn=lambda: settings_store.current()[0], report=_research_report,
                                   day_left_fn=_day_left).start() if env else None
    if env:
        threading.Thread(target=say_worker.run, daemon=True).start()
        if tts:
            threading.Thread(target=auto_worker.run, daemon=True).start()
    # 按意思找 (semantic.py): the tool embeds its query with DashScope; the incremental vector sync every 10 min runs
    # on by default, MIND_SEMANTIC_SYNC=0 turns it off ( first backfill: python3 -m mind.semantic)
    from . import semantic
    embed = semantic.embedder(os.environ.get("DASHSCOPE_API_KEY") or hub.get("DASHSCOPE_API_KEY"))
    if embed and semantic.sync_enabled():
        semantic.ensure_indexes(db)
        threading.Thread(target=semantic.periodic, args=(db, embed), kwargs={"paused_fn": pause_ctl.paused},
                         daemon=True).start()
    threading.Thread(target=api.serve, args=(store, token, "127.0.0.1", a.api_port,
                                             {"env": env, "settings_store": settings_store,
                                                                         "recordings_root": a.recordings, "roles_dir": a.roles,
                                                                         "tts": tts, "say_worker": say_worker,
                                                                         "say_auto": say_auto_live, "mode_store": mode_store,
                                                                         "billing": billing, "round_gate": round_gate,
                                                                         "chat": chat, "pause": pause_ctl, "daily": daily,
                                                                         "research": research, "embed": embed}),
                     daemon=True).start()
    cursor = store.state().get("cursor_minute")
    print(f"mind: lane {LANE}, cursor {cursor}, api 127.0.0.1:{a.api_port}, mode {mode_store.current()['mode']} "
          f"seq {mode_store.seq()}, billing {'on' if billing.allowed() else 'off'} (start = 全开)", flush=True)
    while True:                                  # step 1b: hello owns the mode and its settings timer, no tick here
        try:
            good, version = settings_store.current()      # settings are versioned in the database (phase 1)
            selfvoice.refresh_spoken(db, int(time.time() * 1000))   # hello's own lines count as her voice
            store.set_state(settings_version=version)
            n = tick(records, store, good, int(time.time() * 1000), int(a.catch_up_max_hours * 3600_000), env, version,
                     paused_fn=pause_ctl.paused)
            if n:
                print(f"processed {n} minute(s), cursor {store.state().get('cursor_minute')}", flush=True)
            time.sleep(5)
        except Exception as e:                   # noqa: BLE001 - keep running; /state shows the error
            print(f"mind error: {type(e).__name__}: {str(e)[:200]}", flush=True)
            runlog.error(e, "mind.loop")
            time.sleep(30)


if __name__ == "__main__":
    main()
