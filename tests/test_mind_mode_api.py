"""hello modes, mind's mode endpoints (task A4, reworked for step 1b: hello owns the mode, mind mirrors it).

/mode/report (hello's own change, via ear) is mirrored and its effects run through the guard and hooks
(runner.wire_modes); talk-only speech and the billing switch read the mirror; the page and DualKey no longer switch.

The server runs the real ModeStore + Billing on FakeDB, wired exactly as runner.main wires them; tts, 说一轮 and the
speaking state are test doubles, 提前写 / 连续说 are the real workers (their loops never run here).
"""
import copy
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind import api, runner, say_auto, say_prefetch, speak
from mind.settings_store import SettingsStore
from mind.store import Store

ROOT = Path(__file__).resolve().parents[1]


class Tts:
    def __init__(self):
        self.stops, self.said = 0, []

    def health(self):
        return {'mouth': {'device': {'device_id': 'x'}}}

    def say(self, text, **kw):
        self.said.append((text, kw))
        return f'u{len(self.said)}'

    def voices(self):
        return {'voices': {'tang': {'label': '汤师爷', 'rate': 1.15}}}

    def stop(self):
        self.stops += 1
        return []

    def status(self, uid, wait=0):
        return {'state': 'done'}


class ModeApiBase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.build()

    def build(self):
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        self.tts = Tts()
        self.speaking, self.say_calls = False, []
        mouth = lambda: (self.db['switches'].find_one({'_id': 'mouth'}) or {}).get('mode', 'clips')
        self.worker = say_prefetch.Worker(self.store, settings_fn=lambda: self.ss.current()[0], mouth_fn=mouth,
                                          ctx_fn=None, write_fn=None)
        self.auto = say_auto.Worker(self.store, prefetch=self.worker, settings_fn=lambda: self.ss.current()[0],
                                    mouth_fn=mouth, play_fn=None)

        def fake_round(trigger, is_current):
            self.say_calls.append({'trigger': trigger, 'is_current': is_current, 'current': is_current()})

        self.ms, self.billing, self.gate = runner.wire_modes(
            self.db, self.store, settings_store=self.ss, tts=self.tts, say_worker=self.worker, say_auto=self.auto,
            say_round_fn=fake_round, speaking_fn=lambda: self.speaking)
        self.server = api.make_server(self.store, 'k', port=0, ctx={
            'settings_store': self.ss, 'tts': self.tts, 'roles_dir': str(ROOT / 'config/roles'),
            'say_worker': self.worker, 'say_auto': self.auto, 'mode_store': self.ms, 'billing': self.billing,
            'round_gate': self.gate})
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': 'tts'}}, upsert=True)

    def call(self, path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}' + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Bearer k', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def wait_rounds(self, n):
        deadline = time.time() + 3
        while (len(self.say_calls) < n or self.gate.running) and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.say_calls), n)

    def settings_family(self, on):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['speech']['round']['family'] = on
        self.ss.save(s, 'test', expected_version=v)

    def family(self):
        return bool(self.ss.current()[0]['speech']['round'].get('family'))

    def report(self, mode, effects=(), *, by='asr', inp='forward', boot='b1', conn=1, seq=None, age_ms=0, **st):
        """POST /mode/report as ear forwards hello's mode_report; seq counts up by itself."""
        if seq is None:
            self._rseq = getattr(self, '_rseq', 0) + 1
            seq = self._rseq
        body = {'boot': boot, 'conn': conn, 'age_ms': age_ms, 'seq': seq, 'mode': mode, 'dir': st.get('dir'),
                'host': st.get('host'), 'return_to': st.get('return_to'), 'by': by, 'input': inp,
                'effects': list(effects)}
        return self.call('/mode/report', body)

    LEAVE = ('stop_speech', 'talk_leave')
    ENTER = ('stop_speech', 'talk_enter')


class ReportTest(ModeApiBase):
    """hello reports, mind mirrors and carries out the effects."""

    def test_report_is_mirrored_and_read_back(self):
        s, r = self.report('crowd', self.LEAVE, by='touch', inp='select')
        self.assertEqual((s, r['snapshot']['mode'], r['snapshot']['look']['eye'], r['effects']),
                         (200, 'crowd', 'v14', list(self.LEAVE)))
        st = self.call('/mode')[1]
        self.assertEqual((st['snapshot']['mode'], st['log'][0]['to'], st['robot']['seq'], st['robot']['connected']),
                         ('crowd', 'crowd', 1, True))
        self.assertEqual(self.call('/mode/seq')[1]['seq'], r['snapshot']['seq'])

    def test_leaving_talk_stops_paid_behaviors(self):
        self.worker.set_enabled(True, 1); self.auto.set_enabled(True, 1)
        self.report('idle', self.LEAVE, inp='backward')
        self.assertEqual((self.worker.enabled(), self.auto.enabled(), self.tts.stops >= 1), (False, False, True))

    def test_entering_talk_turns_writing_on(self):
        self.settings_family(True)
        self.report('idle', self.LEAVE, inp='backward'); self.report('talk', self.ENTER)
        self.assertEqual((self.worker.enabled(), self.auto.enabled(), self.family()), (True, True, False))
        self.auto.set_enabled(False, 2)
        self.report('talk', self.ENTER, by='boot', inp=None, boot='b9', seq=0)          # hello rebooted: same
        self.assertTrue(self.auto.enabled())

    def test_entering_talk_with_billing_off(self):
        self.report('idle', self.LEAVE, inp='backward')
        self.call('/switches', {'name': 'billing', 'on': False})
        r = self.report('talk', self.ENTER)[1]
        self.assertEqual((r['snapshot']['mode'], r['result']['code'], self.worker.enabled(), self.auto.enabled()),
                         ('talk', 'ok', False, False))

    def test_forward_in_talk_starts_one_round_then_too_soon(self):
        self.assertEqual(self.report('talk', ['say_round'])[1]['result']['code'], 'ok')
        self.wait_rounds(1)
        self.assertEqual(self.say_calls[0]['trigger'], {'source': 'asr', 'input': 'forward', 'sid': None})
        self.assertEqual(self.report('talk', ['say_round'])[1]['result']['code'], 'too_soon')

    def test_tap_while_speaking_stops(self):
        self.report('talk', ['stop_speech'], by='touch', inp='tap')        # hello knows it is playing: tap = stop
        self.assertEqual((self.tts.stops >= 1, len(self.say_calls)), (True, 0))

    def test_same_report_twice_runs_once(self):
        body = dict(seq=7)
        self.report('talk', ['say_round'], **body)
        self.wait_rounds(1)
        self.gate.last = None                                             # not too_soon: only dedup may stop it
        r = self.report('talk', ['say_round'], **body)[1]
        self.assertEqual((r.get('duplicate'), len(self.say_calls)), (True, 1))

    def test_older_report_is_stale(self):
        self.report('crowd', self.LEAVE, inp='select', seq=5)
        r = self.report('idle', self.LEAVE, inp='backward', seq=4)[1]
        self.assertEqual((r['result']['code'], self.call('/mode')[1]['snapshot']['mode']), ('stale', 'crowd'))

    def test_late_report_keeps_only_the_turn_offs(self):
        self.worker.set_enabled(True, 1)
        r = self.report('crowd', self.LEAVE, inp='select', age_ms=5000)[1]
        self.assertEqual((r['effects'], self.worker.enabled(), self.tts.stops), (['talk_leave'], False, 0))

    def test_safety_net_outside_talk(self):
        self.worker.set_enabled(True, 1); self.auto.set_enabled(True, 1)
        self.report('crowd', ['stop_speech'], inp='select')                 # talk_leave lost on the way
        self.assertEqual((self.worker.enabled(), self.auto.enabled()), (False, False))

    def test_paid_effects_outside_talk_are_refused(self):
        # review I2 (defence in depth): the rules never send these outside talk, but a report that does starts nothing
        r = self.report('idle', ['say_round'], inp='forward')[1]
        self.assertEqual((r['result']['code'], r['effects'], self.say_calls), ('only_talk', [], []))
        r = self.report('crowd', ['prefetch_on'], by='touch', inp='double_tap')[1]
        self.assertEqual((r['result']['code'], r['effects'], self.worker.enabled(), self.auto.enabled()),
                         ('only_talk', [], False, False))
        self.assertIsNone(self.gate.last)                                  # no 10 s reservation taken
        meta = {'source': 'asr', 'input': 'x', 'value': None, 'device': 'robot.report', 'sid': None, 'mode': 'idle'}
        self.assertEqual([self.ms.guard(e, meta) for e in ('say_round', 'prefetch_on', 'talk_enter')],
                         ['only_talk', 'only_talk', None])
        r = self.report('talk', ['say_round'])[1]                          # in talk it still works
        self.assertEqual(r['result']['code'], 'ok')

    def test_bad_reports(self):
        self.assertEqual(self.report('fly')[0], 400)
        self.assertEqual(self.report('settings')[0], 400)                 # settings without return_to
        self.assertEqual(self.report('talk', ['fly'])[0], 400)
        self.assertEqual(self.report('talk', by='nobody')[0], 400)
        self.assertEqual(self.report('talk', boot='')[0], 400)
        self.assertEqual(self.report('talk', age_ms=-1)[0], 400)
        self.assertEqual(self.call('/mode/report', {'boot': 'b1', 'seq': 1, 'mode': 'idle', 'by': 'asr'})[1]['effects'], [])

    def test_robot_closed(self):
        self.report('talk', conn=4)
        self.assertEqual(self.call('/mode/robot', {'boot': 'b1', 'conn': 3, 'event': 'closed'})[0], 200)
        self.assertTrue(self.call('/mode')[1]['robot']['connected'])            # an older link closing late
        self.call('/mode/robot', {'boot': 'b1', 'conn': 4, 'event': 'closed'})
        self.assertFalse(self.call('/mode')[1]['robot']['connected'])
        for event in ('hello', 'applied'):
            self.assertEqual(self.call('/mode/robot', {'boot': 'b1', 'conn': 4, 'event': event})[0], 400)


class NotFromHereTest(ModeApiBase):
    """The page and DualKey no longer switch modes; mind keeps the master switch and presets 0 / 9."""

    def test_page_mode_inputs_are_refused_except_billing(self):
        for body in ({'input': 'select', 'value': {'mode': 'crowd'}}, {'input': 'forward'}, {'input': 'digit', 'value': 9}):
            s, r = self.call('/mode', body)
            self.assertEqual((s, r['error'], r['code']), (409, 'mode is switched on hello', 'on_hello'), body)
        s, r = self.call('/mode', {'input': 'billing', 'value': False})
        self.assertEqual((s, r['snapshot']['billing'], r['snapshot']['mode']), (200, False, 'talk'))
        self.assertEqual(self.call('/mode', {'input': 'fly'})[0], 400)
        self.assertEqual(self.call('/mode', {'input': 'boot'})[0], 400)

    def test_robot_inputs_are_digits_and_billing_only(self):
        base = {'device': 'robot', 'boot': 'b1', 'seq': 1, 'age_ms': 0, 'source': 'asr', 'input': 'forward'}
        s, r = self.call('/mode/input', base)
        self.assertEqual((s, r['code']), (409, 'on_hello'))
        r = self.call('/mode/input', dict(base, seq=2, input='digit', value=0))[1]
        self.assertEqual((r['result']['code'], r['snapshot']['billing']), ('ok', False))
        r = self.call('/mode/input', dict(base, seq=3, input='digit', value=4))[1]
        self.assertEqual(r['result']['code'], 'undefined_preset')            # 4-8: hello selects them itself
        self.assertEqual(self.call('/mode/input', dict(base, source='dualkey', input='digit', value=0))[0], 400)

    def test_dualkey_signals_do_not_touch_the_mode(self):
        src = {'device': 'dualkey-aa', 'boot': 'x', 'seq': 3}
        s, r = self.call('/signal', {'kind': 'mode', 'value': {'direction': 'left'}, 'age_ms': 120, 'source': src})
        self.assertEqual((s, self.call('/mode')[1]['snapshot']['mode']), (200, 'talk'))
        self.call('/signal', {'kind': 'click', 'value': {'type': 'single'}, 'age_ms': 0, 'source': dict(src, seq=4)})
        self.assertEqual((self.tts.stops, self.say_calls, self.db.count('mode_log')), (0, [], 0))
        self.assertNotIn('result', self.db['owner_signals'].docs[-1])
        self.call('/signal', {'kind': 'rating', 'value': {'polarity': 1, 'count': 2}, 'age_ms': 45_000,
                              'source': dict(src, seq=5)})
        self.assertEqual((self.db.count('owner_signals'), self.db['owner_signals'].docs[-1]['kind']), (3, 'rating'))

    def test_presets(self):
        r = self.call('/preset', {'n': 9, 'source': 'page'})[1]
        self.assertEqual((r['robot'], r['result']['code']), ({'hearing': 'tingwu', 'vision': 'recognize'}, 'ok'))
        self.assertEqual(self.call('/preset', {'n': 1, 'source': 'page'})[1]['result']['code'], 'undefined_preset')
        r = self.call('/preset', {'n': 0, 'source': 'page'})[1]
        self.assertEqual((r['robot'], r['snapshot']['billing']), ({}, False))
        self.assertEqual(self.call('/preset', {'n': 10})[0], 400)


class CarriedTest(ModeApiBase):
    """A4 review items, kept through step 1b."""

    def test_talk_enter_goes_through_billing_with_is_current(self):
        calls, real = [], self.billing.talk_enter

        def spy(now_ms, *, is_current):
            calls.append(is_current)
            return real(now_ms, is_current=is_current)
        self.billing.talk_enter = spy
        self.report('idle', self.LEAVE, inp='backward'); self.report('talk', self.ENTER)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]())
        self.report('idle', self.LEAVE, inp='backward')
        self.assertFalse(calls[0]())                     # a later mode change: no longer current

    def test_guard_answers_none_for_talk_enter(self):
        meta = {'source': 'asr', 'input': 'forward', 'value': None, 'device': 'robot.report', 'sid': None, 'mode': 'talk'}
        self.assertIsNone(self.ms.guard('talk_enter', meta))
        self.call('/switches', {'name': 'billing', 'on': False})
        self.assertIsNone(self.ms.guard('talk_enter', meta))

    def test_say_round_gets_is_current_of_its_commit(self):
        self.report('talk', ['say_round'])
        self.wait_rounds(1)
        self.assertTrue(self.say_calls[0]['current'])
        self.report('idle', self.LEAVE, inp='backward')
        self.assertFalse(self.say_calls[0]['is_current']())

    def test_prefetch_on_needs_the_commit_to_be_current(self):
        hook = self.ms.on_change[0]
        snap = self.ms.current()
        meta = {'source': 'touch', 'input': 'double_tap', 'value': None, 'device': 'robot.report', 'sid': None, 'mode': 'talk'}
        hook(snap, dict(snap, seq=snap['seq'] - 1), ['prefetch_on'], meta, 1)       # superseded commit
        self.assertFalse(self.worker.enabled())
        hook(snap, snap, ['prefetch_on'], meta, 2)
        self.assertTrue(self.worker.enabled())

    def test_double_tap_in_talk_turns_prefetch_on(self):
        r = self.report('talk', ['prefetch_on'], by='touch', inp='double_tap')[1]
        self.assertEqual((r['result']['code'], r['effects'], self.worker.enabled()), ('ok', ['prefetch_on'], True))

    def test_restart_with_billing_off_turns_paid_switches_off_again(self):
        self.db['mode'].update_one({'_id': 'current'}, {'$set': {'billing': 'yes'}})     # corrupt: reads off
        for n in ('tingwu', 'eye', 'notes'):
            self.db['switches'].update_one({'_id': n}, {'$set': {'on': True}}, upsert=True)
        self.store.set_state(enabled=True)
        self.build()                                                           # a mind restart
        sw = self.call('/switches')[1]
        self.assertEqual((sw['billing']['on'], sw['tingwu']['on'], sw['eye']['on'], sw['notes']['on'],
                          self.store.enabled()), (False, False, False, False, False))

    def test_restart_with_billing_on_touches_nothing(self):
        self.db['switches'].update_one({'_id': 'tingwu'}, {'$set': {'on': True}}, upsert=True)
        self.build()
        self.assertTrue(self.call('/switches')[1]['tingwu']['on'])
        self.assertFalse(self.worker.enabled())          # a mind restart is not an entry into talk

    def test_billing_off_blocks_paid_switches_and_speech(self):
        self.call('/switches', {'name': 'tingwu', 'on': True})
        self.call('/switches', {'name': 'billing', 'on': False})
        sw = self.call('/switches')[1]
        self.assertEqual((sw['billing']['on'], sw['tingwu']['on']), (False, False))
        self.assertEqual(self.call('/switches', {'name': 'eye', 'on': True})[0], 409)
        self.assertEqual(self.call('/switch', {'enabled': True})[0], 409)
        self.assertEqual(self.call('/voice/try', {'voice': 'v1'})[0], 409)

    def test_billing_change_bumps_seq(self):
        s0 = self.call('/mode/seq')[1]['seq']
        self.call('/switches', {'name': 'billing', 'on': False})
        self.assertEqual(self.call('/mode/seq')[1]['seq'], s0 + 1)
        self.assertFalse(self.call('/mode')[1]['snapshot']['billing'])

    def test_billing_off_still_lets_paid_switches_go_off(self):
        self.call('/switches', {'name': 'billing', 'on': False})
        self.assertEqual(self.call('/switches', {'name': 'tingwu', 'on': False})[0], 200)
        self.assertEqual(self.call('/switches', {'name': 'eye', 'on': False})[0], 200)
        self.assertEqual(self.call('/switch', {'enabled': False})[0], 200)
        s, r = self.call('/switches', {'name': 'tingwu', 'on': True})
        self.assertEqual((s, r['error'], r['code']), (409, '计费关着', 'billing_off'))

    def test_billing_back_on(self):
        self.call('/switches', {'name': 'billing', 'on': False})
        s, r = self.call('/switches', {'name': 'billing', 'on': True})
        self.assertEqual((s, r['billing']['on'], r['result']['code']), (200, True, 'ok'))
        self.assertIsInstance(r['billing']['at'], int)
        self.assertEqual(self.call('/switches', {'name': 'eye', 'on': True})[0], 200)

    def test_billing_switch_needs_a_bool(self):
        self.assertEqual(self.call('/switches', {'name': 'billing', 'on': 'no'})[0], 400)

    def test_partly_failed_billing_off_says_what_failed(self):
        real = self.billing.ctx.set_switch

        def broken(name, on):
            if name == 'notes':
                raise RuntimeError('db down')
            real(name, on)
        self.billing.ctx.set_switch = broken
        s, r = self.call('/switches', {'name': 'billing', 'on': False})
        self.assertEqual((s, r['failed'], r['billing']['on'], r['result']['toast']), (200, ['notes'], False, 'BILLING OFF, 1 FAILED'))

    def test_mode_input_needs_boot_and_seq(self):
        base = {'device': 'robot', 'boot': 'b1', 'seq': 1, 'age_ms': 0, 'source': 'asr', 'input': 'digit', 'value': 0}
        for drop in ('device', 'boot', 'seq', 'age_ms', 'source', 'input'):
            body = {k: v for k, v in base.items() if k != drop}
            self.assertEqual(self.call('/mode/input', body)[0], 400, drop)
        self.assertEqual(self.call('/mode/input', dict(base, seq=True))[0], 400)
        self.assertEqual(self.call('/mode/input', dict(base, source='page'))[0], 400)
        self.assertEqual(self.call('/mode/input', dict(base, input='boot'))[0], 400)
        self.assertTrue(self.call('/mode')[1]['snapshot']['billing'])
        self.assertFalse(self.call('/mode/input', base)[1]['snapshot']['billing'])

    def test_robot_preset_nine_sent_twice_runs_once(self):
        self.call('/switches', {'name': 'billing', 'on': False})
        body = {'device': 'robot', 'boot': 'b1', 'seq': 7, 'age_ms': 0, 'source': 'asr', 'input': 'digit', 'value': 9}
        r1 = self.call('/mode/input', body)[1]
        self.assertEqual((r1['result']['code'], self.call('/switches')[1]['tingwu']['on']), ('senses_on_page', True))
        self.call('/switches', {'name': 'tingwu', 'on': False})
        r2 = self.call('/mode/input', body)[1]
        self.assertEqual((r2.get('duplicate'), r2['result'], self.call('/switches')[1]['tingwu']['on']),
                         (True, r1['result'], False))

    def test_say_endpoints_outside_talk_say_only_talk(self):
        self.report('chat', self.LEAVE, inp='play')
        for path, body in (('/say', {'role': 'roast'}), ('/say/round', {}), ('/say/play', {'rid': 'r1', 'line': 'a'}),
                           ('/say/auto', {'enabled': True}), ('/say/prefetch', {'enabled': True})):
            s, r = self.call(path, body)
            self.assertEqual((s, r['error'], r['code']), (409, '只在脱口秀模式', 'only_talk'), path)
        self.assertEqual(self.call('/say/prefetch', {'enabled': False})[0], 200)       # turning off always works
        self.assertEqual(self.call('/say/auto', {'enabled': False})[0], 200)
        self.assertEqual(self.call('/say/stop', {})[0], 200)

    def test_say_endpoints_with_billing_off(self):
        self.call('/switches', {'name': 'billing', 'on': False})
        for path, body in (('/say', {'role': 'roast'}), ('/say/round', {}), ('/say/prefetch', {'enabled': True}),
                           ('/role/try', {'role': 'roast'}), ('/voice/try', {'voice': 'tang'})):
            s, r = self.call(path, body)
            self.assertEqual((s, r['code']), (409, 'billing_off'), path)
        self.assertEqual(self.call('/say/stop', {})[0], 200)

    def test_in_talk_the_say_pipeline_is_todays(self):
        s, r = self.call('/say/round', {})            # no model access in this ctx: today's own refusal
        self.assertEqual((s, r.get('code'), r['error']), (409, None, 'soul-tts or model access not configured'))
        self.assertEqual(self.call('/say/prefetch', {'enabled': True})[1], {'enabled': True})

    def test_family_already_off_writes_no_settings_version(self):
        v0 = self.ss.current()[1]
        self.report('idle', self.LEAVE, inp='backward'); self.report('talk', self.ENTER)
        self.assertEqual(self.ss.current()[1], v0)
        self.settings_family(True)
        self.report('idle', self.LEAVE, inp='backward'); self.report('talk', self.ENTER)
        self.assertEqual(self.ss.history()[0]['note'], '家人声音 关（进脱口秀）')

    def test_say_round_needs_mouth_on_tts(self):
        self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': 'clips'}})
        r = self.report('talk', ['say_round'])[1]
        self.assertEqual((r['result']['code'], r['effects']), ('mouth_not_tts', []))
        self.assertEqual(self.say_calls, [])

    def test_say_round_while_one_runs_is_busy(self):
        self.gate.running = True
        self.assertEqual(self.report('talk', ['say_round'])[1]['result']['code'], 'busy')

    def test_page_round_counts_for_too_soon(self):
        self.gate.note(int(time.time() * 1000))
        self.assertEqual(self.report('talk', ['say_round'])[1]['result']['code'], 'too_soon')


class NoModesTest(unittest.TestCase):
    """Without a mode store in ctx (old deployments, other tests) nothing is gated."""

    def test_no_mode_store_no_gate(self):
        db = FakeDB()
        server = api.make_server(Store(db), 'k', port=0, ctx={})
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def call(path, body):
            req = urllib.request.Request(f'http://127.0.0.1:{port}' + path, data=json.dumps(body).encode(),
                                         headers={'Authorization': 'Bearer k', 'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, json.load(e)
        self.assertEqual(call('/switches', {'name': 'tingwu', 'on': True})[0], 200)
        self.assertEqual(call('/mode', {'input': 'billing', 'value': False})[0], 409)
        self.assertNotIn('billing', call('/switches', {'name': 'tingwu', 'on': False})[1])


class WorkersModeTest(unittest.TestCase):
    """say_prefetch / say_auto skip their work outside talk (defence in depth)."""

    class St:
        def __init__(self):
            self.state = {}

        def set_state(self, **kw):
            self.state.update(kw)

    def test_prefetch_skips_outside_talk(self):
        w = say_prefetch.Worker(self.St(), settings_fn=lambda: {'speech': {'pipeline': 'round'}}, mouth_fn=lambda: 'tts',
                                ctx_fn=lambda s, now: self.fail('no context outside talk'), write_fn=None,
                                mode_fn=lambda: 'idle')
        w.set_enabled(True, 0)
        self.assertEqual((w.step(5), w.last), ('not_talk', {'reason': 'not_talk', 'at': 5}))

    def test_auto_skips_outside_talk(self):
        p = say_prefetch.Worker(self.St(), settings_fn=dict, mouth_fn=lambda: 'tts', ctx_fn=None, write_fn=None)
        a = say_auto.Worker(self.St(), prefetch=p, settings_fn=lambda: {'speech': {'pipeline': 'round'}},
                            mouth_fn=lambda: 'tts', play_fn=lambda *a: self.fail('no play outside talk'),
                            mode_fn=lambda: 'crowd')
        a.set_enabled(True, 0)
        self.assertEqual(a.tick(a.next_at), 'not_talk')
        self.assertEqual(a.last['reason'], 'not_talk')

    def test_default_mode_fn_is_talk(self):
        w = say_prefetch.Worker(self.St(), settings_fn=lambda: {'speech': {'pipeline': 'v1'}}, mouth_fn=lambda: 'tts',
                                ctx_fn=None, write_fn=None)
        w.set_enabled(True, 0)
        self.assertEqual(w.step(1), 'off')


class SpeakingTest(unittest.TestCase):
    """speak.speaking(): a line handed to soul-tts counts until its status says it ended, or a stop."""

    def test_speaking_while_a_line_plays(self):
        release = threading.Event()

        class SlowTts(Tts):
            def status(self, uid, wait=0):
                release.wait(3)
                return {'state': 'done'}
        db, tts = FakeDB(), SlowTts()
        db['utterances'].insert_one({'uid': 'u1'})
        self.assertFalse(speak.speaking())
        t = threading.Thread(target=speak._finish, args=(db, tts, 'u1', 't1'))
        t.start()
        deadline = time.time() + 2
        while not speak.speaking() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(speak.speaking())
        speak.cancel_pending(tts)                          # a stop ends it at once
        self.assertFalse(speak.speaking())
        release.set(); t.join(3)
        self.assertFalse(speak.speaking())


if __name__ == '__main__':
    unittest.main()
