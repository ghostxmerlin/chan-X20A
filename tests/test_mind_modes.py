"""hello's modes: the mode table and the pure state machine (server/mind/mode_table.py, modes.py).

Since 2026-11-20 (Providence) Docking (crowd) is the only mode; its own rules are in tests/test_docking_only.py.
These are the general properties: purity, the result shape, routing, table validation, the snapshot.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from mind import modes, mode_table  # noqa: E402

CROWD = {'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None}


def go(inp, value=None, speaking=False, source='asr'):
    return modes.transition(dict(CROWD), inp, value, source=source, speaking=speaking)


class ModesTest(unittest.TestCase):
    def test_table_is_valid(self):
        self.assertEqual(set(mode_table.MODES), {'crowd'})
        self.assertEqual(mode_table.MODES['crowd']['look'], {'eye': 'v14', 'pool': 'lively', 'label': 'DOCK',
                                                             'color': '30D060'})

    def test_next_previous_have_no_talk_show_any_more(self):
        for inp in ('next', 'previous'):
            s, r, e = go(inp)
            self.assertEqual((s, r['code'], e), (CROWD, 'only_talk', []))

    def test_server_side_tap_results_follow_snapshot_touch(self):
        for inp in ('tap', 'double_tap'):
            self.assertNotEqual(go(inp, source='dualkey')[1]['code'], 'no_action', inp)

    def test_transition_does_not_mutate_input(self):
        st = dict(CROWD)
        before = copy.deepcopy(st)
        modes.transition(st, 'boot', None, source='asr')
        self.assertEqual(st, before)

    def test_every_result_has_short_ascii_toast_and_known_code(self):
        inputs = [('forward', None), ('backward', None), ('play', None), ('pause', None), ('open', None),
                  ('close', None), ('start', None), ('stop', None), ('next', None), ('previous', None),
                  ('tap', None), ('double_tap', None), ('long', None), ('boot', None), ('timeout', None),
                  ('wiggle', None)] + [('direction', d) for d in ('up', 'down', 'left', 'right')] + \
                 [('select', {'mode': m}) for m in (*mode_table.MODES, *mode_table.REMOVED_MODES)]
        for inp, val in inputs:
            for speaking in (False, True):
                s, r, e = go(inp, val, speaking=speaking)
                self.assertIn(r['code'], modes.CODES)
                self.assertTrue(r['toast'].isascii() and len(r['toast']) <= 32, r)
                self.assertEqual(s, CROWD, (inp, val))                  # nothing ever leaves Docking
                self.assertTrue(set(e) <= {'stop_speech'}, (inp, e))      # no talk-show effect can come out

    def test_digit_and_billing_are_not_routed_here(self):
        for inp, val in (('digit', 3), ('billing', False)):
            with self.assertRaisesRegex(ValueError, 'routed by ModeStore'):
                go(inp, val)

    def test_bad_state_or_source_raises(self):
        for mode in ('disco', 'talk'):
            with self.assertRaises(ValueError):
                modes.transition(dict(CROWD, mode=mode), 'tap', None, source='asr')
        with self.assertRaises(ValueError):
            go('tap', source='telepathy')

    def test_table_validation_rejects_bad_rows(self):
        breaks = (lambda t: t['crowd']['look'].update(eye='disco'),
                  lambda t: t['crowd']['look'].update(label='DOCKING MODE'),
                  lambda t: t['crowd']['look'].update(label='围观'),
                  lambda t: t['crowd']['touch'].update(tap='badge'),
                  lambda t: t.update(talk=copy.deepcopy(t['crowd'])),
                  lambda t: t.pop('crowd'))
        for brk in breaks:
            t = copy.deepcopy(mode_table.MODES)
            brk(t)
            with self.assertRaises((ValueError, KeyError)):
                mode_table._validate(t)

    def test_snapshot_shape(self):
        snap = modes.snapshot(dict(CROWD), seq=12, since=1763100000000, by='asr', billing=True)
        self.assertEqual(snap, {'seq': 12, 'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None,
                                'billing': True, 'since': 1763100000000, 'by': 'asr',
                                'look': {'eye': 'v14', 'pool': 'lively', 'label': 'DOCK', 'color': '30D060'},
                                'touch': {'tap': 'server', 'double': 'server'}})
        snap['look']['eye'] = 'x'
        self.assertEqual(mode_table.MODES['crowd']['look']['eye'], 'v14')   # a copy, not the table

    def test_the_snapshot_fits_one_frame(self):
        snap = modes.snapshot(dict(CROWD), seq=2**31, since=1763100000000, by='dualkey', billing=True)
        frame = json.dumps({'type': 'mode', **snap}, separators=(',', ':')).encode()
        self.assertLessEqual(len(frame), 1024)
        self.assertIn(snap['look']['eye'], mode_table.EYES)


if __name__ == '__main__':
    unittest.main()
