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
from .mode_table import MODES, DIRECTIONS, SUBTITLE_DIRS, REMOVED_MODES

INPUTS = ('forward', 'backward', 'play', 'pause', 'open', 'close', 'start', 'stop', 'next', 'previous',
          'digit', 'tap', 'double_tap', 'long', 'direction', 'select', 'billing', 'boot', 'timeout')
SOURCES = ('asr', 'touch', 'select', 'quick', 'dualkey', 'page', 'boot', 'timeout')
# talk_enter (owner 14): every entry into talk = 提前写 on + 连续说 on + 家人声音 off; boot no longer is one (it lands
# in crowd, owner 2026-11-18). Billing gates nothing since the owner's 2026-11-14 evening ruling ('billing_off'
# stays in CODES only as a known word, never produced).
# talk_leave: both off, stock dropped.
EFFECTS = ('stop_speech', 'talk_leave', 'talk_enter', 'say_round', 'prefetch_on', 'prefetch_off')

# result code -> robot toast (ASCII, <= 32 chars; '{mode}' = the corner label of the mode named).
# senses_on_page (gap c, 【我定】): a robot-sent preset 1 does the server half of preset 0 (master switch off) and
# cannot do the robot half (local hearing, vision upload), so its code is senses_on_page, not ok; same for 9.
CODES = {
    'ok': 'OK', 'already': 'ALREADY', 'not_in_mode': 'NOT IN {mode}', 'only_talk': 'ONLY IN TALK',
    'billing_off': 'BILLING OFF', 'busy': 'BUSY', 'too_soon': 'TOO SOON', 'mouth_not_tts': 'MOUTH NOT TTS',
    'not_implemented': 'NOT YET', 'undefined_preset': 'PRESET UNDEFINED', 'stale': '', 'unknown_input': 'NO EFFECT',
    'no_action': 'NO ACTION', 'senses_on_page': 'SENSES: USE PAGE',
    'removed_mode': 'DOCKING ONLY', 'asr_off': 'ASR OFF',      # Providence, Docking only (owner 2026-11-20)
}
_EFFECT_TOAST = {'say_round': 'SAYING', 'prefetch_on': 'PREFETCH ON', 'prefetch_off': 'PREFETCH OFF',
                 'stop_speech': 'STOP', 'talk_enter': 'TALK'}

# The old voice word pairs (forward / backward talk, play / pause chat, open / close settings, start / stop event):
# their modes are gone (owner 2026-11-20), so they answer no_action like the voice module's other words.
VOICE_WORDS = ('forward', 'backward', 'play', 'pause', 'open', 'close', 'start', 'stop')
_NOT_HERE = ('digit', 'billing')               # presets / master switch: ModeStore routes them (A2, A3)


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
        if new['mode'] == 'talk':
            out.append('talk_enter')              # entering talk (owner 14): 提前写 / 连续说 on, 家人声音 off
    out += [e for e in effects if e not in out]
    if toast is None and code == 'ok':
        if (new['mode'], new['dir']) != (old['mode'], old['dir']):
            toast = label(new)
        elif out:
            toast = _EFFECT_TOAST.get(out[-1], 'OK')
    return new, result(code, toast), out


def _same(st, code, **kw):
    return dict(st), result(code, **kw), []


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

    # 1. the same in every mode: a robot reboot and the DualKey long press both land in Docking and stop speech
    if input in ('boot', 'long'):
        return _done(st, {'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None}, ['stop_speech'])

    # 2. what Docking gives the input: tap = listen (stop while speaking), double tap = quiet
    if input in m['inputs']:
        meaning = m['inputs'][input]
        if meaning == 'listen':                                 # crowd tap (owner 2026-11-19): stop, else listen
            return (dict(st), result('ok', 'STOP'), ['stop_speech']) if speaking else (dict(st), result('ok', 'LISTEN'), [])
        if meaning == 'quiet':                                  # crowd double tap: 安静一下 (stops a reply too)
            return dict(st), result('ok', 'QUIET'), ['stop_speech'] if speaking else []
        return _same(st, meaning)
    if input in ('next', 'previous'):
        return _same(st, 'only_talk')

    # 3. select {mode}: Docking is already on; a removed mode is refused by name
    if input == 'select':
        target = value.get('mode') if isinstance(value, dict) else None
        if target in REMOVED_MODES:
            return _same(st, 'removed_mode')
        if target not in MODES:
            return _same(st, 'unknown_input')
        return _same(st, 'already')

    # 4. DualKey stick: left was Docking's own direction (already there); the others belonged to removed modes
    if input == 'direction':
        if value in DIRECTIONS and value in (m['dirs'] or {}):
            return _same(st, 'already')
        return _same(st, 'unknown_input')

    # 5. the old voice words
    if input in VOICE_WORDS:
        return _same(st, 'no_action')

    return _same(st, 'unknown_input')


def snapshot(state, *, seq, since, by, billing):
    """The mode snapshot mind hands to ear (for the robot) and the page; look and touch come from the table."""
    st = _state(state)
    m = MODES[st['mode']]
    return {'seq': seq, 'mode': st['mode'], 'dir': st['dir'], 'host': st['host'], 'return_to': st['return_to'],
            'billing': bool(billing), 'since': since, 'by': by,
            'look': dict(m['look'], label=label(st)), 'touch': dict(m['touch'])}
