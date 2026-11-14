"""Pre-write talk-show rounds while people are talking (spec section 6A, user 2026-11-13).

Not always on: the owner turns it on when a discussion gets heated ("next") and off ("previous"); for
now the page has the switch. While on (and the mouth is on TTS and speech.pipeline is "round"), a
round is written when a stretch of clear speech has ended (quiet_s), at least spacing_s after the
last one, not inside the same moment, and only while this hour's say_* spending is under the cap.
A restart, turning it off, or a scene/profile switch drops the stock. Outside the talk show (modes, step 1)
it writes nothing even when on (mode_fn; last reason not_talk): leaving talk turns it off anyway, this is the
second line of defence.
"""
import contextlib
import threading
import time

from .say_moment import same_moment, signature

HOUR_MS = 3600_000


def _cfg(s):
    r = (s.get('speech') or {}).get('round') or {}
    return (r.get('prefetch_quiet_s', 5) * 1000, r.get('prefetch_spacing_s', 30) * 1000,
            r.get('prefetch_usd_per_hour', 0.6))


class Worker:
    def __init__(self, store, *, settings_fn, mouth_fn, ctx_fn, write_fn, clock=time.time, mode_fn=lambda: 'talk'):
        self.store, self.settings_fn, self.mouth_fn, self.mode_fn = store, settings_fn, mouth_fn, mode_fn
        self.ctx_fn, self.write_fn, self.clock = ctx_fn, write_fn, clock
        self._lock = threading.Lock()
        self._busy = False
        self._stock = {}
        self._scope_key = None
        self._on = False
        self._attempt = {}                 # scope -> last write attempt (ms), successful or not
        self.last = None                   # {reason, at} of the latest step, shown on the page
        self.on_change = []                # f(on, now_ms) after every switch (连续说 follows it off)
        self.store.set_state(say_prefetch={'on': False, 'since': None})       # a restart always starts off

    # ---- switch ----
    def enabled(self):
        return self._on

    def set_enabled(self, on, now_ms):
        self._on = bool(on)
        if not on:
            self._stock.clear()
        self.store.set_state(say_prefetch={'on': self._on, 'since': now_ms})
        for f in self.on_change:
            f(self._on, now_ms)

    # ---- shared with the click path ----
    def busy(self):
        return self._busy

    @contextlib.contextmanager
    def writing(self):
        """Hold while reading the stock or writing a fresh round, so a click and a pre-write never write
        the same moment twice."""
        with self._lock:
            yield

    def stock(self, scope_id):
        return self._stock.get(scope_id)

    def put(self, scope_id, round_):
        self._stock[scope_id] = round_

    def _check_scope(self, s):
        speech = s.get('speech') or {}
        key = (speech.get('profile'), speech.get('scope_since'))
        if self._scope_key is not None and key != self._scope_key:
            self._stock.clear()
        self._scope_key = key

    def _hour_cost(self, now_ms):
        start = (now_ms // HOUR_MS) * HOUR_MS
        return sum(d.get('cost_usd') or 0 for d in self.store.db['mind_runs'].find({'at': {'$gte': start}}, {'step': 1, 'cost_usd': 1})
                   if str(d.get('step', '')).startswith('say_'))

    # ---- one step ----
    def step(self, now_ms):
        reason = self._step(now_ms)
        self.last = {'reason': reason, 'at': now_ms}
        return reason

    def _step(self, now_ms):
        if not self._on:
            return 'off'
        if self.mode_fn() != 'talk':
            return 'not_talk'
        s = self.settings_fn()
        if (s.get('speech') or {}).get('pipeline') != 'round' or self.mouth_fn() != 'tts':
            return 'off'
        self._check_scope(s)
        quiet_ms, spacing_ms, cap = _cfg(s)
        ctx = self.ctx_fn(s, now_ms)
        scope_id = ctx['scope']['id']
        clear = [e['at'] for e in ctx.get('evidence', []) if e.get('kind') == 'speech' and e.get('quality') == 'usable']
        if not clear:
            return 'no_speech'
        latest = max(clear)
        if now_ms - latest < quiet_ms:
            return 'waiting'
        sig = signature(ctx, now_ms)

        def blocked():
            last = self._stock.get(scope_id)
            if last and latest <= last['written_at']:
                return 'nothing_new'
            # Failed writes count too, so a provider outage or empty rounds do not retry every few seconds.
            if now_ms - max(last['written_at'] if last else 0, self._attempt.get(scope_id, 0)) < spacing_ms:
                return 'spacing'
            if last and same_moment(last.get('signature'), sig):
                return 'same_moment'
            return None
        if reason := blocked():
            return reason
        if self._hour_cost(now_ms) >= cap:
            return 'budget'
        with self._lock:
            # A click may have written this moment while we were deciding: decide again under the lock.
            if reason := blocked():
                return reason
            self._busy = True
            self._attempt[scope_id] = now_ms
            try:
                round_ = self.write_fn(ctx, now_ms)
            finally:
                self._busy = False
            if self._on:
                self._stock[scope_id] = round_
        return 'wrote'

    def run(self, log=print):
        while True:
            try:
                self.step(int(self.clock() * 1000))
            except Exception as e:                    # noqa: BLE001 - keep the thread alive; log the reason
                log(f'say prefetch: {type(e).__name__}: {str(e)[:160]}', flush=True)
                time.sleep(5)
            time.sleep(1)
