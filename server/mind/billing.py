"""hello's billing switch and the presets 0 / 9, server half.

Owner 2026-11-14 evening (verbatim): "计费开关是这样，启动默认全开，我就使用网页单点开关，或asr9 asr0来整体开关。其他奇怪的设定都
取消。" So billing is NOT a gate any more. It is only the last whole action:
  on   = preset 9's server half: 听悟 / 识别 / 纪要 / 情景 on (+ 围观 realtime; mind start leaves it as stored), 嘴 -> TTS, 提前写 + 连续说 on when the mirrored mode is talk.
  off  = preset 0's server half: 听悟 / 识别 / 纪要 / 情景 / 围观 realtime off, 连续说 / 提前写 off, stop what hello is saying.
A mind start changes no switch (owner 2026-11-15; hello's power-on sends 9 itself since collector-0.4.96). hello's
power-on 9 changes no switch either (2026-11-17, see power_on): a reboot only restores. Single switches always
work whatever billing shows; speech is held only by the mode (only_talk), never by billing. Turning things off tries
every step and returns the failed step names (logged) instead of raising.

ModeStore.set_billing records the value (seq bumps, so a paid hook still running for an older commit sees is_current()
go false: a later 0 wins over a racing talk entry). Lock contract (mode_store docstring): Billing holds no lock.

ctx (runner._BillingCtx):
  mode_store                 the ModeStore
  set_switch(name, on)       hello.switches tingwu / eye / notes (the /switches page switches)
  switch_on(name) -> bool    their current value
  set_mind(on)               情景 (scene recognition, the /switch master of mind's own loop)
  set_mouth(mode)            嘴: off / clips / tts
  set_family(on)             speech.round.family (家人声音); family_on() -> bool
  say_worker, say_auto       say_prefetch.Worker (提前写) and say_auto.Worker (连续说); either may be None
  cancel()                   stop what hello is saying (speak.cancel_pending(tts))
  mode() -> str              the current (mirrored) mode id
"""
from .mode_table import PRESETS
from .modes import SOURCES, result
from .runlog import hello_log, trace as log_trace

PAID_SWITCHES = ('tingwu', 'eye', 'notes')     # 听悟, 识别, 纪要; 情景 is set_mind
# 围观 realtime voice session (spike 2026-11-14, ear/realtime_link.py; runs only while hello reports crowd).
# 0 turns it off, 9 turns it on. Owner 2026-11-15: mind's own start (preset 9 with source 'boot') leaves it as it was
# (the stored hello.switches value), so a mind restart neither arms nor drops 围观.
REALTIME = 'realtime'
# collector-0.4.96 (owner 2026-11-15): hello does the robot half of its own 0 / 9 (hearing listen, camera, M12
# detection), so a digit from hello answers ok like the page; only other sources still answer senses_on_page.
HELLO_SOURCES = ('asr', 'quick', 'select', 'touch', 'boot')   # boot: the power-on big mode (0.5.21)


def _log(msg):
    print(f'mind billing: {msg}', flush=True)


class Billing:
    def __init__(self, ctx):
        self.ctx = ctx
        self.ms = ctx.mode_store
        self.ms.preset_fn = self.preset
        self.ms.billing_fn = self.switch

    # ---- the switch (the last whole action) ----
    def allowed(self):
        """What the page shows: True after start / 9 / switch on, False after 0 / switch off. Gates nothing."""
        return self.ms.billing()

    def off(self, source, now_ms):
        """Preset 0's server half -> the failed step names ([] when everything went off)."""
        self.ms.set_billing(False, source=source, now_ms=now_ms)
        return self._run_all('billing off', [
            *((n, lambda n=n: self.ctx.set_switch(n, False)) for n in PAID_SWITCHES),
            (REALTIME, lambda: self.ctx.set_switch(REALTIME, False)),
            ('mind', lambda: self.ctx.set_mind(False)),
            ('talk', lambda: self._talk_off(now_ms)),
            ('cancel', self.ctx.cancel)])

    def on(self, source, now_ms):
        """Preset 9's server half (mind's start runs this too) -> the failed step names."""
        return apply_preset(9, self, source=source, now_ms=now_ms).get('failed', [])

    def switch(self, on, *, source, now_ms, meta=None):
        """ModeStore billing_fn: the page switch / a billing input = preset 9 (on) or preset 0 (off)."""
        return apply_preset(9 if on else 0, self, source=source, now_ms=now_ms)

    def preset(self, n, *, source, now_ms, meta=None):
        """ModeStore preset_fn. Owner 2026-11-20 (collector-0.5.21): hello's power-on digit is its stored big mode
        (0 关 / 2 收集 / 9 智能) and is applied like any other (the small switches are temporary); the answer says
        power_on so the run log and the away pause do not take a reboot for the owner."""
        on = n in (0, 2, 9) and self.power_on(source, meta)
        out = apply_preset(n, self, source=source, now_ms=now_ms)
        return {**out, 'power_on': True} if on else out

    def power_on(self, source, meta):
        """Is this digit hello's power-on 9 rather than an owner's 9? (2026-11-17: an unexplained reboot must not undo
        the owner's 闭嘴 / 0 / single switches off.)

        Since collector-0.4.96 hello owes mind a 9 at every power-on and sends it, behind the first boot report, with
        source quick (as the quick page's 全开) until mind answers ok (new input seq on each retry). The wire carries
        no "power-on" flag, so: a quick 9 from a device is the power-on 9 while mind has answered no digit from that
        robot boot with ok yet (hello.mode_log keeps every answer, so this survives a mind restart). After the first
        ok, every quick 9 of that boot is the owner's (the quick page's 全开). A source "boot" digit (a later
        firmware may say so) is always the power-on 9. Edge cases, both rare: a 9 the owner said offline before the
        first link of a boot counts as the power-on 9 (kept); a power-on 9 whose ok answer was lost on the way and
        that hello sends again counts as the owner's (turns everything on, as before 2026-11-17)."""
        meta = meta or {}
        device, boot = meta.get('device'), meta.get('boot')
        if not (isinstance(device, str) and device and isinstance(boot, str) and boot):
            return False
        if source == 'boot':
            return True
        if source != 'quick':
            return False
        try:
            seen = self.ms.db['mode_log'].find_one({'device': device, 'boot': boot, 'input': 'digit', 'code': 'ok'})
        except Exception as e:                 # noqa: BLE001 - unknown: treat it as the owner's 9 (the old behaviour)
            _log(f'power-on check failed ({type(e).__name__}): this 9 runs as an owner\'s 9')
            return False
        return seen is None

    # ---- talk_enter (owner 14) ----
    def talk_enter(self, now_ms, *, is_current):
        """The talk_enter effect: 家人声音 off, then 连续说 on (which turns 提前写 on). is_current is required
        (= lambda: store.seq() == the committed snapshot's seq). Nothing at all outside talk or when is_current() is
        already false; when it goes false while this runs (a later mode change or a 0), both are turned off again.
        -> True when they stay on."""
        live = lambda: self.ctx.mode() == 'talk' and is_current()
        if not live():
            return False
        if self.ctx.family_on():
            self.ctx.set_family(False)        # first, so the first pre-written round is already non-family
        self._talk_on(now_ms)
        if not live():
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

    @staticmethod
    def _run_all(what, steps):
        """Try every (name, fn) step even when one fails; -> the failed names, logged."""
        failed, why = [], []
        for name, step in steps:
            try:
                step()
            except Exception as e:             # noqa: BLE001 - keep going with the rest
                failed.append(name)
                why.append(f'{name}: {type(e).__name__}: {e}')
        if failed:
            _log(f'{what}: {len(failed)} step(s) FAILED, still on / off or unknown: {why}')
        return failed


# ---- presets: the server half -----------------------------------------------------------------------------------
# Each action(b, source, now_ms, out) -> (code, detail): code None = done; detail = a few ASCII words for the toast;
# out['failed'] collects the steps that did not take.

def _billing_off(b, source, now_ms, out):
    out['failed'] += b.off(source, now_ms)
    return None, None


def _billing_on(b, source, now_ms, out):
    b.ms.set_billing(True, source=source, now_ms=now_ms)
    return None, None


def _paid_senses_on(b, source, now_ms, out):
    out['failed'] += b._run_all('senses on', [*((n, lambda n=n: b.ctx.set_switch(n, True)) for n in PAID_SWITCHES),
                                              (REALTIME, lambda: b.ctx.set_switch(REALTIME, True)),   # power-on too (0.5.21)
                                              ('mind', lambda: b.ctx.set_mind(True))])
    return None, None


def _mouth_tts(b, source, now_ms, out):
    b.ctx.set_mouth('tts')
    return None, None


def _mouth_off(b, source, now_ms, out):
    """关 / 收集: she listens but does not speak (owner 2026-11-20)."""
    out['failed'] += b._run_all('mouth off', [('mouth', lambda: b.ctx.set_mouth('off'))])
    return None, None


def _talk_on(b, source, now_ms, out):
    if b.ctx.mode() != 'talk':
        return None, 'TALK SKIPPED'           # no mode change; entering talk turns them on anyway
    b._talk_on(now_ms)
    if b.ctx.mode() != 'talk':                # left talk meanwhile: the mode wins
        b._talk_off(now_ms)
        return 'only_talk', None
    return None, None


def _realtime_toggle(b, source, now_ms, out):
    """Preset 1: 围观闭嘴 / 开口 = the realtime switch flips (off closes the session at once, ear/realtime_link.py)."""
    on = not b.ctx.switch_on(REALTIME)
    failed = b._run_all('realtime toggle', [(REALTIME, lambda: b.ctx.set_switch(REALTIME, on))])
    out['failed'] += failed
    return None, ('DOCK ON' if on else 'DOCK QUIET') if not failed else None    # Docking (owner 2026-11-18, was MEET)


ACTIONS = {'realtime_toggle': _realtime_toggle, 'billing_off': _billing_off, 'billing_on': _billing_on, 'paid_senses_on': _paid_senses_on,
           'mouth_tts': _mouth_tts, 'mouth_off': _mouth_off, 'talk_on': _talk_on}


def apply_preset(n, billing, *, source, now_ms):
    """_apply_preset with one run-log record when it ends: preset.apply {n, code, toast, failed, robot}, by = source;
    the switch.set records it causes share its trace."""
    with hello_log.scope(trace=log_trace()):
        try:
            out = _apply_preset(n, billing, source=source, now_ms=now_ms)
        except Exception as e:
            hello_log.emit('preset.apply', by=source, ok=False, err=f'{type(e).__name__}: {str(e)[:160]}',
                           sum=f'预设 {n}: error', extra={'n': n, 'failed': []})
            raise
        res, failed = out.get('result') or {}, list(out.get('failed') or [])
        hello_log.emit('preset.apply', by=source, ok=not failed, err=f"failed: {', '.join(failed)}" if failed else None,
                       sum=f"预设 {n}: {res.get('code')}" + (f" ({len(failed)} 步失败)" if failed else ''),
                       extra={'n': n, 'code': res.get('code'), 'toast': res.get('toast'), 'failed': failed,
                              'robot': out.get('robot')})
        return out


def _apply_preset(n, billing, *, source, now_ms):
    """Run preset n's server half -> {result: {code, toast}, robot: {hearing?, vision?}} (+ failed: [step names]
    when some step did not take; the toast then says 'PRESET N: K FAILED').

    robot is the preset's robot half, always returned; the page and hello (HELLO_SOURCES, its own digit word, since
    collector-0.4.96) carry it out, any other source answers senses_on_page after doing the server half. 1-8 are
    undefined_preset here (4-8 are mode selects hello does itself, 1-3 empty). Toasts: ok = 'PRESET N' (+ ': detail'),
    else the code's own toast. Stops at the first refused step.
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
    if robot and source not in ('page', *HELLO_SOURCES):
        return done(result('senses_on_page', f'PRESET {n}: {details[0]}, USE PAGE' if out['failed'] else None))
    toast = f'PRESET {n}' + (': ' + ', '.join(details) if details else '')
    return done(result('ok', toast[:32]))
