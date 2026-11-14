"""hello's modes: the one definition (spec docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md §3.1, §3.2).

A Python module (not JSON) because server/mind/deploy.sh only ships server/mind/*.py. The robot does not know
which mode wears which eyes: it only reads the `look` / `touch` words from the snapshot (modes.snapshot).
PRESETS (0-9) are added here by task A3.

Per mode:
  name     Chinese name for people
  kind     normal / temporary (settings: remembers return_to) / host (event-driven: outer layer) / idle
  enter    voice words that enter it; leave: voice words (and timeout) that leave it
  dirs     DualKey stick direction -> subtitle direction (None for modes without directions)
  labels   per-direction corner label (subtitle only)
  look     eye (EYES), pool (POOLS), label (ASCII <= 10 chars), color (RRGGBB)
  touch    home-screen single / double tap: server = send to mind, badge = robot flips its badge page locally,
           none = robot shows a local hint only, nothing sent
  inputs   what tap / double_tap / next / previous / forward mean inside this mode: a tuple of effect names, or
           a result code. A server-side tap while hello is speaking always stops instead (modes.transition).
  paid     what this mode MAY spend money on (step 1: only talk; nothing paid starts on entering any mode)
"""

EYES = ('laughing_man', 'v05', 'v14', 'v18', 'page_subtitle', 'page_settings', 'page_idle')
POOLS = (None, 'lively')
TOUCH = ('server', 'badge', 'none')
DIRECTIONS = ('up', 'down', 'left', 'right')
SUBTITLE_DIRS = ('zh_en', 'en_zh')

_SHELL = {'tap': 'not_implemented', 'double_tap': 'not_implemented'}
_NO_TAP = {'tap': 'no_action', 'double_tap': 'no_action'}

MODES = {
    'talk': {
        'name': '脱口秀', 'kind': 'normal', 'enter': ('forward',), 'leave': ('backward',), 'dirs': None,
        'look': {'eye': 'laughing_man', 'pool': None, 'label': 'TALK', 'color': 'E040A0'},   # always, vision on or off
        'touch': {'tap': 'server', 'double': 'server'},
        'inputs': {'forward': ('say_round',),       # already in talk: forward = one round (spec §12.1)
                   'tap': ('say_round',), 'double_tap': ('prefetch_on',),
                   'next': ('prefetch_on',), 'previous': ('prefetch_off',)},
        'paid': ('say_round', 'prefetch', 'auto'),
    },
    'chat': {
        'name': '对话', 'kind': 'normal', 'enter': ('play',), 'leave': ('pause',), 'dirs': None,
        'look': {'eye': 'v05', 'pool': None, 'label': 'CHAT', 'color': 'F0B000'},
        'touch': {'tap': 'server', 'double': 'server'},          # tap = stop; double = show the dialogue (later)
        'inputs': dict(_SHELL), 'paid': (),
    },
    'settings': {
        'name': '设置', 'kind': 'temporary', 'enter': ('open',), 'leave': ('close', 'timeout'), 'dirs': None,
        'look': {'eye': 'page_settings', 'pool': None, 'label': 'SET', 'color': 'D8D8CC'},
        'touch': {'tap': 'none', 'double': 'none'},
        'inputs': dict(_NO_TAP), 'paid': (),
    },
    'event': {
        # The outer layer. Waiting = the IDLE screen with label AUTO (【我定】, spec §3.2); a hosted guest mode later
        # shows the guest's own look with host = 'event'. Unlike IDLE its tap is none, not badge: waiting is not
        # "nothing running", and a badge flip would hide that event-driven is on (gap b, 【我定】).
        'name': '事件驱动', 'kind': 'host', 'enter': ('start',), 'leave': ('stop',), 'dirs': None,
        'look': {'eye': 'page_idle', 'pool': None, 'label': 'AUTO', 'color': '20B8C8'},
        'touch': {'tap': 'none', 'double': 'none'},
        'inputs': dict(_NO_TAP), 'paid': (),
    },
    'subtitle': {
        'name': '字幕', 'kind': 'normal', 'enter': (), 'leave': (), 'dirs': {'up': 'zh_en', 'down': 'en_zh'},
        'labels': {'zh_en': 'SUB ZH>EN', 'en_zh': 'SUB EN>ZH'},
        'look': {'eye': 'page_subtitle', 'pool': None, 'label': 'SUB', 'color': '7C6CF0'},
        'touch': {'tap': 'none', 'double': 'none'},
        'inputs': dict(_NO_TAP), 'paid': (),
    },
    'crowd': {
        'name': '围观', 'kind': 'normal', 'enter': (), 'leave': (), 'dirs': {'left': None},
        'look': {'eye': 'v14', 'pool': 'lively', 'label': 'MEET', 'color': '30D060'},
        'touch': {'tap': 'server', 'double': 'server'},          # tap = stop; double = protest (later)
        'inputs': dict(_SHELL), 'paid': (),
    },
    'companion': {
        # Spec §3.2: double tap = 无 here, so touch.double = none and a DualKey double click = no_action (the brief
        # lumped companion with chat / crowd as not_implemented; the spec table wins, 【我定】).
        'name': '陪伴', 'kind': 'normal', 'enter': (), 'leave': (), 'dirs': {'right': None},
        'look': {'eye': 'v18', 'pool': None, 'label': 'PAL', 'color': 'F2E6C8'},
        'touch': {'tap': 'server', 'double': 'none'},            # tap = stop / wake (wake later)
        'inputs': {'tap': 'not_implemented', 'double_tap': 'no_action'}, 'paid': (),
    },
    'idle': {
        'name': 'IDLE', 'kind': 'idle', 'enter': (), 'leave': (), 'dirs': None,
        'look': {'eye': 'page_idle', 'pool': None, 'label': 'IDLE', 'color': '505050'},
        'touch': {'tap': 'badge', 'double': 'server'},            # double = music (later)
        'inputs': {'tap': 'no_action', 'double_tap': 'not_implemented'}, 'paid': (),
    },
}

_IDS = ('talk', 'chat', 'settings', 'event', 'subtitle', 'crowd', 'companion', 'idle')
_KINDS = ('normal', 'temporary', 'host', 'idle')
_MODE_INPUTS = ('forward', 'tap', 'double_tap', 'next', 'previous')
_EFFECTS = ('say_round', 'prefetch_on', 'prefetch_off')
_CODES = ('not_implemented', 'no_action')


def _label_ok(s):
    return isinstance(s, str) and 0 < len(s) <= 10 and s.isascii() and s.isprintable()


def _need(ok, what='mode table'):
    if not ok:
        raise ValueError(f'bad mode table: {what!r}')


def _validate(modes):
    _need(tuple(modes) == _IDS, sorted(modes))
    words, dirs = {}, {}
    for mid, m in modes.items():
        _need(m['kind'] in _KINDS, mid)
        _need(isinstance(m['name'], str) and m['name'], mid)
        look = m['look']
        _need(set(look) == {'eye', 'pool', 'label', 'color'}, mid)
        _need(look['eye'] in EYES and look['pool'] in POOLS and _label_ok(look['label']), mid)
        _need(isinstance(look['color'], str) and len(look['color']) == 6 and
              all(c in '0123456789ABCDEF' for c in look['color']), mid)
        _need(set(m['touch']) == {'tap', 'double'} and all(v in TOUCH for v in m['touch'].values()), mid)
        _need(m['touch']['double'] != 'badge', mid)                  # badge is IDLE's single tap only
        _need((m['touch']['tap'] == 'badge') == (mid == 'idle'), mid)
        for w in m['enter'] + m['leave']:
            _need(w not in words, w)
            words[w] = mid
        for d, sub in (m['dirs'] or {}).items():
            _need(d in DIRECTIONS and d not in dirs, (mid, d))
            _need(sub is None or (mid == 'subtitle' and sub in SUBTITLE_DIRS), (mid, d))
            dirs[d] = mid
        if mid == 'subtitle':
            _need(set(m['labels']) == set(SUBTITLE_DIRS) and all(_label_ok(v) for v in m['labels'].values()))
        inputs = m['inputs']
        _need(set(inputs) <= set(_MODE_INPUTS) and {'tap', 'double_tap'} <= set(inputs), mid)
        for k, v in inputs.items():
            _need(v in _CODES if isinstance(v, str) else (isinstance(v, tuple) and v and set(v) <= set(_EFFECTS)), (mid, k))
        # The snapshot's touch is what the robot acts on, the server must agree with it (gap d): a tap the robot
        # handles alone (none / badge) can only reach mind as a DualKey click, and there it does nothing.
        for k, key in (('tap', 'tap'), ('double_tap', 'double')):
            _need((m['touch'][key] == 'server') == (inputs[k] != 'no_action'), (mid, k))
        _need(isinstance(m['paid'], tuple), mid)
    _need(set(dirs) == set(DIRECTIONS), dirs)
    return modes


_validate(MODES)
