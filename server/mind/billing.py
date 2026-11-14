"""hello's billing master switch and the 0-9 presets, server half (spec 2026-11-14-hello-modes-design-v2 §3.3, §7).

Step 1 of the master switch = turn off + refuse re-enable + block speech (no llm.chat gate yet):
  off  ModeStore.set_billing(False) first (seq bumps, so the pre-check refuses paid effects from then on and paid
       hooks already running see is_current() go false), then 听悟 / 识别 / 纪要 / 情景 off, 连续说 / 提前写 off,
       stop what hello is saying.
  on   only ModeStore.set_billing(True): restores nothing ("all on" is preset 9).
  allowed() = ModeStore.billing(); the HTTP layer (task A4) answers 409 计费关着 to paid switches / speech while off.

Lock contract (mode_store docstring): Billing holds no lock at all, so set_billing / apply are never called under one.
preset() / switch() are ModeStore's preset_fn / billing_fn (installed by the constructor); they run outside the
store's lock and never re-enter apply with the device key being handled (nested applies carry no device).

ctx (task A4 builds it in runner.py):
  mode_store                 the ModeStore
  set_switch(name, on)       hello.switches tingwu / eye / notes (the /switches page switches)
  switch_on(name) -> bool    their current value
  set_mind(on)               情景 (scene recognition, the /switch master of mind's own loop)
  set_mouth(mode)            嘴: off / clips / tts
  set_family(on)             speech.round.family (家人声音); family_on() -> bool
  say_worker, say_auto       say_prefetch.Worker (提前写) and say_auto.Worker (连续说); either may be None
  cancel()                   stop what hello is saying (speak.cancel_pending(tts))
  mode() -> str              the current mode id (live: mode_store.current()['mode'])
"""
from .mode_table import PRESETS
from .modes import SOURCES, result

PAID_SWITCHES = ('tingwu', 'eye', 'notes')     # 听悟, 识别, 纪要; 情景 is set_mind


def _log(msg):
    print(f'mind billing: {msg}', flush=True)


class Billing:
    def __init__(self, ctx):
        self.ctx = ctx
        self.ms = ctx.mode_store
        self.ms.preset_fn = self.preset
        self.ms.billing_fn = self.switch

    # ---- the switch ----
    def allowed(self):
        return self.ms.billing()

    def off(self, source, now_ms):
        self.ms.set_billing(False, source=source, now_ms=now_ms)
        self._run_all('billing off', [
            *(lambda n=n: self.ctx.set_switch(n, False) for n in PAID_SWITCHES),
            lambda: self.ctx.set_mind(False),
            lambda: self._talk_off(now_ms),
            self.ctx.cancel])

    def on(self, source, now_ms):
        self.ms.set_billing(True, source=source, now_ms=now_ms)

    def switch(self, on, *, source, now_ms, meta=None):
        """ModeStore billing_fn: returns None, so the store answers ok when the value changed, already when not."""
        (self.on if on else self.off)(source, now_ms)

    def preset(self, n, *, source, now_ms, meta=None):
        """ModeStore preset_fn."""
        return apply_preset(n, self, source=source, now_ms=now_ms)

    # ---- talk_enter (owner 14) ----
    def talk_enter(self, now_ms, is_current=None):
        """The talk_enter effect: 家人声音 off, then 连续说 on (which turns 提前写 on). Nothing at all while the master
        switch is off, outside talk, or when is_current() (= store.seq() == the committed snapshot's seq) is already
        false; when it goes false while this runs, 提前写 / 连续说 are turned off again (a later mode change or billing
        off wins; at worst a racing re-entry ends with them off, which costs nothing). -> True when they stay on."""
        cur = is_current or (lambda: True)
        ok = lambda: self.allowed() and self.ctx.mode() == 'talk' and cur()
        if not ok():
            return False
        if self.ctx.family_on():
            self.ctx.set_family(False)        # first, so the first pre-written round is already non-family
        self._talk_on(now_ms)
        if not ok():
            self._talk_off(now_ms)
            return False
        return True

    # ---- pieces ----
    def _talk_on(self, now_ms):
        auto, worker = self.ctx.say_auto, self.ctx.say_worker
        if auto is not None:
            auto.set_enabled(True, now_ms)     # also turns 提前写 on
        elif worker is not None:
            worker.set_enabled(True, now_ms)

    def _talk_off(self, now_ms):
        if self.ctx.say_auto is not None:
            self.ctx.say_auto.set_enabled(False, now_ms)
        if self.ctx.say_worker is not None:
            self.ctx.say_worker.set_enabled(False, now_ms)     # drops the stock

    def _paid_senses_on(self):
        for n in PAID_SWITCHES:
            self.ctx.set_switch(n, True)
        self.ctx.set_mind(True)
        if not self.allowed():                 # the switch went off meanwhile: off wins
            self._run_all('undo senses on', [*(lambda n=n: self.ctx.set_switch(n, False) for n in PAID_SWITCHES),
                                             lambda: self.ctx.set_mind(False)])
            return 'billing_off'
        return None

    @staticmethod
    def _run_all(what, steps):
        """Turning things off: try every step even when one fails, then raise naming the failures."""
        failed = []
        for step in steps:
            try:
                step()
            except Exception as e:             # noqa: BLE001 - keep turning the rest off
                failed.append(f'{type(e).__name__}: {e}')
        if failed:
            _log(f'{what}: {len(failed)} step(s) failed: {failed}')
            raise RuntimeError(f'{what}: {"; ".join(failed)}')


# ---- presets: the server half -----------------------------------------------------------------------------------
# Each action -> (code, detail): code None = done; detail = a few ASCII words for the toast.

def _billing_off(b, source, now_ms):
    b.off(source, now_ms)
    return None, None


def _billing_on(b, source, now_ms):
    b.on(source, now_ms)
    return None, None


def _paid_senses_on(b, source, now_ms):
    if not b.allowed():
        return 'billing_off', None
    return b._paid_senses_on(), None


def _mouth_tts(b, source, now_ms):
    b.ctx.set_mouth('tts')
    return None, None


def _talk_on(b, source, now_ms):
    if b.ctx.mode() != 'talk':
        return None, 'TALK SKIPPED'           # spec §12.2: no mode change; entering talk turns them on anyway
    if not b.allowed():
        return 'billing_off', None
    b._talk_on(now_ms)
    if not (b.allowed() and b.ctx.mode() == 'talk'):
        b._talk_off(now_ms)
        return 'billing_off' if not b.allowed() else 'only_talk', None
    return None, None


def _toggle_tingwu(b, source, now_ms):
    on = not b.ctx.switch_on('tingwu')
    if on and not b.allowed():
        return 'billing_off', None             # turning it off never needs the switch
    b.ctx.set_switch('tingwu', on)
    if on and not b.allowed():
        b.ctx.set_switch('tingwu', False)
        return 'billing_off', None
    return None, 'TINGWU ON' if on else 'TINGWU OFF'


def _toggle_prefetch(b, source, now_ms):
    worker = b.ctx.say_worker
    if b.ctx.mode() != 'talk':
        return 'only_talk', None
    if worker is None:
        return 'not_implemented', None
    on = not worker.enabled()
    if on and not b.allowed():
        return 'billing_off', None
    worker.set_enabled(on, now_ms)             # off also turns 连续说 off (say_auto follows it)
    if on and not (b.allowed() and b.ctx.mode() == 'talk'):
        worker.set_enabled(False, now_ms)
        return 'billing_off' if not b.allowed() else 'only_talk', None
    return None, 'PREFETCH ON' if on else 'PREFETCH OFF'


def _mode_idle(b, source, now_ms):
    r = b.ms.apply('select', {'mode': 'idle'}, source=source, now_ms=now_ms)['result']
    return (None, r['toast']) if r['code'] == 'ok' else (r['code'], None)


ACTIONS = {'billing_off': _billing_off, 'billing_on': _billing_on, 'paid_senses_on': _paid_senses_on,
           'mouth_tts': _mouth_tts, 'talk_on': _talk_on, 'toggle_tingwu': _toggle_tingwu,
           'toggle_prefetch': _toggle_prefetch, 'mode_idle': _mode_idle}


def apply_preset(n, billing, *, source, now_ms):
    """Run preset n's server half -> {result: {code, toast}, robot: {hearing?, vision?}}.

    robot is the preset's robot half, always returned; step 1 only the page carries it out, so a preset with a robot
    half sent from anywhere but the page (robot digit word, quick settings) answers senses_on_page after doing the
    server half. Toasts: ok = 'PRESET N' (+ ': detail'), else the code's own toast. Stops at the first refused step.
    """
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 9:
        raise ValueError(f'preset must be 0-9, not {n!r}')
    if source not in SOURCES:
        raise ValueError(f'unknown source {source!r}')
    p = PRESETS[n]
    robot = dict(p['robot'])
    if not p['server']:
        return {'result': result('undefined_preset'), 'robot': robot}
    details = []
    for action in p['server']:
        code, detail = ACTIONS[action](billing, source, now_ms)
        if code:
            return {'result': result(code), 'robot': robot}
        if detail:
            details.append(detail)
    if robot and source != 'page':
        return {'result': result('senses_on_page'), 'robot': robot}
    toast = f'PRESET {n}' + (': ' + ', '.join(details) if details else '')
    return {'result': result('ok', toast[:32]), 'robot': robot}
