"""Providence, Docking only (owner 2026-11-20; plan docs/superpowers/plans/2026-11-20-providence-docking-only.md).

mind keeps one mode, crowd (shown as Docking). Today's firmware (0.5.18) still reports the old modes and sends ASR
digits until it is reflashed: mind keeps running and shows Docking, and the factory voice commands do nothing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mind_fixtures import FakeDB  # noqa: E402
from mind import mode_table, modes  # noqa: E402
from mind.mode_store import ModeStore, valid_state  # noqa: E402

CROWD = {'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None}


def go(inp, value=None, speaking=False, source='touch'):
    return modes.transition(dict(CROWD), inp, value, source=source, speaking=speaking)


class OneMode(unittest.TestCase):
    def test_only_crowd(self):
        self.assertEqual(list(mode_table.MODES), ['crowd'])
        self.assertEqual(mode_table.MODES['crowd']['look']['label'], 'DOCK')

    def test_only_the_crowd_state_is_valid(self):
        self.assertTrue(valid_state(dict(CROWD)))
        for mode in ('talk', 'chat', 'settings', 'event', 'subtitle', 'companion', 'idle'):
            self.assertFalse(valid_state(dict(CROWD, mode=mode)), mode)

    def test_boot_and_long_go_to_crowd(self):
        for inp in ('boot', 'long'):
            new, res, effects = go(inp)
            self.assertEqual((new, res['code'], effects), (CROWD, 'ok', ['stop_speech']), inp)

    def test_tap_and_double_tap_keep_their_meaning(self):
        self.assertEqual(go('tap')[1], {'code': 'ok', 'toast': 'LISTEN'})
        self.assertEqual(go('tap', speaking=True)[2], ['stop_speech'])
        self.assertEqual(go('double_tap')[1], {'code': 'ok', 'toast': 'QUIET'})

    def test_select_other_mode_is_refused(self):
        for mode in ('talk', 'chat', 'subtitle', 'idle'):
            new, res, effects = go('select', {'mode': mode})
            self.assertEqual((new, res, effects), (CROWD, {'code': 'removed_mode', 'toast': 'DOCKING ONLY'}, []), mode)

    def test_select_docking_again_stays(self):
        new, res, _ = go('select', {'mode': 'crowd'})
        self.assertEqual((new, res['code']), (CROWD, 'already'))

    def test_voice_words_do_nothing(self):
        for word in ('forward', 'backward', 'open', 'close', 'play', 'pause', 'start', 'stop'):
            new, res, effects = go(word, source='asr')
            self.assertEqual((new, res['code'], effects), (CROWD, 'no_action', []), word)

    def test_stick_directions_do_nothing(self):
        for d in ('up', 'down', 'left', 'right'):
            new, res, _ = go('direction', d, source='dualkey')
            self.assertEqual((new, res['code']), (CROWD, 'already' if d == 'left' else 'unknown_input'), d)


class OldFirmware(unittest.TestCase):
    def setUp(self):
        self.ms = ModeStore(FakeDB(), clock_ms=lambda: 1000)

    def report(self, mode, seq=1, effects=()):
        body = {'seq': seq, 'mode': mode, 'dir': 'zh_en' if mode == 'subtitle' else None, 'host': None,
                'return_to': 'crowd' if mode == 'settings' else None, 'by': 'asr', 'input': 'forward',
                'effects': list(effects)}
        return self.ms.report(body, boot='b1', now_ms=1000 + seq)

    def test_old_firmware_report_is_shown_as_docking(self):
        for i, mode in enumerate(('talk', 'idle', 'settings', 'subtitle')):
            out = self.report(mode, seq=i + 1, effects=['talk_enter', 'prefetch_on'] if mode == 'talk' else [])
            self.assertEqual(out['snapshot']['mode'], 'crowd', mode)
            self.assertEqual(out['effects'], [], mode)          # nothing paid starts from an old mode's effects

    def test_a_crowd_report_still_mirrors(self):
        out = self.report('crowd')
        self.assertEqual(out['snapshot']['mode'], 'crowd')


if __name__ == '__main__':
    unittest.main()
