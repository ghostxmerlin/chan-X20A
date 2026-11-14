"""Dump hello's mode rules as test vectors: tests/data/mode_vectors.json (hello modes step 1b, task F1).

hello now owns the mode (plan docs/superpowers/plans/2026-11-14-hello-modes-step1b-robot-owned.md): the firmware
runs the transition rules itself (fw/mode_rules.h). The rules stay the ones in server/mind/modes.py; these vectors
are the contract between the two. tests/mode_rules_test.cpp replays every one and requires the same new state,
result code, toast and effects (in order). Run again after changing modes.py or mode_table.py:

    python3 -B tests/gen_mode_vectors.py

Covered: every reachable state (each mode; settings with every return_to; subtitle in both directions; every
guest mode hosted by event) x every input (the voice words, tap, double_tap, long, boot, timeout, direction with
each value, select with each mode / direction and bad values, digit 1-8, an unknown word) x speaking / not.

digit is not in modes.py (mind's ModeStore routes it to the presets). The owner's table of 2026-11-14 replaces
the old presets on the robot: 4 = enter idle, 5 = crowd, 6 = companion, 7 = subtitle zh_en, 8 = subtitle en_zh,
1-3 = no_action. "Enter" (controller ruling 2026-11-14): a digit never leaves its mode. Already there (subtitle: in
that direction) -> already, nothing changes; otherwise it runs as `select` (from the other subtitle direction that
switches the direction). 0 and 9 (all paid off / on) stay with mind and are not here; neither is billing.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from mind import modes, mode_table  # noqa: E402
from mind.mode_store import valid_state  # noqa: E402

OUT = ROOT / "tests" / "data" / "mode_vectors.json"
FIELDS = ('mode', 'dir', 'host', 'return_to')
DIGIT_SELECT = {4: {'mode': 'idle'}, 5: {'mode': 'crowd'}, 6: {'mode': 'companion'},
                7: {'mode': 'subtitle', 'dir': 'zh_en'}, 8: {'mode': 'subtitle', 'dir': 'en_zh'}}
DIGIT_EMPTY = (1, 2, 3)


def states():
    out = []
    for mode in mode_table.MODES:
        for d in (None, *mode_table.SUBTITLE_DIRS):
            for host in (None, 'event'):
                for rt in (None, *mode_table.MODES):
                    st = {'mode': mode, 'dir': d, 'host': host, 'return_to': rt}
                    if valid_state(st):
                        out.append(st)
    return out


def inputs():
    words = ('forward', 'backward', 'play', 'pause', 'open', 'close', 'start', 'stop', 'next', 'previous',
             'tap', 'double_tap', 'long', 'boot', 'timeout')
    out = [(w, None) for w in words]
    out += [('direction', d) for d in (*mode_table.DIRECTIONS, 'diagonal', None, 3)]
    for m in mode_table.MODES:
        out.append(('select', {'mode': m}))
    out += [('select', {'mode': 'subtitle', 'dir': d}) for d in (*mode_table.SUBTITLE_DIRS, None, 'zh_zh', 7)]
    out += [('select', {'mode': 'chat', 'dir': 'en_zh'}),        # a dir on another mode is ignored
            ('select', {'mode': 'party'}), ('select', {'mode': None}), ('select', {}), ('select', 'talk'),
            ('select', None)]
    out += [('digit', n) for n in range(1, 9)]
    out.append(('sing', None))                                     # not an input: unknown_input
    return out


def run(st, inp, value, speaking):
    if inp == 'digit':
        if value in DIGIT_EMPTY:
            return dict(st), modes.result('no_action'), []
        target = DIGIT_SELECT[value]
        if st['mode'] == target['mode'] and st['dir'] == target.get('dir'):
            return dict(st), modes.result('already'), []       # enter, never leave
        return modes.transition(st, 'select', target, source='asr', speaking=speaking)
    return modes.transition(st, inp, value, source='asr', speaking=speaking)


def table():
    out = {}
    for mid, m in mode_table.MODES.items():
        row = {'label': m['look']['label'], 'color': m['look']['color'], 'eye': m['look']['eye'],
               'pool': m['look']['pool'], 'tap': m['touch']['tap'], 'double': m['touch']['double']}
        if 'labels' in m:
            row['labels'] = m['labels']
        out[mid] = row
    return out


def main():
    vectors = []
    for st in states():
        for inp, value in inputs():
            for speaking in (False, True):
                new, res, effects = run(st, inp, value, speaking)
                assert valid_state(new), (st, inp, value, new)
                vectors.append([[st[k] for k in FIELDS], inp, value, speaking,
                                [new[k] for k in FIELDS], res['code'], res['toast'], effects])
    body = {
        'about': 'hello mode rules (server/mind/modes.py + the owner digit table of 2026-11-14), made by '
                 'tests/gen_mode_vectors.py. Each vector: [[mode, dir, host, return_to], input, value, speaking, '
                 '[new mode, dir, host, return_to], code, toast, effects].',
        'digits': {'select': {str(k): v for k, v in DIGIT_SELECT.items()}, 'no_action': list(DIGIT_EMPTY),
                   'mind': [0, 9]},
        'table': table(),
        'vectors': vectors,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ['{']
    for k in ('about', 'digits', 'table'):
        lines.append(f'  {json.dumps(k)}: {json.dumps(body[k], ensure_ascii=False)},')
    lines.append('  "vectors": [')
    lines += [f'    {json.dumps(v, ensure_ascii=False)}{"," if i + 1 < len(vectors) else ""}' for i, v in enumerate(vectors)]
    lines += ['  ]', '}']
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(vectors)} vectors, {len(states())} states -> {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
