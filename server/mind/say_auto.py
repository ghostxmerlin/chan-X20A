"""连续说 (owner 2026-11-14): "以一定的合适频率（2-3分钟）一次，如果提前写有高得分选项，则自动播放".

Off by default and after every restart, like 提前写. Turning it on turns 提前写 on (the lines come from
its stock); turning 提前写 off turns it off. While on (and the mouth is on TTS and speech.pipeline is
"round") it wakes every 120–180 s, counted from the end of the last wake, and hands over to
speak.auto_play, which plays only existing stock (one check call at most, never a new write).
"""
import random
import time

WAKE_S = (120, 180)


class Worker:
    def __init__(self, store, *, prefetch, settings_fn, mouth_fn, play_fn, clock=time.time, sleep=time.sleep, rng=None):
        self.store, self.prefetch, self.settings_fn, self.mouth_fn = store, prefetch, settings_fn, mouth_fn
        self.play_fn, self.clock, self.sleep, self.rng = play_fn, clock, sleep, rng or random.Random()
        self._on = False
        self.next_at = None
        self.last = None                   # {reason, at, n, stopped} of the latest wake, shown on the page
        prefetch.on_change.append(self._prefetch_changed)
        self.store.set_state(say_auto={'on': False, 'since': None})          # a restart always starts off

    # ---- switch ----
    def enabled(self):
        return self._on

    def set_enabled(self, on, now_ms):
        self._on = bool(on)
        if self._on and not self.prefetch.enabled():
            self.prefetch.set_enabled(True, now_ms)
        self.next_at = now_ms + int(self.rng.uniform(*WAKE_S) * 1000) if self._on else None
        self.store.set_state(say_auto={'on': self._on, 'since': now_ms})

    def _prefetch_changed(self, on, now_ms):
        if not on and self._on:
            self.set_enabled(False, now_ms)

    def status(self):
        return {'on': self._on, 'next_at': self.next_at, 'last': self.last}

    # ---- one wake ----
    def tick(self, now_ms):
        """Wake when due; returns the wake's reason, or None when not due (or off)."""
        if not self._on or self.next_at is None or now_ms < self.next_at:
            return None
        try:
            return self.step(now_ms)
        finally:
            if self._on:
                self.next_at = int(self.clock() * 1000) + int(self.rng.uniform(*WAKE_S) * 1000)

    def step(self, now_ms):
        try:
            res = self._step(now_ms)
        except Exception as e:                         # noqa: BLE001 - shown on the page, logged by run
            self.last = {'reason': 'error', 'at': now_ms, 'n': 0, 'stopped': False, 'error': type(e).__name__}
            raise
        self.last = {'reason': res['reason'], 'at': now_ms, 'n': len(res.get('played') or []), 'stopped': bool(res.get('stopped'))}
        return res['reason']

    def _step(self, now_ms):
        s = self.settings_fn()
        if (s.get('speech') or {}).get('pipeline') != 'round' or self.mouth_fn() != 'tts':
            return {'reason': 'off', 'played': []}
        return self.play_fn(s, now_ms, lambda: self._on and self.mouth_fn() == 'tts')

    def run(self, log=print):
        while True:
            try:
                self.tick(int(self.clock() * 1000))
            except Exception as e:                    # noqa: BLE001 - keep the thread alive; log the reason
                log(f'say auto: {type(e).__name__}: {str(e)[:160]}', flush=True)
            self.sleep(1)
