"""hello modes step 1, task A4: mind's mode endpoints, the guard and hooks (runner.wire_modes), talk-only speech,
the billing switch on /switches and DualKey directions / clicks from /signal.

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


class BriefTest(ModeApiBase):
    """The brief's tests (task-A4-brief.md Step 1)."""

    def test_page_switches_mode_and_reads_back(self):
        s, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'crowd'}})
        self.assertEqual((s, r['snapshot']['mode'], r['snapshot']['look']['eye']), (200, 'crowd', 'v14'))
        self.assertEqual(self.call('/mode')[1]['log'][0]['to'], 'crowd')
        self.assertEqual(self.call('/mode/seq')[1]['seq'], r['snapshot']['seq'])

    def test_leaving_talk_stops_paid_behaviors(self):
        self.worker.set_enabled(True, 1); self.auto.set_enabled(True, 1)
        self.call('/mode', {'input': 'backward'})
        self.assertEqual((self.worker.enabled(), self.auto.enabled(), self.tts.stops >= 1), (False, False, True))

    def test_entering_talk_turns_writing_on(self):
        self.settings_family(True)
        self.call('/mode', {'input': 'backward'}); self.call('/mode', {'input': 'forward'})
        self.assertEqual((self.worker.enabled(), self.auto.enabled(), self.family()), (True, True, False))
        self.call('/mode/robot', {'boot': 'b9', 'conn': 9, 'event': 'hello'})          # reboot while in talk: same
        self.assertTrue(self.auto.enabled())

    def test_entering_talk_with_billing_off(self):
        self.call('/mode', {'input': 'backward'})
        self.call('/switches', {'name': 'billing', 'on': False})
        r = self.call('/mode', {'input': 'forward'})[1]
        self.assertEqual((r['snapshot']['mode'], r['result']['code'], self.worker.enabled(), self.auto.enabled()),
                         ('talk', 'ok', False, False))

    def test_say_round_only_in_talk(self):
        self.call('/mode', {'input': 'backward'})
        self.assertEqual(self.call('/say/round', {})[0], 409)
        self.assertEqual(self.call('/say/prefetch', {'enabled': True})[0], 409)
        self.assertEqual(self.call('/say/stop', {})[0], 200)

    def test_billing_change_bumps_seq(self):
        s0 = self.call('/mode/seq')[1]['seq']
        self.call('/switches', {'name': 'billing', 'on': False})
        self.assertEqual(self.call('/mode/seq')[1]['seq'], s0 + 1)
        self.assertFalse(self.call('/mode')[1]['snapshot']['billing'])

    def test_billing_off_blocks_paid_switches_and_speech(self):
        self.call('/switches', {'name': 'tingwu', 'on': True})
        self.call('/switches', {'name': 'billing', 'on': False})
        sw = self.call('/switches')[1]
        self.assertEqual((sw['billing']['on'], sw['tingwu']['on']), (False, False))
        self.assertEqual(self.call('/switches', {'name': 'eye', 'on': True})[0], 409)
        self.assertEqual(self.call('/switch', {'enabled': True})[0], 409)
        self.assertEqual(self.call('/voice/try', {'voice': 'v1'})[0], 409)

    def test_robot_forward_in_talk_starts_one_round_then_too_soon(self):
        body = {'device': 'robot', 'boot': 'b1', 'seq': 1, 'age_ms': 100, 'source': 'asr', 'input': 'forward'}
        self.assertEqual(self.call('/mode/input', body)[1]['result']['code'], 'ok')
        self.wait_rounds(1)
        self.assertEqual(self.say_calls[0]['trigger']['source'], 'asr')
        body['seq'] = 2
        self.assertEqual(self.call('/mode/input', body)[1]['result']['code'], 'too_soon')

    def test_tap_while_speaking_stops(self):
        self.speaking = True
        body = {'device': 'robot', 'boot': 'b1', 'seq': 3, 'age_ms': 0, 'source': 'touch', 'input': 'tap'}
        self.call('/mode/input', body)
        self.assertEqual((self.tts.stops >= 1, len(self.say_calls)), (True, 0))

    def test_preset_returns_robot_half_only_for_page(self):
        self.assertEqual(self.call('/preset', {'n': 1, 'source': 'page'})[1]['robot'], {'hearing': 'local', 'vision': 'upload'})

    def test_dualkey_direction_signal_switches_mode(self):
        self.call('/signal', {'kind': 'mode', 'value': {'direction': 'left'}, 'age_ms': 120,
                              'source': {'device': 'dualkey-aa', 'boot': 'x', 'seq': 3}})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'crowd')
        self.assertEqual(self.db['owner_signals'].docs[-1]['result']['code'], 'ok')

    def test_dualkey_stale_direction_ignored(self):
        self.call('/signal', {'kind': 'mode', 'value': {'direction': 'left'}, 'age_ms': 45_000,
                              'source': {'device': 'dualkey-aa', 'boot': 'x', 'seq': 4}})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')
        self.assertEqual(self.db['owner_signals'].docs[-1]['result']['code'], 'stale')

    def test_robot_boot_event(self):
        self.call('/mode/robot', {'boot': 'b1', 'conn': 1, 'event': 'hello'})
        self.call('/mode', {'input': 'play'})
        self.call('/mode/robot', {'boot': 'b2', 'conn': 2, 'event': 'hello'})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')

    def test_bad_input(self):
        self.assertEqual(self.call('/mode', {'input': 'fly'})[0], 400)
        self.assertEqual(self.call('/mode/input', {'device': 'robot', 'seq': 'x', 'input': 'play'})[0], 400)
        self.assertEqual(self.call('/preset', {'n': 10})[0], 400)


class CarriedTest(ModeApiBase):
    """Items carried into A4 from the A1-A3 reviews, one test each (or more)."""

    # 1. talk_enter only through Billing.talk_enter with is_current; the same is_current for say_round / prefetch_on
    def test_talk_enter_goes_through_billing_with_is_current(self):
        calls, real = [], self.billing.talk_enter

        def spy(now_ms, *, is_current):
            calls.append(is_current)
            return real(now_ms, is_current=is_current)
        self.billing.talk_enter = spy
        self.call('/mode', {'input': 'backward'}); self.call('/mode', {'input': 'forward'})
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]())
        self.call('/mode', {'input': 'backward'})
        self.assertFalse(calls[0]())                     # a later mode change: no longer current

    def test_guard_answers_none_for_talk_enter(self):
        meta = {'source': 'page', 'input': 'forward', 'value': None, 'device': None, 'sid': None, 'mode': 'talk'}
        self.assertIsNone(self.ms.guard('talk_enter', meta))
        self.call('/switches', {'name': 'billing', 'on': False})
        self.assertIsNone(self.ms.guard('talk_enter', meta))

    def test_say_round_gets_is_current_of_its_commit(self):
        self.call('/mode/input', {'device': 'robot', 'boot': 'b1', 'seq': 1, 'age_ms': 0, 'source': 'asr',
                                  'input': 'forward'})
        self.wait_rounds(1)
        self.assertTrue(self.say_calls[0]['current'])
        self.call('/mode', {'input': 'backward'})
        self.assertFalse(self.say_calls[0]['is_current']())

    def test_prefetch_on_needs_the_commit_to_be_current(self):
        hook = self.ms.on_change[0]
        snap = self.ms.current()
        meta = {'source': 'page', 'input': 'double_tap', 'value': None, 'device': None, 'sid': None, 'mode': 'talk'}
        hook(snap, dict(snap, seq=snap['seq'] - 1), ['prefetch_on'], meta, 1)       # superseded commit
        self.assertFalse(self.worker.enabled())
        hook(snap, snap, ['prefetch_on'], meta, 2)
        self.assertTrue(self.worker.enabled())

    def test_double_tap_in_talk_turns_prefetch_on(self):
        r = self.call('/mode', {'input': 'double_tap'})[1]
        self.assertEqual((r['result']['code'], r['effects'], self.worker.enabled()), ('ok', ['prefetch_on'], True))

    # 2. startup: billing off (also a corrupt stored value) runs billing.off again
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

    # 3. billing off refuses only turning paid switches ON
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

    # 4. replies surface effects and Billing.off's failed list
    def test_reply_has_effects(self):
        r = self.call('/mode', {'input': 'backward'})[1]
        self.assertEqual(r['effects'], ['stop_speech', 'talk_leave'])

    def test_partly_failed_billing_off_says_what_failed(self):
        real = self.billing.ctx.set_switch

        def broken(name, on):
            if name == 'notes':
                raise RuntimeError('db down')
            real(name, on)
        self.billing.ctx.set_switch = broken
        s, r = self.call('/switches', {'name': 'billing', 'on': False})
        self.assertEqual((s, r['failed'], r['billing']['on'], r['result']['toast']), (200, ['notes'], False, 'BILLING OFF, 1 FAILED'))

    # 5. /mode/input without the dedup key is refused
    def test_mode_input_needs_boot_and_seq(self):
        base = {'device': 'robot', 'boot': 'b1', 'seq': 1, 'age_ms': 0, 'source': 'asr', 'input': 'play'}
        for drop in ('device', 'boot', 'seq', 'age_ms', 'source', 'input'):
            body = {k: v for k, v in base.items() if k != drop}
            self.assertEqual(self.call('/mode/input', body)[0], 400, drop)
        self.assertEqual(self.call('/mode/input', dict(base, seq=True))[0], 400)
        self.assertEqual(self.call('/mode/input', dict(base, source='page'))[0], 400)
        self.assertEqual(self.call('/mode/input', dict(base, input='boot'))[0], 400)
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')
        self.assertEqual(self.call('/mode/input', base)[1]['snapshot']['mode'], 'chat')

    # 6. a robot preset 2 sent twice toggles 听悟 once, through HTTP
    def test_robot_preset_two_sent_twice_toggles_once(self):
        body = {'device': 'robot', 'boot': 'b1', 'seq': 7, 'age_ms': 0, 'source': 'asr', 'input': 'digit', 'value': 2}
        r1 = self.call('/mode/input', body)[1]
        r2 = self.call('/mode/input', body)[1]
        self.assertEqual((r1['result']['toast'], r2['result']['toast'], r2.get('duplicate')),
                         ('PRESET 2: TINGWU ON', 'PRESET 2: TINGWU ON', True))
        self.assertTrue(self.call('/switches')[1]['tingwu']['on'])
        self.call('/mode/input', dict(body, seq=8))
        self.assertFalse(self.call('/switches')[1]['tingwu']['on'])

    # 7. talk only: 说一轮 / say endpoints answer 409 only_talk elsewhere; in talk today's pipeline is untouched
    def test_say_endpoints_outside_talk_say_only_talk(self):
        self.call('/mode', {'input': 'play'})
        for path, body in (('/say', {'role': 'roast'}), ('/say/round', {}), ('/say/play', {'rid': 'r1', 'line': 'a'}),
                           ('/say/auto', {'enabled': True})):
            s, r = self.call(path, body)
            self.assertEqual((s, r['error'], r['code']), (409, '只在脱口秀模式', 'only_talk'), path)
        self.assertEqual(self.call('/say/prefetch', {'enabled': False})[0], 200)       # turning off always works
        self.assertEqual(self.call('/say/auto', {'enabled': False})[0], 200)

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

    # owner decisions
    def test_manual_pick_and_settings_return_count_as_talk_entries(self):
        self.call('/mode', {'input': 'open'})                      # settings from talk: leaves talk
        self.assertFalse(self.worker.enabled())
        self.call('/mode', {'input': 'close'})                     # back to talk = an entry
        self.assertEqual((self.worker.enabled(), self.auto.enabled()), (True, True))

    def test_family_already_off_writes_no_settings_version(self):
        v0 = self.ss.current()[1]
        self.call('/mode', {'input': 'backward'}); self.call('/mode', {'input': 'forward'})
        self.assertEqual(self.ss.current()[1], v0)
        self.settings_family(True)
        self.call('/mode', {'input': 'backward'}); self.call('/mode', {'input': 'forward'})
        self.assertEqual(self.ss.history()[0]['note'], '家人声音 关（进脱口秀）')

    def test_forward_needs_mouth_on_tts(self):
        self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': 'clips'}})
        r = self.call('/mode', {'input': 'forward'})[1]
        self.assertEqual((r['result']['code'], r['effects']), ('mouth_not_tts', []))
        self.assertEqual(self.say_calls, [])

    def test_forward_while_a_round_runs_is_busy(self):
        self.gate.running = True
        self.assertEqual(self.call('/mode', {'input': 'forward'})[1]['result']['code'], 'busy')

    def test_page_round_counts_for_too_soon(self):
        self.gate.note(int(time.time() * 1000))
        self.assertEqual(self.call('/mode', {'input': 'forward'})[1]['result']['code'], 'too_soon')

    def test_dualkey_click_in_talk(self):
        src = {'device': 'dualkey-aa', 'boot': 'x', 'seq': 10}
        self.speaking = True
        r = self.call('/signal', {'kind': 'click', 'value': {'type': 'single'}, 'age_ms': 0, 'source': src})[1]
        self.assertEqual((r['effects'], self.tts.stops >= 1, self.say_calls), (['stop_speech'], True, []))
        self.speaking = False
        self.call('/signal', {'kind': 'click', 'value': {'type': 'single'}, 'age_ms': 0, 'source': dict(src, seq=11)})
        self.wait_rounds(1)
        self.assertEqual(self.say_calls[0]['trigger']['source'], 'dualkey')
        self.call('/mode', {'input': 'backward'})
        self.call('/signal', {'kind': 'click', 'value': {'type': 'long'}, 'age_ms': 0, 'source': dict(src, seq=12)})
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')

    def test_dualkey_resend_applies_once(self):
        body = {'kind': 'mode', 'value': {'direction': 'left'}, 'age_ms': 0,
                'source': {'device': 'dualkey-aa', 'boot': 'x', 'seq': 5}}
        self.call('/signal', body)
        r = self.call('/signal', body)[1]
        self.assertEqual((r['result']['code'], self.call('/mode')[1]['snapshot']['mode']), ('ok', 'crowd'))
        self.assertEqual(self.db.count('owner_signals'), 1)

    def test_old_dualkey_rating_is_not_stale(self):
        self.call('/signal', {'kind': 'rating', 'value': {'polarity': 1, 'count': 2}, 'age_ms': 45_000,
                              'source': {'device': 'dualkey-aa', 'boot': 'x', 'seq': 6}})
        self.assertNotIn('result', self.db['owner_signals'].docs[-1])

    def test_robot_endpoint_validates(self):
        self.assertEqual(self.call('/mode/robot', {'boot': 'b1', 'conn': 'x', 'event': 'hello'})[0], 400)
        self.assertEqual(self.call('/mode/robot', {'boot': 'b1', 'conn': 1, 'event': 'bye'})[0], 400)
        s, r = self.call('/mode/robot', {'boot': 'b1', 'conn': 1, 'event': 'hello'})
        self.assertEqual((s, r['snapshot']['mode']), (200, 'talk'))
        self.call('/mode/robot', {'boot': 'b1', 'conn': 1, 'event': 'applied', 'applied_seq': r['snapshot']['seq']})
        robot = self.call('/mode')[1]['robot']
        self.assertEqual((robot['connected'], robot['applied_seq']), (True, r['snapshot']['seq']))

    def test_settings_timeout_tick(self):
        self.call('/mode', {'input': 'open'})
        self.ms.tick(int(time.time() * 1000) + 301_000)
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'talk')


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
        self.assertEqual(call('/mode', {'input': 'forward'})[0], 409)
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
