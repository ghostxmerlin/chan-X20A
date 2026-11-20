"""hello's modes: the one definition (spec docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md §3.1, §3.2).

A Python module (not JSON) because server/mind/deploy.sh only ships server/mind/*.py. The robot does not know
which mode wears which eyes: it only reads the `look` / `touch` words from the snapshot (modes.snapshot).
PRESETS (0-9, owner's step-1b table) are the second table here; billing.apply_preset runs their server half.

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

# Owner 2026-11-20 (Providence): the robot keeps only Docking. talk / chat / settings / event / subtitle / companion /
# idle are gone (settings was an empty placeholder page; the screen's Wi-Fi and quick-settings pages are not modes).
REMOVED_MODES = ('talk', 'chat', 'settings', 'event', 'subtitle', 'companion', 'idle')

MODES = {
    'crowd': {
        # Shown as Docking (owner 2026-11-18: renamed from 围观, badge DOCK, hello's main mode and boot mode); the id
        # stays crowd on the wire and in the database (display-only rename).
        'name': 'Docking', 'kind': 'normal', 'enter': (), 'leave': (), 'dirs': {'left': None},
        'look': {'eye': 'v14', 'pool': 'lively', 'label': 'DOCK', 'color': '30D060'},
        'touch': {'tap': 'server', 'double': 'server'},
        # owner 2026-11-19 (was tap = look and greet, double tap = preset 1): tap = 'listen': speaking -> stop_speech
        # (toast STOP), else ok / LISTEN and the ear opens a must-answer window (stronger than being called); double
        # tap = 'quiet': ok / QUIET (+ stop_speech while speaking) and the ear's 安静一下 starts. Both are reported
        # (mode_report input tap / double_tap) and the ear's realtime session acts on them.
        'inputs': {'tap': 'listen', 'double_tap': 'quiet'}, 'paid': (),
    },
}

_IDS = ('crowd',)
_KINDS = ('normal', 'temporary', 'host', 'idle')
_MODE_INPUTS = ('forward', 'tap', 'double_tap', 'next', 'previous')
_EFFECTS = ('say_round', 'prefetch_on', 'prefetch_off')
_CODES = ('not_implemented', 'no_action', 'listen', 'quiet')   # crowd's tap / double tap (owner 2026-11-19)


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
        _need(m['touch']['tap'] != 'badge', mid)                     # IDLE (the badge flip) is gone
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
    _need(set(dirs) <= set(DIRECTIONS), dirs)                         # Docking keeps only left
    return modes


_validate(MODES)


# ---- presets 0-9 (owner 2026-11-14 new table, plan step 1b; replaces the step-1 table) --------------------------
# Only 0 and 9 are mind's: hello sends those digits as mode_input (the paid services live on the server). 4-8 are
# mode selects hello does itself (IDLE, 围观, 陪伴, 字幕 中→英, 字幕 英→中) and 1-3 are empty there; on mind 1-8 are
# undefined_preset. server: action names run in order by billing.apply_preset:
#   billing_off      billing shows off; 听悟 / 识别 / 纪要 / 情景 off, 提前写 / 连续说 off, stop speaking
#   billing_on       billing shows on (owner 2026-11-14 evening: billing is the last whole action, never a gate)
#   paid_senses_on   听悟 / 识别 / 纪要 / 情景 on
#   mouth_tts        嘴 -> TTS
#   talk_on          提前写 + 连续说 on; outside talk skipped (said in the toast; entering talk turns them on anyway)
# robot: the robot half, in the page's own tier names (switches-ui.js levelRequests / visionRequests). Only the page
#   carries it out; a robot-sent 9 does the server half and answers senses_on_page. 9 = tingwu / recognize, the paid
#   tiers, so the robot half keeps 听悟 / 识别 on (owner 4: 花费全开).
PRESET_ACTIONS = ('billing_off', 'billing_on', 'paid_senses_on', 'mouth_tts', 'talk_on', 'realtime_toggle')
ROBOT_HALF = {'hearing': ('local', 'tingwu'), 'vision': ('upload', 'recognize')}
_PAID_SENSES = {'hearing': 'tingwu', 'vision': 'recognize'}
_ON_HELLO = {}    # 4-8 were hello's own mode selects; Docking is the only mode since 2026-11-20

PRESETS = {
    0: {'name': '花费全关', 'server': ('billing_off',), 'robot': {}},
    # 1 (owner 2026-11-15 "能让hello在围观模式中闭嘴"): the 围观 realtime switch flips (闭嘴 / 开口); hello sends it for
    # its ASR 1 (a double tap in crowd is 安静一下 since 2026-11-19). 0 / 9 still set it with everything else.
    1: {'name': 'Docking 闭嘴/开口', 'server': ('realtime_toggle',), 'robot': {}},
    **{n: {'name': '空', 'server': (), 'robot': {}} for n in (2, 3, 4, 5, 6, 7, 8)},
    **{n: {'name': f'{name}（hello 自己切）', 'server': (), 'robot': {}} for n, name in _ON_HELLO.items()},
    9: {'name': '花费全开', 'server': ('billing_on', 'paid_senses_on', 'mouth_tts', 'talk_on'), 'robot': dict(_PAID_SENSES)},
}
PRESETS = dict(sorted(PRESETS.items()))


def _validate_presets(presets):
    _need(set(presets) == set(range(10)), 'preset keys')
    for n, p in presets.items():
        _need(set(p) == {'name', 'server', 'robot'} and isinstance(p['name'], str) and p['name'], n)
        _need(isinstance(p['server'], tuple) and all(a in PRESET_ACTIONS for a in p['server']), n)
        _need(isinstance(p['robot'], dict) and all(k in ROBOT_HALF and v in ROBOT_HALF[k] for k, v in p['robot'].items()), n)
    return presets


_validate_presets(PRESETS)
