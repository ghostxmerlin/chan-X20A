"""hello modes step 1: the mode table and the pure state machine (server/mind/mode_table.py, modes.py).

Spec docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md §3.2, §3.5, §3.8; plan task A1.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from mind import modes, mode_table


class TransitionTest(unittest.TestCase):
    def go(self, mode, inp, value=None, speaking=False, **st):
        s = {'mode': mode, 'dir': None, 'host': None, 'return_to': None, **st}
        return modes.transition(s, inp, value, source='asr', speaking=speaking)

    def test_pairs_enter_and_close_to_idle(self):
        for enter, leave, m in (('forward', 'backward', 'talk'), ('play', 'pause', 'chat'), ('start', 'stop', 'event')):
            s, r, e = self.go('idle', enter)
            self.assertEqual((s['mode'], r['code']), (m, 'ok'))
            s, r, e = self.go(m, leave)
            self.assertEqual(s['mode'], 'idle')
            self.assertEqual(e[0], 'stop_speech')

    def test_opening_another_mode_goes_straight_there(self):
        self.assertEqual(self.go('chat', 'open')[0]['mode'], 'settings')
        self.assertEqual(self.go('crowd', 'play')[0]['mode'], 'chat')
        self.assertEqual(self.go('chat', 'select', {'mode': 'chat'})[0]['mode'], 'idle')    # picking the current one closes it

    def test_leave_word_outside_mode_changes_nothing(self):
        s, r, e = self.go('talk', 'pause')
        self.assertEqual((s['mode'], r['code'], e), ('talk', 'not_in_mode', []))

    def test_talk_inputs(self):
        self.assertEqual(self.go('talk', 'forward')[2], ['say_round'])
        self.assertEqual(self.go('talk', 'tap')[2], ['say_round'])
        self.assertEqual(self.go('talk', 'tap', speaking=True)[2], ['stop_speech'])
        self.assertEqual(self.go('talk', 'double_tap')[2], ['prefetch_on'])
        self.assertEqual(self.go('talk', 'previous')[2], ['prefetch_off'])
        self.assertEqual(self.go('idle', 'next')[1]['code'], 'only_talk')
        self.assertEqual(self.go('idle', 'forward')[2], ['stop_speech', 'talk_enter'])   # outside talk, forward enters (owner 14)

    def test_entering_talk_emits_talk_enter(self):
        self.assertEqual(self.go('idle', 'forward')[2], ['stop_speech', 'talk_enter'])
        self.assertEqual(self.go('settings', 'close', return_to='talk')[2], ['stop_speech', 'talk_enter'])
        self.assertEqual(self.go('talk', 'boot')[2], ['stop_speech', 'talk_enter'])     # robot reboot while already in talk
        self.assertEqual(self.go('crowd', 'long')[2], ['stop_speech', 'talk_enter'])

    def test_forward_inside_talk_is_not_an_entry(self):
        self.assertEqual(self.go('talk', 'forward')[2], ['say_round'])

    def test_leaving_talk_adds_talk_leave(self):
        self.assertEqual(self.go('talk', 'play')[2], ['stop_speech', 'talk_leave'])

    def test_dualkey_directions(self):
        s, _, _ = self.go('talk', 'direction', 'up')
        self.assertEqual((s['mode'], s['dir']), ('subtitle', 'zh_en'))
        s, _, _ = self.go('subtitle', 'direction', 'down', dir='zh_en')
        self.assertEqual((s['mode'], s['dir']), ('subtitle', 'en_zh'))
        self.assertEqual(self.go('crowd', 'direction', 'left')[0]['mode'], 'idle')
        self.assertEqual(self.go('crowd', 'direction', 'right')[0]['mode'], 'companion')

    def test_settings_is_a_temporary_layer(self):
        s, _, _ = self.go('crowd', 'open')
        self.assertEqual((s['mode'], s['return_to']), ('settings', 'crowd'))
        self.assertEqual(self.go('settings', 'close', return_to='crowd')[0]['mode'], 'crowd')
        self.assertEqual(self.go('settings', 'timeout', return_to='talk')[0]['mode'], 'talk')
        s, _, _ = self.go('settings', 'select', {'mode': 'chat'}, return_to='talk')
        self.assertEqual((s['mode'], s['return_to']), ('chat', None))

    def test_shell_taps_and_long_press(self):
        for m in ('chat', 'crowd', 'companion'):
            self.assertEqual(self.go(m, 'tap')[1]['code'], 'not_implemented')
            self.assertEqual(self.go(m, 'tap', speaking=True)[2], ['stop_speech'])
        for m in ('subtitle', 'event', 'settings'):
            self.assertEqual(self.go(m, 'tap')[1]['code'], 'no_action')
        self.assertEqual(self.go('idle', 'tap')[1]['code'], 'no_action')            # DualKey click: no badge flip
        self.assertEqual(self.go('talk', 'boot')[2], ['stop_speech', 'talk_enter'])  # robot reboot while already in talk
        s, r, e = self.go('crowd', 'long')
        self.assertEqual((s['mode'], e[0]), ('talk', 'stop_speech'))
        self.assertEqual(self.go('idle', 'boot')[0]['mode'], 'talk')

    def test_leaving_settings_any_way_clears_return_to(self):
        for inp, val in (('play', None), ('direction', 'left'), ('long', None), ('boot', None), ('select', {'mode': 'idle'})):
            self.assertIsNone(self.go('settings', inp, val, return_to='crowd')[0]['return_to'])

    def test_stop_ends_hosted_guest_and_open_stops_event(self):
        s, _, _ = self.go('crowd', 'stop', host='event')
        self.assertEqual((s['mode'], s['host']), ('idle', None))
        s, _, _ = self.go('crowd', 'open', host='event')
        self.assertEqual((s['mode'], s['return_to'], s['host']), ('settings', 'crowd', None))
        self.assertEqual(self.go('event', 'open')[0]['return_to'], 'idle')

    def test_manual_pick_stops_event_driven(self):
        s, _, e = self.go('event', 'play')
        self.assertEqual((s['mode'], s['host']), ('chat', None))
        self.assertEqual(self.go('chat', 'pause')[0]['mode'], 'idle')       # not back to event-driven

    def test_event_waiting_shows_idle_screen(self):
        snap = modes.snapshot({'mode': 'event', 'dir': None, 'host': None, 'return_to': None}, seq=1, since=1, by='asr', billing=True)
        self.assertEqual((snap['look']['eye'], snap['look']['label']), ('page_idle', 'AUTO'))

    def test_snapshot_uses_table(self):
        st = {'mode': 'chat', 'dir': None, 'host': None, 'return_to': None}
        snap = modes.snapshot(st, seq=7, since=1, by='page', billing=False)
        self.assertEqual((snap['look']['eye'], snap['touch']['tap'], snap['billing']), ('v05', 'server', False))
        idle = modes.snapshot({**st, 'mode': 'idle'}, seq=8, since=1, by='page', billing=True)
        self.assertEqual(idle['touch'], {'tap': 'badge', 'double': 'server'})
        self.assertLessEqual(len(json.dumps(snap).encode()), 1024)

    def test_table_is_valid(self):
        self.assertEqual(set(mode_table.MODES), {'talk', 'chat', 'settings', 'event', 'subtitle', 'crowd', 'companion', 'idle'})
        self.assertEqual(mode_table.MODES['talk']['look']['eye'], 'laughing_man')
        self.assertEqual(mode_table.MODES['crowd']['look']['eye'], 'v14')
        self.assertEqual(mode_table.MODES['companion']['look']['eye'], 'v18')
        for m in mode_table.MODES.values():
            self.assertTrue(m['look']['label'].isascii() and len(m['look']['label']) <= 10)


class EdgeCaseTest(unittest.TestCase):
    """Beyond the brief: spec §3.5 / §3.8 edges and the four residual gaps (a)-(d)."""

    def go(self, mode, inp, value=None, speaking=False, source='asr', **st):
        s = {'mode': mode, 'dir': None, 'host': None, 'return_to': None, **st}
        return modes.transition(s, inp, value, source=source, speaking=speaking)

    # --- entering / leaving -------------------------------------------------------------
    def test_enter_word_while_already_there_is_already(self):
        for m, word in (('chat', 'play'), ('settings', 'open'), ('event', 'start')):
            s, r, e = self.go(m, word)
            self.assertEqual((s['mode'], r['code'], e), (m, 'already', []))

    def test_leave_word_in_idle_and_toast_names_the_mode(self):
        s, r, e = self.go('idle', 'backward')
        self.assertEqual((s['mode'], r['code'], r['toast'], e), ('idle', 'not_in_mode', 'NOT IN TALK', []))

    def test_close_and_timeout_outside_settings(self):
        for inp in ('close', 'timeout'):
            s, r, e = self.go('talk', inp)
            self.assertEqual((s['mode'], r['code'], e), ('talk', 'not_in_mode', []))

    def test_close_without_return_to_goes_idle(self):
        s, r, e = self.go('settings', 'close')
        self.assertEqual((s['mode'], s['return_to'], r['code'], e), ('idle', None, 'ok', ['stop_speech']))

    def test_select_current_settings_returns(self):
        s, _, _ = self.go('settings', 'select', {'mode': 'settings'}, return_to='chat')
        self.assertEqual((s['mode'], s['return_to']), ('chat', None))

    def test_settings_remembers_subtitle_direction(self):
        s, _, _ = self.go('subtitle', 'open', dir='en_zh')
        self.assertEqual((s['mode'], s['return_to'], s['dir']), ('settings', 'subtitle', 'en_zh'))
        snap = modes.snapshot(s, seq=1, since=1, by='asr', billing=True)
        self.assertEqual(snap['look']['label'], 'SET')
        back, _, _ = modes.transition(s, 'close', None, source='asr')
        self.assertEqual((back['mode'], back['dir'], back['return_to']), ('subtitle', 'en_zh', None))
        other, _, _ = modes.transition(s, 'play', None, source='asr')
        self.assertEqual((other['mode'], other['dir']), ('chat', None))

    def test_leaving_talk_into_settings_closes_prefetch(self):
        s, r, e = self.go('talk', 'open')
        self.assertEqual((s['mode'], s['return_to'], e), ('settings', 'talk', ['stop_speech', 'talk_leave']))

    def test_select_idle_in_idle_is_already(self):
        s, r, e = self.go('idle', 'select', {'mode': 'idle'})
        self.assertEqual((s['mode'], r['code'], e), ('idle', 'already', []))

    def test_select_talk_in_talk_leaves(self):
        s, r, e = self.go('talk', 'select', {'mode': 'talk'}, source='page')
        self.assertEqual((s['mode'], e), ('idle', ['stop_speech', 'talk_leave']))

    # --- subtitle -----------------------------------------------------------------------
    def test_subtitle_same_direction_leaves(self):
        s, r, e = self.go('subtitle', 'direction', 'up', dir='zh_en', source='dualkey')
        self.assertEqual((s['mode'], s['dir'], e), ('idle', None, ['stop_speech']))

    def test_subtitle_direction_switch_is_not_a_mode_change(self):
        s, r, e = self.go('subtitle', 'select', {'mode': 'subtitle', 'dir': 'en_zh'}, dir='zh_en', source='page')
        self.assertEqual((s['mode'], s['dir'], r['code'], r['toast'], e), ('subtitle', 'en_zh', 'ok', 'SUB EN>ZH', []))

    def test_select_subtitle_without_dir(self):
        s, _, _ = self.go('idle', 'select', {'mode': 'subtitle'})
        self.assertEqual((s['mode'], s['dir']), ('subtitle', 'zh_en'))
        s, _, _ = self.go('subtitle', 'select', {'mode': 'subtitle'}, dir='en_zh')
        self.assertEqual(s['mode'], 'idle')

    def test_subtitle_snapshot_label_has_direction(self):
        snap = modes.snapshot({'mode': 'subtitle', 'dir': 'en_zh', 'host': None, 'return_to': None},
                              seq=2, since=1, by='dualkey', billing=True)
        self.assertEqual((snap['look']['eye'], snap['look']['label'], snap['touch']), ('page_subtitle', 'SUB EN>ZH', {'tap': 'none', 'double': 'none'}))

    def test_bad_values_are_unknown_input(self):
        for inp, val in (('direction', 'sideways'), ('select', {'mode': 'party'}), ('select', 'chat'),
                         ('select', {'mode': 'subtitle', 'dir': 'fr_de'}), ('wiggle', None)):
            s, r, e = self.go('talk', inp, val)
            self.assertEqual((s['mode'], r['code'], e), ('talk', 'unknown_input', []), (inp, val))

    # --- long press / boot ----------------------------------------------------------------
    def test_long_in_talk_only_stops(self):
        s, r, e = self.go('talk', 'long', source='dualkey')
        self.assertEqual((s['mode'], r['code'], e), ('talk', 'ok', ['stop_speech']))

    def test_long_clears_host_and_dir(self):
        s, _, e = self.go('crowd', 'long', host='event')
        self.assertEqual((s['mode'], s['host'], e), ('talk', None, ['stop_speech', 'talk_enter']))
        s, _, _ = self.go('subtitle', 'long', dir='zh_en')
        self.assertEqual((s['mode'], s['dir']), ('talk', None))

    def test_boot_from_elsewhere(self):
        s, r, e = self.go('settings', 'boot', return_to='chat', source='boot')
        self.assertEqual((s, e), ({'mode': 'talk', 'dir': None, 'host': None, 'return_to': None}, ['stop_speech', 'talk_enter']))
        s, r, e = self.go('crowd', 'boot', host='event', source='boot')
        self.assertEqual((s['host'], e), (None, ['stop_speech', 'talk_enter']))

    # --- event-driven host ----------------------------------------------------------------
    def test_hosted_guest_leave_returns_to_event_waiting(self):
        s, r, e = self.go('crowd', 'direction', 'left', host='event', source='dualkey')
        self.assertEqual((s['mode'], s['host'], e), ('event', None, ['stop_speech']))
        s, _, _ = self.go('chat', 'pause', host='event')
        self.assertEqual((s['mode'], s['host']), ('event', None))
        s, _, _ = self.go('chat', 'select', {'mode': 'chat'}, host='event', source='page')
        self.assertEqual((s['mode'], s['host']), ('event', None))

    def test_hosted_start_is_already_and_select_event_leaves(self):
        s, r, e = self.go('crowd', 'start', host='event')
        self.assertEqual((s['mode'], s['host'], r['code'], e), ('crowd', 'event', 'already', []))
        s, r, e = self.go('crowd', 'select', {'mode': 'event'}, host='event', source='page')
        self.assertEqual((s['mode'], s['host'], e), ('idle', None, ['stop_speech']))
        s, _, _ = self.go('event', 'select', {'mode': 'event'}, source='page')
        self.assertEqual(s['mode'], 'idle')

    def test_hosted_manual_pick_clears_host(self):
        s, _, _ = self.go('crowd', 'direction', 'right', host='event', source='dualkey')
        self.assertEqual((s['mode'], s['host']), ('companion', None))
        s, _, _ = self.go('crowd', 'select', {'mode': 'idle'}, host='event', source='page')
        self.assertEqual((s['mode'], s['host']), ('idle', None))

    def test_hosted_snapshot_uses_guest_look(self):
        snap = modes.snapshot({'mode': 'crowd', 'dir': None, 'host': 'event', 'return_to': None},
                              seq=3, since=1, by='asr', billing=True)
        self.assertEqual((snap['host'], snap['look']['eye'], snap['look']['pool'], snap['look']['label'], snap['touch']['tap']),
                         ('event', 'v14', 'lively', 'MEET', 'server'))

    # --- taps (gap a, b) --------------------------------------------------------------------
    def test_event_waiting_tap_is_none_but_idle_tap_is_badge(self):
        ev = modes.snapshot({'mode': 'event', 'dir': None, 'host': None, 'return_to': None}, seq=1, since=1, by='asr', billing=True)
        idle = modes.snapshot({'mode': 'idle', 'dir': None, 'host': None, 'return_to': None}, seq=1, since=1, by='asr', billing=True)
        self.assertEqual(ev['look']['eye'], idle['look']['eye'])
        self.assertEqual((ev['touch'], idle['touch']['tap']), ({'tap': 'none', 'double': 'none'}, 'badge'))
        for m in ('event', 'idle'):
            s, r, e = self.go(m, 'tap', speaking=True, source='dualkey')
            self.assertEqual((s['mode'], r['code'], e), (m, 'no_action', []))

    def test_double_taps(self):
        self.assertEqual(self.go('talk', 'double_tap', speaking=True)[2], ['prefetch_on'])
        for m in ('chat', 'crowd'):
            self.assertEqual(self.go(m, 'double_tap')[1]['code'], 'not_implemented')
        for m in ('companion', 'subtitle', 'settings', 'event'):
            self.assertEqual(self.go(m, 'double_tap', speaking=True)[1]['code'], 'no_action', m)
        self.assertEqual(self.go('idle', 'double_tap')[1]['code'], 'not_implemented')

    def test_server_side_tap_results_follow_snapshot_touch(self):
        """touch = none / badge in the snapshot <=> the server answers no_action for a DualKey click."""
        for mid, m in mode_table.MODES.items():
            for inp, key in (('tap', 'tap'), ('double_tap', 'double')):
                code = self.go(mid, inp, source='dualkey')[1]['code']
                if m['touch'][key] == 'server':
                    self.assertNotEqual(code, 'no_action', (mid, inp))
                else:
                    self.assertEqual(code, 'no_action', (mid, inp))

    def test_next_previous_only_in_talk(self):
        for m in ('chat', 'settings', 'event', 'subtitle', 'crowd', 'companion', 'idle'):
            for inp in ('next', 'previous'):
                s, r, e = self.go(m, inp)
                self.assertEqual((s['mode'], r['code'], r['toast'], e), (m, 'only_talk', 'ONLY IN TALK', []))
        self.assertEqual(self.go('talk', 'next')[2], ['prefetch_on'])

    # --- purity and result shape -----------------------------------------------------------
    def test_transition_does_not_mutate_input(self):
        st = {'mode': 'settings', 'dir': None, 'host': None, 'return_to': 'crowd'}
        before = copy.deepcopy(st)
        modes.transition(st, 'close', None, source='asr')
        self.assertEqual(st, before)

    def test_every_result_has_short_ascii_toast_and_known_code(self):
        inputs = [('forward', None), ('backward', None), ('play', None), ('pause', None), ('open', None),
                  ('close', None), ('start', None), ('stop', None), ('next', None), ('previous', None),
                  ('tap', None), ('double_tap', None), ('long', None), ('boot', None), ('timeout', None),
                  ('wiggle', None)] + [('direction', d) for d in ('up', 'down', 'left', 'right')] + \
                 [('select', {'mode': m}) for m in mode_table.MODES]
        for mid in mode_table.MODES:
            for host in (None, 'event'):
                if host and mid in ('event', 'settings', 'idle'):
                    continue
                for inp, val in inputs:
                    for speaking in (False, True):
                        s, r, e = self.go(mid, inp, val, speaking=speaking, host=host)
                        self.assertIn(r['code'], modes.CODES)
                        self.assertTrue(r['toast'].isascii() and len(r['toast']) <= 32, r)
                        self.assertIn(s['mode'], mode_table.MODES)
                        self.assertTrue(set(e) <= set(modes.EFFECTS))
                        if s['mode'] != mid:
                            self.assertEqual(e[0], 'stop_speech', (mid, inp))
                            self.assertEqual('talk_leave' in e, mid == 'talk', (mid, inp))
                        # owner 14: every entry into talk (and boot, even when already there) = talk_enter
                        self.assertEqual('talk_enter' in e, s['mode'] == 'talk' and (mid != 'talk' or inp == 'boot'),
                                         (mid, host, inp, val, speaking))
                        self.assertFalse('talk_enter' in e and 'talk_leave' in e, (mid, inp))
                        if s['mode'] != 'settings':
                            self.assertIsNone(s['return_to'])
                        if s['mode'] != 'subtitle' and not (s['mode'] == 'settings' and s['return_to'] == 'subtitle'):
                            self.assertIsNone(s['dir'])

    def test_digit_and_billing_are_not_routed_here(self):
        # presets and the master switch go through ModeStore / billing (A2, A3); a misroute must be loud
        for inp, val in (('digit', 3), ('billing', False)):
            with self.assertRaisesRegex(ValueError, 'routed by ModeStore'):
                self.go('talk', inp, val)

    def test_bad_state_or_source_raises(self):
        with self.assertRaises(ValueError):
            modes.transition({'mode': 'disco', 'dir': None, 'host': None, 'return_to': None}, 'tap', None, source='asr')
        with self.assertRaises(ValueError):
            self.go('talk', 'tap', source='telepathy')

    def test_table_validation_rejects_bad_rows(self):
        breaks = (lambda t: t['talk']['look'].update(eye='disco'),
                  lambda t: t['chat']['look'].update(label='CHAT MODE 2'),
                  lambda t: t['crowd']['look'].update(label='围观'),
                  lambda t: t['crowd']['touch'].update(tap='badge'),
                  lambda t: t['subtitle']['touch'].update(tap='server'),     # server tap but no_action meaning
                  lambda t: t['chat'].update(enter=('forward',)),
                  lambda t: t.pop('idle'))
        for brk in breaks:
            t = copy.deepcopy(mode_table.MODES)
            brk(t)
            with self.assertRaises(ValueError):
                mode_table._validate(t)

    def test_snapshot_shape(self):
        snap = modes.snapshot({'mode': 'talk', 'dir': None, 'host': None, 'return_to': None},
                              seq=12, since=1763100000000, by='asr', billing=True)
        self.assertEqual(snap, {'seq': 12, 'mode': 'talk', 'dir': None, 'host': None, 'return_to': None, 'billing': True,
                                'since': 1763100000000, 'by': 'asr',
                                'look': {'eye': 'laughing_man', 'pool': None, 'label': 'TALK', 'color': 'E040A0'},
                                'touch': {'tap': 'server', 'double': 'server'}})
        snap['look']['eye'] = 'x'
        self.assertEqual(mode_table.MODES['talk']['look']['eye'], 'laughing_man')   # a copy, not the table

    def test_every_snapshot_fits_one_frame(self):
        for mid in mode_table.MODES:
            for d in ((None,) if mid != 'subtitle' else ('zh_en', 'en_zh')):
                snap = modes.snapshot({'mode': mid, 'dir': d, 'host': None, 'return_to': 'companion'},
                                      seq=2**31, since=1763100000000, by='dualkey', billing=True)
                frame = json.dumps({'type': 'mode', **snap}, separators=(',', ':')).encode()
                self.assertLessEqual(len(frame), 1024)
                self.assertIn(snap['look']['eye'], mode_table.EYES)


if __name__ == '__main__':
    unittest.main()
