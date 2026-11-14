"""hello's modes: the pure state machine (spec docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md §3.4-§3.8, §5).

No database, no clock, no I/O: ModeStore (task A2) owns seq / since / persistence / dedup / staleness / the
settings timeout clock, and runs the returned effects in order. `digit` (presets, A3) and `billing` (the master
switch, A2/A3) are routed by ModeStore, never here: passing them raises ValueError so a misroute is loud instead
of looking like junk input (genuinely unknown inputs still answer unknown_input).

State = {mode, dir, host, return_to}:
  mode       one of mode_table.MODES
  dir        subtitle direction (zh_en / en_zh). Also kept while in settings when return_to == 'subtitle', so close
             returns to the same direction (【我定】: return_to stays a plain mode id, as the snapshot shows it).
  host       'event' while event-driven hosts a guest mode (never set in step 1: nothing enters a guest yet)
  return_to  the mode settings goes back to; None outside settings
"""
from .mode_table import MODES, DIRECTIONS, SUBTITLE_DIRS

INPUTS = ('forward', 'backward', 'play', 'pause', 'open', 'close', 'start', 'stop', 'next', 'previous',
          'digit', 'tap', 'double_tap', 'long', 'direction', 'select', 'billing', 'boot', 'timeout')
SOURCES = ('asr', 'touch', 'select', 'quick', 'dualkey', 'page', 'boot', 'timeout')
EFFECTS = ('stop_speech', 'talk_leave', 'say_round', 'prefetch_on', 'prefetch_off')

# result code -> robot toast (ASCII, <= 32 chars; '{mode}' = the corner label of the mode named).
# senses_on_page (gap c, 【我定】): a robot-sent preset 1 does the server half of preset 0 (master switch off) and
# cannot do the robot half (local hearing, vision upload), so its code is senses_on_page, not ok; same for 9.
CODES = {
    'ok': 'OK', 'already': 'ALREADY', 'not_in_mode': 'NOT IN {mode}', 'only_talk': 'ONLY IN TALK',
    'billing_off': 'BILLING OFF', 'busy': 'BUSY', 'too_soon': 'TOO SOON', 'mouth_not_tts': 'MOUTH NOT TTS',
    'not_implemented': 'NOT YET', 'undefined_preset': 'PRESET UNDEFINED', 'stale': '', 'unknown_input': 'NO EFFECT',
    'no_action': 'NO ACTION', 'senses_on_page': 'SENSES: USE PAGE',
}
_EFFECT_TOAST = {'say_round': 'SAYING', 'prefetch_on': 'PREFETCH ON', 'prefetch_off': 'PREFETCH OFF',
                 'stop_speech': 'STOP'}

_WORDS = {w: (mid, 'enter') for mid, m in MODES.items() for w in m['enter']}
_WORDS.update({w: (mid, 'leave') for mid, m in MODES.items() for w in m['leave']})
_DIR_OWNER = {d: mid for mid, m in MODES.items() for d in (m['dirs'] or {})}
_NOT_HERE = ('digit', 'billing')               # presets / master switch: ModeStore routes them (A2, A3)
_DEFAULT_DIR = SUBTITLE_DIRS[0]                 # select subtitle without a dir = 中→英 (【我定】, same as DualKey up)


def result(code, toast=None, *, mode=None):
    """{code, toast}; the toast defaults to the code's own, '{mode}' filled with that mode's corner label."""
    if code not in CODES:
        raise ValueError(f'unknown result code {code!r}')
    t = CODES[code] if toast is None else toast
    return {'code': code, 'toast': t.replace('{mode}', MODES[mode]['look']['label'] if mode else '').strip()}


def label(state):
    m = MODES[state['mode']]
    if state['mode'] == 'subtitle':
        return m['labels'].get(state.get('dir'), m['look']['label'])
    return m['look']['label']


def _state(state):
    try:
        st = {k: state[k] for k in ('mode', 'dir', 'host', 'return_to')}
    except (KeyError, TypeError):
        raise ValueError(f'bad mode state {state!r}') from None
    if st['mode'] not in MODES or st['host'] not in (None, 'event') or \
            st['return_to'] not in (None, *MODES) or st['dir'] not in (None, *SUBTITLE_DIRS):
        raise ValueError(f'bad mode state {state!r}')
    return st


def _done(old, new, effects=(), code='ok', toast=None):
    """Finish a transition: tidy the state, put the mode-change effects first, pick the toast."""
    if new['mode'] != 'settings':
        new['return_to'] = None
    if new['mode'] != 'subtitle' and not (new['mode'] == 'settings' and new['return_to'] == 'subtitle'):
        new['dir'] = None
    out = []
    if new['mode'] != old['mode']:
        out.append('stop_speech')                 # any mode change stops what hello is saying
        if old['mode'] == 'talk':
            out.append('talk_leave')              # leaving talk: 提前写 / 连续说 off, prefetched stock dropped
    out += [e for e in effects if e not in out]
    if toast is None and code == 'ok':
        if (new['mode'], new['dir']) != (old['mode'], old['dir']):
            toast = label(new)
        elif out:
            toast = _EFFECT_TOAST.get(out[-1], 'OK')
    return new, result(code, toast), out


def _same(st, code, **kw):
    return dict(st), result(code, **kw), []


def _enter(st, target, dir=None):
    """A manual pick of `target`: straight there, never via IDLE. Event-driven stops (host cleared, owner 12)."""
    new = {'mode': target, 'dir': dir if target == 'subtitle' else None, 'host': None, 'return_to': None}
    if target == 'settings':
        # Remember what was on screen: event-driven waiting shows the IDLE screen, so it comes back to idle; a
        # hosted guest comes back unhosted (close must not switch event-driven back on, spec §3.8).
        new['return_to'] = 'idle' if st['mode'] == 'event' else st['mode']
        new['dir'] = st['dir'] if st['mode'] == 'subtitle' else None
    return _done(st, new)


def _leave(st):
    """Leave the current mode: settings -> return_to (or idle); a hosted guest -> event waiting; else -> idle."""
    if st['mode'] == 'settings':
        target = st['return_to'] or 'idle'
        new = {'mode': target, 'dir': st['dir'] if target == 'subtitle' else None, 'host': None, 'return_to': None}
    elif st['host'] == 'event':
        new = {'mode': 'event', 'dir': None, 'host': None, 'return_to': None}
    else:
        new = {'mode': 'idle', 'dir': None, 'host': None, 'return_to': None}
    if new['mode'] == st['mode'] and new['dir'] == st['dir']:
        return _same(st, 'already')               # leaving idle: nowhere to go
    return _done(st, new)


def _leave_host(st):
    """stop / picking event again while it hosts a guest: event-driven and its guest stop together (spec §3.8)."""
    return _done(st, {'mode': 'idle', 'dir': None, 'host': None, 'return_to': None})


def _switch_dir(st, d):
    new = dict(st, dir=d)
    return _done(st, new)


def transition(state, input, value=None, *, source, speaking=False):
    """(state, input, value) -> (new_state, {code, toast}, effects). Pure: never touches the database or the clock.

    `speaking` (gap a, 【我定】): the caller passes True while stop_speech would have something to cancel, i.e. from
    the moment a line or a 说一轮 round is accepted (writing has started, before any audio) until its playback on
    /mouth has finished or been cancelled. Writing counts: a tap then means "stop" (and cancels the round), not
    "another round", which would only come back busy anyway.

    The robot decides from the snapshot's `touch` whether a screen tap is sent at all (gap d): with touch = none or
    badge it never reaches mind, so a tap / double_tap that does arrive for those modes is a DualKey click and
    answers no_action. Swipes are the robot's own business (they open local pages in every mode); `look.eye`
    starting with page_ tells it the home screen is a placeholder page.
    """
    st = _state(state)
    if source not in SOURCES:
        raise ValueError(f'unknown source {source!r}')
    if input in _NOT_HERE:
        raise ValueError(f'{input!r}: digit/billing are routed by ModeStore, not transition')
    mode, m = st['mode'], MODES[st['mode']]

    # 1. the same in every mode
    if input == 'boot':
        # robot reboot (new boot id): talk, even when already there; 提前写 / 连续说 off (spec §3.6, §3.8)
        return _done(st, {'mode': 'talk', 'dir': None, 'host': None, 'return_to': None}, ['stop_speech', 'talk_leave'])
    if input == 'long':
        # DualKey long press: stop the current thing, back to talk; a manual pick, so event-driven stops too
        return _done(st, {'mode': 'talk', 'dir': None, 'host': None, 'return_to': None}, ['stop_speech'])

    # 2. what this mode gives the input (tap, double_tap, next, previous, and forward inside talk)
    if input in m['inputs']:
        meaning = m['inputs'][input]
        if input == 'tap' and speaking and m['touch']['tap'] == 'server':
            return _done(st, dict(st), ['stop_speech'])        # hello is speaking: a tap stops it (owner 9)
        if isinstance(meaning, str):
            return _same(st, meaning)
        return _done(st, dict(st), list(meaning))
    if input in ('next', 'previous'):
        return _same(st, 'only_talk')

    # 3. select {mode, dir?}: the swipe-up picker and the page; picking the current mode leaves it
    if input == 'select':
        target = value.get('mode') if isinstance(value, dict) else None
        d = value.get('dir') if isinstance(value, dict) else None
        if target not in MODES or (target == 'subtitle' and d not in (None, *SUBTITLE_DIRS)):
            return _same(st, 'unknown_input')
        if st['host'] == target:
            return _leave_host(st)
        if target == mode:
            if target == 'subtitle' and d is not None and d != st['dir']:
                return _switch_dir(st, d)
            return _leave(st)
        return _enter(st, target, d or _DEFAULT_DIR)

    # 4. DualKey stick: up / down = subtitle 中→英 / 英→中, left = crowd, right = companion; same push again leaves
    if input == 'direction':
        owner = _DIR_OWNER.get(value)
        if value not in DIRECTIONS or owner is None:
            return _same(st, 'unknown_input')
        d = MODES[owner]['dirs'][value]
        if owner == mode:
            if d is None or d == st['dir']:
                return _leave(st)
            return _switch_dir(st, d)              # in subtitle, the other up / down = change direction, not via IDLE
        return _enter(st, owner, d)

    # 5. the voice word pairs (and timeout, settings' second leave input)
    if input in _WORDS:
        owner, kind = _WORDS[input]
        if kind == 'enter':
            if owner == mode or owner == st['host']:
                return _same(st, 'already')
            return _enter(st, owner)
        if owner == mode:
            return _leave(st)
        if owner == st['host']:
            return _leave_host(st)
        return _same(st, 'not_in_mode', mode=owner)

    return _same(st, 'unknown_input')


def snapshot(state, *, seq, since, by, billing):
    """The mode snapshot mind hands to ear (for the robot) and the page; look and touch come from the table."""
    st = _state(state)
    m = MODES[st['mode']]
    return {'seq': seq, 'mode': st['mode'], 'dir': st['dir'], 'host': st['host'], 'return_to': st['return_to'],
            'billing': bool(billing), 'since': since, 'by': by,
            'look': dict(m['look'], label=label(st)), 'touch': dict(m['touch'])}
