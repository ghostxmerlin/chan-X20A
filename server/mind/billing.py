"""hello's billing master switch and the 0-9 presets, server half (spec 2026-11-14-hello-modes-design-v2 §3.3, §7).

Step 1 of the master switch = turn off + refuse re-enable + block speech (no llm.chat gate yet):
  off  ModeStore.set_billing(False) first (seq bumps, so the pre-check refuses paid effects from then on and paid
       hooks already running see is_current() go false), then 听悟 / 识别 / 纪要 / 情景 off, 连续说 / 提前写 off,
       stop what hello is saying. Every step is tried even when one fails; off() returns the names of the failed
       steps (logged loudly) instead of raising, so the input is still logged / deduplicated and the caller hears
       the truth: billing IS off, these steps did not take (review I3). A second off is a real retry.
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
        """-> the failed step names ([] when everything went off): tingwu / eye / notes / mind / talk / cancel."""
        self.ms.set_billing(False, source=source, now_ms=now_ms)
        return self._run_all('billing off', [
            *((n, lambda n=n: self.ctx.set_switch(n, False)) for n in PAID_SWITCHES),
            ('mind', lambda: self.ctx.set_mind(False)),
            ('talk', lambda: self._talk_off(now_ms)),
            ('cancel', self.ctx.cancel)])

    def on(self, source, now_ms):
        self.ms.set_billing(True, source=source, now_ms=now_ms)

    def switch(self, on, *, source, now_ms, meta=None):
        """ModeStore billing_fn. None (the store answers ok when the value changed, already when not), or after a
        partly failed off {result: ok/already with toast 'BILLING OFF, N FAILED', failed: [step names]}."""
        was = self.allowed()
        if on:
            self.on(source, now_ms)
            return None
        failed = self.off(source, now_ms)
        if not failed:
            return None
        return {'result': result('ok' if was else 'already', f'BILLING OFF, {len(failed)} FAILED'), 'failed': failed}

    def preset(self, n, *, source, now_ms, meta=None):
        """ModeStore preset_fn."""
        return apply_preset(n, self, source=source, now_ms=now_ms)

    # ---- talk_enter (owner 14) ----
    def talk_enter(self, now_ms, *, is_current):
        """The talk_enter effect: 家人声音 off, then 连续说 on (which turns 提前写 on). is_current is required
        (= lambda: store.seq() == the committed snapshot's seq). Nothing at all outside talk or when is_current() is
        already false. With the master switch off only the free part runs (家人声音 off; owner 14 exempts only the two
        paid behaviours). When is_current() goes false while this runs, 提前写 / 连续说 are turned off again (a later
        mode change or billing off wins; at worst a racing re-entry ends with them off, which costs nothing).
        -> True when they stay on."""
        cur = is_current
        ok = lambda: self.allowed() and self.ctx.mode() == 'talk' and cur()
        if not (self.ctx.mode() == 'talk' and cur()):
            return False
        if self.ctx.family_on():
            self.ctx.set_family(False)        # first, so the first pre-written round is already non-family
        if not self.allowed():
            return False
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
        if not self.allowed():                 # the switch went off meanwhile: off wins (its own off is running too)
            self._run_all('undo senses on', [*((n, lambda n=n: self.ctx.set_switch(n, False)) for n in PAID_SWITCHES),
                                             ('mind', lambda: self.ctx.set_mind(False))])
            return 'billing_off'
        return None

    @staticmethod
    def _run_all(what, steps):
        """Turning things off: try every (name, fn) step even when one fails; -> the failed names, logged."""
        failed, why = [], []
        for name, step in steps:
            try:
                step()
            except Exception as e:             # noqa: BLE001 - keep turning the rest off
                failed.append(name)
                why.append(f'{name}: {type(e).__name__}: {e}')
        if failed:
            _log(f'{what}: {len(failed)} step(s) FAILED, still on or unknown: {why}')
        return failed


# ---- presets: the server half -----------------------------------------------------------------------------------
# Each action(b, source, now_ms, out) -> (code, detail): code None = done; detail = a few ASCII words for the toast;
# out['failed'] collects the steps of a partly failed billing off.

def _billing_off(b, source, now_ms, out):
    out['failed'] += b.off(source, now_ms)
    return None, None


def _billing_on(b, source, now_ms, out):
    b.on(source, now_ms)
    return None, None


def _paid_senses_on(b, source, now_ms, out):
    if not b.allowed():
        return 'billing_off', None
    return b._paid_senses_on(), None


def _mouth_tts(b, source, now_ms, out):
    b.ctx.set_mouth('tts')
    return None, None


def _talk_on(b, source, now_ms, out):
    if b.ctx.mode() != 'talk':
        return None, 'TALK SKIPPED'           # spec §12.2: no mode change; entering talk turns them on anyway
    if not b.allowed():
        return 'billing_off', None
    b._talk_on(now_ms)
    if not (b.allowed() and b.ctx.mode() == 'talk'):
        b._talk_off(now_ms)
        return 'billing_off' if not b.allowed() else 'only_talk', None
    return None, None


ACTIONS = {'billing_off': _billing_off, 'billing_on': _billing_on, 'paid_senses_on': _paid_senses_on,
           'mouth_tts': _mouth_tts, 'talk_on': _talk_on}


def apply_preset(n, billing, *, source, now_ms):
    """Run preset n's server half -> {result: {code, toast}, robot: {hearing?, vision?}} (+ failed: [step names]
    when a billing off inside it partly failed; the toast then says 'PRESET N: K FAILED').

    robot is the preset's robot half, always returned; only the page carries it out, so a preset with a robot half
    sent from anywhere but the page (hello's digit word) answers senses_on_page after doing the server half. 1-8 are
    undefined_preset here (4-8 are mode selects hello does itself, 1-3 empty). Toasts: ok = 'PRESET N' (+ ': detail'), else the code's own toast. Stops at the first refused step.
    """
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 9:
        raise ValueError(f'preset must be 0-9, not {n!r}')
    if source not in SOURCES:
        raise ValueError(f'unknown source {source!r}')
    p = PRESETS[n]
    robot = dict(p['robot'])
    if not p['server']:
        return {'result': result('undefined_preset'), 'robot': robot}
    details, out = [], {'failed': []}

    def done(res):
        return {'result': res, 'robot': robot, **({'failed': out['failed']} if out['failed'] else {})}

    for action in p['server']:
        code, detail = ACTIONS[action](billing, source, now_ms, out)
        if code:
            return done(result(code))
        if detail:
            details.append(detail)
    if out['failed']:
        details.insert(0, f"{len(out['failed'])} FAILED")
    if robot and source != 'page':
        return done(result('senses_on_page', f'PRESET {n}: {details[0]}, USE PAGE' if out['failed'] else None))
    toast = f'PRESET {n}' + (': ' + ', '.join(details) if details else '')
    return done(result('ok', toast[:32]))
