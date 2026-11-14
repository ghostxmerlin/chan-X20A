"""hello modes step 1: the mode store (server/mind/mode_store.py), plan task A2.

Persistence (hello.mode / hello.mode_log), device-input dedup, stale drop, routing of digit / billing, the guard
pre-check on paid effects, on_change hooks, billing bumping seq, robot connection ids, boot default, settings
timeout, and rejection of impossible stored states.
"""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from mind_fixtures import FakeDB
from mind.mode_store import STALE_MS, ModeStore, valid_state
from mind.modes import result


class ModeStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(); self.ms = ModeStore(self.db, clock_ms=lambda: 0)

    def test_starts_in_talk_and_persists(self):
        self.assertEqual((self.ms.current()['mode'], self.ms.current()['billing']), ('talk', True))
        self.ms.apply('play', None, source='page', now_ms=10)
        self.assertEqual(ModeStore(self.db).current()['mode'], 'chat')        # a mind restart keeps the mode and seq

    def test_device_input_dedup(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=5, now_ms=10)
        a = self.ms.apply('play', None, **kw)
        b = self.ms.apply('play', None, **kw)
        self.assertEqual(a['snapshot']['seq'], b['snapshot']['seq'])
        self.assertEqual(self.db.count('mode_log'), 1)

    def test_stale_input_dropped(self):
        r = self.ms.apply('play', None, source='dualkey', device='dualkey-aa', boot='x', dev_seq=6, age_ms=4000, now_ms=10)
        self.assertEqual((r['result']['code'], self.ms.current()['mode']), ('stale', 'talk'))

    def test_guard_decides_paid_effects(self):
        ms = ModeStore(FakeDB(), guard=lambda effect, meta: 'too_soon' if effect == 'say_round' else None)
        r = ms.apply('forward', None, source='asr', device='robot', boot='b', dev_seq=1, now_ms=1)
        self.assertEqual((r['result']['code'], r['effects']), ('too_soon', []))

    def test_billing_change_bumps_seq(self):
        s0 = self.ms.seq()
        snap = self.ms.set_billing(False, source='quick', now_ms=5)
        self.assertEqual((snap['seq'], snap['billing']), (s0 + 1, False))
        self.assertEqual(self.ms.set_billing(False, source='page', now_ms=6)['seq'], s0 + 1)   # no change, same seq

    def test_mind_runs_no_settings_timer(self):
        # step 1b: hello owns the settings timer and reports by:"timeout"; mind must never flip the mirror itself
        self.assertFalse(hasattr(self.ms, 'tick'))

    def test_change_hooks_get_effects_and_meta(self):
        seen = []; self.ms.on_change.append(lambda o, n, e, m, t: seen.append((o['mode'], n['mode'], e, m['source'])))
        self.ms.apply('backward', None, source='page', now_ms=1)
        self.assertEqual(seen, [('talk', 'idle', ['stop_speech', 'talk_leave'], 'page')])

    def test_seq_only_grows_on_change(self):
        s0 = self.ms.seq(); self.ms.apply('pause', None, source='page', now_ms=1)
        self.assertEqual(self.ms.seq(), s0)


class RoutingTest(unittest.TestCase):
    """digit -> preset_fn, billing -> billing_fn; both after dedup / stale / log, never into transition."""

    def setUp(self):
        self.db = FakeDB()
        self.calls = []

        def preset(n, *, source, now_ms, meta):
            self.calls.append((n, source, meta['device']))
            return {'result': result('ok', 'PRESET %d' % n), 'robot': {'hearing': 'local'}}

        self.ms = ModeStore(self.db, clock_ms=lambda: 0, preset_fn=preset)

    def test_robot_preset_sent_twice_applies_once(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=9, now_ms=10)
        a = self.ms.apply('digit', 3, **kw)
        b = self.ms.apply('digit', 3, **kw)
        self.assertEqual(self.calls, [(3, 'asr', 'robot')])
        self.assertEqual((a['result'], b['result']), (result('ok', 'PRESET 3'), result('ok', 'PRESET 3')))
        self.assertEqual(a['robot'], {'hearing': 'local'})
        self.assertTrue(b['duplicate'])
        self.assertEqual(self.db.count('mode_log'), 1)
        row = self.db['mode_log'].docs[0]
        self.assertEqual((row['input'], row['value'], row['device'], row['boot'], row['dev_seq'], row['code']),
                         ('digit', 3, 'robot', 'b1', 9, 'ok'))

    def test_dedup_survives_a_mind_restart(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=9, now_ms=10)
        self.ms.apply('digit', 3, **kw)
        again = ModeStore(self.db, preset_fn=lambda *a, **k: self.fail('preset ran twice'))
        self.assertEqual(again.apply('digit', 3, **kw)['result']['code'], 'ok')

    def test_same_seq_from_a_new_boot_is_a_new_input(self):
        self.ms.apply('digit', 3, source='asr', device='robot', boot='b1', dev_seq=9, now_ms=10)
        self.ms.apply('digit', 3, source='asr', device='robot', boot='b2', dev_seq=9, now_ms=11)
        self.assertEqual(len(self.calls), 2)

    def test_stale_preset_is_not_applied_but_logged(self):
        r = self.ms.apply('digit', 3, source='asr', device='robot', boot='b1', dev_seq=9, age_ms=3001, now_ms=10)
        self.assertEqual((r['result']['code'], self.calls), ('stale', []))
        self.assertEqual(self.db['mode_log'].docs[0]['code'], 'stale')
        self.assertEqual(self.ms.apply('digit', 3, source='asr', device='robot', boot='b1', dev_seq=10, age_ms=3000,
                                       now_ms=11)['result']['code'], 'ok')     # 3000 ms is still fresh

    def test_default_preset_fn_is_undefined(self):
        ms = ModeStore(FakeDB(), clock_ms=lambda: 0)
        self.assertEqual(ms.apply('digit', 4, source='page', now_ms=1)['result']['code'], 'undefined_preset')

    def test_bad_digit_value_is_unknown_input(self):
        for v in (None, 10, -1, '3', True, 2.0):
            self.assertEqual(self.ms.apply('digit', v, source='page', now_ms=1)['result']['code'], 'unknown_input')
        self.assertEqual(self.calls, [])

    def test_billing_input_goes_to_billing_fn(self):
        got = []
        self.ms.billing_fn = lambda on, *, source, now_ms, meta: got.append((on, source)) or \
            self.ms.set_billing(on, source=source, now_ms=now_ms)
        s0 = self.ms.seq()
        r = self.ms.apply('billing', False, source='quick', device='robot', boot='b1', dev_seq=1, now_ms=5)
        self.assertEqual((got, r['result']['code'], r['snapshot']['billing'], r['snapshot']['seq']),
                         ([(False, 'quick')], 'ok', False, s0 + 1))
        r = self.ms.apply('billing', False, source='quick', device='robot', boot='b1', dev_seq=2, now_ms=6)
        self.assertEqual((r['result']['code'], self.ms.seq()), ('already', s0 + 1))
        self.assertEqual(self.ms.apply('billing', 'off', source='page', now_ms=7)['result']['code'], 'unknown_input')
        self.assertEqual(len(got), 2)

    def test_default_billing_fn_sets_billing(self):
        ms = ModeStore(FakeDB(), clock_ms=lambda: 0)
        ms.apply('billing', False, source='page', now_ms=1)
        self.assertFalse(ms.billing())

    def test_routed_inputs_never_reach_transition(self):
        import mind.mode_store as m
        real = m.transition
        m.transition = lambda *a, **k: self.fail('transition called for %r' % (a[1],))
        try:
            self.ms.apply('digit', 1, source='page', now_ms=1)
            self.ms.apply('billing', True, source='page', now_ms=2)
        finally:
            m.transition = real

    def test_concurrent_duplicate_waits_for_the_first(self):
        gate, started = threading.Event(), threading.Event()
        count = []

        def slow(n, **k):
            count.append(n); started.set(); gate.wait(5)
            return {'result': result('ok')}

        self.ms.preset_fn = slow
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=1, now_ms=1)
        out = []
        t1 = threading.Thread(target=lambda: out.append(self.ms.apply('digit', 2, **kw)))
        t1.start(); started.wait(5)
        t2 = threading.Thread(target=lambda: out.append(self.ms.apply('digit', 2, **kw)))
        t2.start()
        gate.set(); t1.join(5); t2.join(5)
        self.assertEqual((count, [o['result']['code'] for o in out]), ([2], ['ok', 'ok']))

    def test_preset_fn_may_apply_modes_itself(self):
        def preset(n, *, source, now_ms, meta):
            self.ms.apply('select', {'mode': 'idle'}, source=source, now_ms=now_ms)
            self.ms.set_billing(False, source=source, now_ms=now_ms)
            return {'result': result('ok')}

        self.ms.preset_fn = preset
        r = self.ms.apply('digit', 0, source='asr', device='robot', boot='b1', dev_seq=1, now_ms=3)
        self.assertEqual((r['snapshot']['mode'], r['snapshot']['billing']), ('idle', False))
        row = self.db['mode_log'].find_one({'input': 'digit'})
        self.assertEqual((row['from'], row['to']), ('talk', 'idle'))


class GuardAndHooksTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()

    def test_guard_meta_and_mode_change_kept(self):
        metas = []
        ms = ModeStore(self.db, guard=lambda e, m: metas.append((e, m)) or 'busy')
        r = ms.apply('double_tap', None, source='dualkey', device='dk', boot='x', dev_seq=1, sid='s1', now_ms=1)
        self.assertEqual((r['result'], r['effects']), (result('busy'), []))
        self.assertEqual(metas, [('prefetch_on', {'source': 'dualkey', 'input': 'double_tap', 'value': None,
                                                  'device': 'dk', 'sid': 's1', 'mode': 'talk'})])
        ms.apply('backward', None, source='page', now_ms=2)              # not paid: the guard is not asked
        self.assertEqual(len(metas), 1)
        self.assertEqual(ms.current()['mode'], 'idle')

    def test_guard_none_keeps_effect(self):
        ms = ModeStore(self.db, guard=lambda e, m: None)
        r = ms.apply('forward', None, source='page', now_ms=1)
        self.assertEqual((r['result']['code'], r['effects']), ('ok', ['say_round']))

    def test_billing_off_blocks_paid_effects_without_a_guard(self):
        ms = ModeStore(self.db)
        ms.set_billing(False, source='page', now_ms=1)
        r = ms.apply('tap', None, source='touch', now_ms=2)
        self.assertEqual((r['result']['code'], r['effects']), ('billing_off', []))

    def test_speaking_fn_turns_tap_into_stop(self):
        ms = ModeStore(self.db, speaking_fn=lambda: True)
        self.assertEqual(ms.apply('tap', None, source='touch', now_ms=1)['effects'], ['stop_speech'])

    def test_hooks_run_for_effects_without_mode_change_and_not_for_duplicates(self):
        ms = ModeStore(self.db, clock_ms=lambda: 0)
        seen = []
        ms.on_change.append(lambda o, n, e, m, t: seen.append((e, t)))
        kw = dict(source='asr', device='robot', boot='b', dev_seq=1, now_ms=7)
        ms.apply('forward', None, **kw)
        ms.apply('forward', None, **kw)
        ms.apply('pause', None, source='page', now_ms=8)          # no effect, no change: no hook
        self.assertEqual(seen, [(['say_round'], 7)])

    def test_a_failing_hook_does_not_stop_the_others(self):
        ms = ModeStore(self.db)
        seen = []
        ms.on_change.append(lambda *a: 1 / 0)
        ms.on_change.append(lambda o, n, e, m, t: seen.append(n['mode']))
        r = ms.apply('play', None, source='page', now_ms=1)
        self.assertEqual((seen, r['result']['code']), (['chat'], 'ok'))

    def test_hooks_run_outside_the_lock(self):
        ms = ModeStore(self.db)
        seen = []

        def hook(o, n, e, m, t):
            box = []
            th = threading.Thread(target=lambda: box.append(ms.apply('pause', None, source='page', now_ms=t)))
            th.start(); th.join(2)
            seen.append(bool(box))

        ms.on_change.append(hook)
        ms.apply('play', None, source='page', now_ms=1)
        self.assertEqual(seen, [True, True])                      # play -> chat, and the nested pause -> idle


class PersistenceTest(unittest.TestCase):
    def test_boot_document(self):
        db = FakeDB(); ModeStore(db, clock_ms=lambda: 42)
        doc = db['mode'].find_one({'_id': 'current'})
        self.assertEqual({k: doc[k] for k in ('mode', 'dir', 'host', 'return_to', 'seq', 'billing', 'by', 'since')},
                         {'mode': 'talk', 'dir': None, 'host': None, 'return_to': None, 'seq': 1, 'billing': True,
                          'by': 'boot', 'since': 42})

    def test_all_four_state_fields_persist(self):
        db = FakeDB(); ms = ModeStore(db, clock_ms=lambda: 0)
        ms.apply('direction', 'down', source='dualkey', now_ms=1)
        ms.apply('open', None, source='page', now_ms=2)
        again = ModeStore(db).current()
        self.assertEqual((again['mode'], again['dir'], again['return_to'], again['seq']), ('settings', 'en_zh', 'subtitle', 3))
        self.assertEqual(ModeStore(db).apply('close', None, source='page', now_ms=3)['snapshot']['dir'], 'en_zh')

    def test_billing_persists(self):
        db = FakeDB(); ModeStore(db).set_billing(False, source='page', now_ms=1)
        self.assertEqual(ModeStore(db).billing(), False)

    def test_bad_stored_state_falls_back_to_boot_default(self):
        for bad in ({'mode': 'event', 'host': 'event'}, {'mode': 'settings', 'host': 'event', 'return_to': 'talk'},
                    {'mode': 'idle', 'host': 'event'}, {'mode': 'talk', 'return_to': 'chat'},
                    {'mode': 'settings', 'return_to': None}, {'mode': 'settings', 'return_to': 'settings'},
                    {'mode': 'subtitle', 'dir': None}, {'mode': 'chat', 'dir': 'zh_en'}, {'mode': 'nope'},
                    {'mode': 'talk', 'seq': 'x'}, {'mode': 'talk', 'billing': 'yes'}):
            db = FakeDB()
            doc = {'_id': 'current', 'mode': 'talk', 'dir': None, 'host': None, 'return_to': None, 'seq': 7,
                   'billing': False, 'since': 1, 'by': 'page', **bad}
            db['mode'].docs.append(dict(doc))
            ms = ModeStore(db, clock_ms=lambda: 50)
            snap = ms.current()
            self.assertEqual((snap['mode'], snap['host'], snap['return_to'], snap['by']), ('talk', None, None, 'boot'), bad)
            self.assertGreater(snap['seq'], 7 if isinstance(doc['seq'], int) else 0, bad)
            self.assertEqual(db['mode_log'].docs[-1]['code'], 'bad_state', bad)
            self.assertEqual(db['mode'].find_one({'_id': 'current'})['mode'], 'talk', bad)

    def test_corrupt_stored_billing_reads_off(self):
        # owner 15: a stored billing value that is not true / false counts as off; only a missing doc starts on
        for bad in ('yes', 1, None, 'missing'):
            db = FakeDB()
            doc = {'_id': 'current', 'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None, 'seq': 7,
                   'billing': bad, 'since': 1, 'by': 'page'}
            if bad == 'missing':
                doc.pop('billing')
            db['mode'].docs.append(doc)
            ms = ModeStore(db, clock_ms=lambda: 50)
            self.assertEqual((ms.billing(), ms.current()['billing']), (False, False), bad)
            self.assertGreater(ms.seq(), 7, bad)
            self.assertIs(db['mode'].find_one({'_id': 'current'})['billing'], False, bad)
        self.assertTrue(ModeStore(FakeDB(), clock_ms=lambda: 0).billing())

    def test_valid_state_rules(self):
        ok = lambda **kw: valid_state({'mode': 'talk', 'dir': None, 'host': None, 'return_to': None, **kw})
        self.assertTrue(ok())
        self.assertTrue(ok(mode='crowd', host='event'))
        self.assertTrue(ok(mode='settings', return_to='subtitle', dir='zh_en'))
        self.assertTrue(ok(mode='settings', return_to='idle'))
        self.assertFalse(ok(mode='settings', return_to='subtitle'))           # subtitle without its direction
        self.assertFalse(ok(mode='settings', return_to='event'))              # event-driven returns as idle
        self.assertFalse(ok(mode='event', host='event'))
        self.assertFalse(ok(return_to='chat'))
        self.assertFalse(ok(host='robot'))
        self.assertFalse(valid_state({'mode': 'talk'}))

    def test_impossible_state_is_never_written(self):
        db = FakeDB(); ms = ModeStore(db, clock_ms=lambda: 0)
        import mind.mode_store as m
        real = m.transition
        m.transition = lambda st, *a, **k: ({'mode': 'idle', 'dir': None, 'host': 'event', 'return_to': None},
                                            result('ok'), [])
        try:
            with self.assertRaises(ValueError):
                ms.apply('stop', None, source='page', now_ms=1)
        finally:
            m.transition = real
        self.assertEqual((ms.current()['mode'], db['mode'].find_one({'_id': 'current'})['mode']), ('talk', 'talk'))

    def test_log_rows_and_recent_log(self):
        db = FakeDB(); ms = ModeStore(db, clock_ms=lambda: 0)
        ms.apply('play', None, source='asr', device='robot', boot='b', dev_seq=4, sid=None, now_ms=10)
        ms.apply('pause', None, source='page', now_ms=20)
        ms.apply('pause', None, source='page', now_ms=30)
        row = db['mode_log'].docs[0]
        for k in ('at', 'source', 'device', 'input', 'value', 'from', 'to', 'code', 'seq', 'effects'):
            self.assertIn(k, row)
        self.assertEqual((row['from'], row['to'], row['code'], row['seq'], row['effects']),
                         ('talk', 'chat', 'ok', 2, ['stop_speech', 'talk_leave']))
        self.assertEqual([r['at'] for r in ms.log(limit=2)], [30, 20])
        self.assertEqual(ms.log()[0]['code'], 'not_in_mode')                    # pause while in idle
        self.assertNotIn('_id', ms.log()[0])

    def test_since_and_by_follow_mode_changes_not_billing(self):
        ms = ModeStore(FakeDB(), clock_ms=lambda: 0)
        ms.apply('play', None, source='asr', now_ms=10)
        ms.set_billing(False, source='page', now_ms=20)
        snap = ms.current()
        self.assertEqual((snap['since'], snap['by'], snap['billing']), (10, 'asr', False))

    def test_ensure_indexes(self):
        from mind.store import Store
        db = FakeDB(); Store(db).ensure_indexes()
        self.assertIn(([('at', 1)], False), db['mode_log'].indexes)
        self.assertIn(([('device', 1), ('boot', 1), ('dev_seq', 1)], False), db['mode_log'].indexes)

    def test_bad_source_raises(self):
        with self.assertRaises(ValueError):
            ModeStore(FakeDB()).apply('play', None, source='nobody', now_ms=1)


class RobotAndTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(); self.ms = ModeStore(self.db, clock_ms=lambda: 0)

    def test_boot_input_runs_talk_enter_even_in_talk(self):
        seen = []
        self.ms.on_change.append(lambda o, n, e, m, t: seen.append((e, m['source'])))
        s0 = self.ms.seq()
        self.ms.apply('boot', None, source='boot', now_ms=1)
        self.assertEqual((seen, self.ms.seq()), ([(['stop_speech', 'talk_enter'], 'boot')], s0))

    def test_talk_enter_with_billing_off_is_kept_for_the_free_part_and_the_entry_is_ok(self):
        # owner 14: entering talk with the master switch off is not an error; the effect still reaches the hook so
        # Billing.talk_enter can deselect 家人声音 (free) while it turns nothing paid on (review minor 1)
        self.ms.apply('backward', None, source='page', now_ms=1)
        self.ms.set_billing(False, source='page', now_ms=2)
        r = self.ms.apply('forward', None, source='asr', now_ms=3)
        self.assertEqual((r['snapshot']['mode'], r['result'], r['effects']),
                         ('talk', {'code': 'ok', 'toast': 'BILLING OFF'}, ['stop_speech', 'talk_enter']))

    def test_talk_enter_refused_by_the_guard_is_dropped_but_the_entry_is_ok(self):
        ms = ModeStore(FakeDB(), guard=lambda e, m: 'busy' if e == 'talk_enter' else None)
        ms.apply('backward', None, source='page', now_ms=1)
        r = ms.apply('forward', None, source='asr', now_ms=2)
        self.assertEqual((r['result'], r['effects']), ({'code': 'ok', 'toast': 'BUSY'}, ['stop_speech']))

    def test_talk_enter_is_asked_of_the_guard(self):
        asked = []
        ms = ModeStore(FakeDB(), guard=lambda e, m: asked.append(e) or None)
        ms.apply('backward', None, source='page', now_ms=1)
        self.assertEqual(ms.apply('forward', None, source='asr', now_ms=2)['effects'], ['stop_speech', 'talk_enter'])
        self.assertEqual(asked, ['talk_enter'])

    def test_talk_enter_dropped_when_billing_went_off_in_between(self):
        self.ms.apply('backward', None, source='page', now_ms=1)
        seen, fired = [], []

        def h1(o, n, e, m, t):
            if not fired:
                fired.append(1); self.ms.set_billing(False, source='page', now_ms=3)

        self.ms.on_change.extend([h1, lambda o, n, e, m, t: seen.append(list(e))])
        r = self.ms.apply('forward', None, source='asr', now_ms=2)
        self.assertEqual((seen, r['effects']), ([['stop_speech']], ['stop_speech']))


class ReviewFixesTest(unittest.TestCase):
    """A2 review: I1 superseded paid effects, I2 speaking_fn outside the lock, I3 robot view after a restart,
    M1 same-thread re-entry, M5 returned results are copies."""

    def setUp(self):
        self.db = FakeDB(); self.ms = ModeStore(self.db, clock_ms=lambda: 0)

    def _interleave(self, second):
        """forward commits say_round; the first hook commits `second` before the later hooks of forward run."""
        seen, fired = [], []

        def h1(o, n, e, m, t):
            if not fired:
                fired.append(1); second()

        self.ms.on_change.extend([h1, lambda o, n, e, m, t: seen.append((m['input'], list(e), n['seq']))])
        r = self.ms.apply('forward', None, source='asr', now_ms=1)
        return r, seen

    def test_say_round_dropped_when_a_later_input_left_talk(self):
        r, seen = self._interleave(lambda: self.ms.apply('backward', None, source='page', now_ms=2))
        self.assertEqual(seen, [('backward', ['stop_speech', 'talk_leave'], 2)])    # forward's say_round never runs
        self.assertEqual(r['effects'], [])

    def test_say_round_dropped_when_billing_went_off_in_between(self):
        r, seen = self._interleave(lambda: self.ms.set_billing(False, source='page', now_ms=2))
        self.assertEqual(seen, [])

    def test_unpaid_effects_still_run_when_superseded(self):
        seen, fired = [], []

        def h1(o, n, e, m, t):
            if not fired:
                fired.append(1); self.ms.set_billing(False, source='page', now_ms=2)

        self.ms.on_change.extend([h1, lambda o, n, e, m, t: seen.append(list(e))])
        self.ms.apply('backward', None, source='page', now_ms=1)
        self.assertEqual(seen, [['stop_speech', 'talk_leave']])

    def test_is_current_follows_the_committed_seq(self):
        checks = []
        self.ms.on_change.append(lambda o, n, e, m, t: checks.append(lambda: self.ms.seq() == n['seq']))
        self.ms.apply('forward', None, source='asr', now_ms=1)
        self.assertTrue(checks[0]())
        self.ms.apply('backward', None, source='page', now_ms=2)
        self.assertFalse(checks[0]())

    def test_slow_speaking_fn_does_not_block_other_inputs(self):
        gate, started = threading.Event(), threading.Event()
        calls = []

        def speaking():
            calls.append(1)
            if len(calls) == 1:
                started.set(); gate.wait(5)
            return False

        self.ms.speaking_fn = speaking
        t1 = threading.Thread(target=lambda: self.ms.apply('tap', None, source='touch', now_ms=1))
        t1.start(); started.wait(5)
        done = []
        t2 = threading.Thread(target=lambda: done.append((self.ms.set_billing(False, source='page', now_ms=2),
                                                          self.ms.apply('play', None, source='page', now_ms=3))))
        t2.start(); t2.join(2)
        finished = bool(done)
        gate.set(); t1.join(5)
        self.assertTrue(finished)

    def test_same_thread_reentry_with_the_same_key_raises(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=1, now_ms=1)
        self.ms.preset_fn = lambda n, **k: self.ms.apply('play', None, **kw)
        with self.assertRaises(ValueError):
            self.ms.apply('digit', 2, **kw)

    def test_returned_results_are_copies(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=1, now_ms=1)
        a = self.ms.apply('play', None, **kw)
        a['result']['extra'] = 1
        self.assertNotIn('extra', self.ms.apply('play', None, **kw)['result'])


class ReportTest(unittest.TestCase):
    """Step 1b: hello owns the mode; report() mirrors it and runs its effects through the pre-check and the hooks."""
    def setUp(self):
        self.db = FakeDB()
        self.seen = []
        self.guard = None
        self.ms = ModeStore(self.db, clock_ms=lambda: 0, guard=lambda e, m: self.guard(e, m) if self.guard else None)
        self.ms.on_change.append(lambda o, n, e, m, t: self.seen.append((o['mode'], n['mode'], e, m['source'])))

    def rep(self, seq, mode, effects=(), by='asr', inp='forward', boot='b1', conn=1, age_ms=0, **st):
        body = {'seq': seq, 'mode': mode, 'dir': st.get('dir'), 'host': st.get('host'),
                'return_to': st.get('return_to'), 'by': by, 'input': inp, 'effects': list(effects)}
        return self.ms.report(body, boot=boot, conn=conn, age_ms=age_ms, now_ms=10 + seq)

    def test_mirrors_the_reported_state_and_runs_its_effects(self):
        s0 = self.ms.seq()
        r = self.rep(1, 'crowd', ['stop_speech', 'talk_leave'], by='touch', inp='select')
        self.assertEqual((r['snapshot']['mode'], r['snapshot']['by'], r['snapshot']['seq'], r['effects']),
                         ('crowd', 'touch', s0 + 1, ['stop_speech', 'talk_leave']))
        self.assertEqual(self.seen, [('talk', 'crowd', ['stop_speech', 'talk_leave'], 'touch')])
        self.assertEqual(self.ms.robot(), {'connected': True, 'boot': 'b1', 'conn': 1, 'seq': 1, 'at': 11})
        row = self.db['mode_log'].docs[-1]
        self.assertEqual((row['device'], row['dev_seq'], row['input'], row['from'], row['to']),
                         ('robot.report', 1, 'select', 'talk', 'crowd'))
        self.assertEqual(ModeStore(self.db).current()['mode'], 'crowd')          # kept across a mind restart

    def test_same_seq_runs_once(self):
        self.rep(1, 'talk', ['say_round'])
        again = self.rep(1, 'talk', ['say_round'])
        self.assertEqual((again.get('duplicate'), len(self.seen)), (True, 1))
        self.rep(2, 'talk', ['say_round'])
        self.assertEqual(len(self.seen), 2)

    def test_report_seq_is_apart_from_mode_input_seq(self):
        self.ms.apply('digit', 5, source='asr', device='robot', boot='b1', dev_seq=3, now_ms=1)
        self.assertNotIn('duplicate', self.rep(3, 'idle', ['stop_speech', 'talk_leave'], inp='backward'))
        self.assertEqual(self.ms.current()['mode'], 'idle')

    def test_new_boot_restarts_the_seq(self):
        self.rep(5, 'crowd', inp='select')
        r = self.rep(0, 'talk', ['stop_speech', 'talk_enter'], by='boot', inp=None, boot='b2', conn=2)
        self.assertEqual((r['result']['code'], r['snapshot']['mode'], r['snapshot']['by']), ('ok', 'talk', 'boot'))
        self.assertEqual(self.db['mode_log'].docs[-1]['input'], 'report')

    def test_older_report_of_the_same_boot_changes_nothing(self):
        self.rep(5, 'crowd', inp='select')
        r = self.rep(4, 'idle', ['stop_speech'], inp='backward')
        self.assertEqual((r['result']['code'], self.ms.current()['mode'], len(self.seen)), ('stale', 'crowd', 1))

    def test_paid_effects_pass_the_billing_and_the_guard(self):
        self.ms.set_billing(False, source='page', now_ms=1)
        r = self.rep(1, 'talk', ['say_round'])
        self.assertEqual((r['result']['code'], r['effects'], self.seen), ('billing_off', [], []))
        self.ms.set_billing(True, source='page', now_ms=2)
        self.guard = lambda e, m: 'too_soon' if e == 'say_round' else None
        r = self.rep(2, 'talk', ['say_round'])
        self.assertEqual((r['result']['code'], r['effects']), ('too_soon', []))

    def test_talk_entry_with_billing_off_keeps_talk_enter_for_the_free_part(self):
        self.rep(1, 'idle', ['stop_speech', 'talk_leave'], inp='backward')
        self.ms.set_billing(False, source='page', now_ms=2)
        r = self.rep(2, 'talk', ['stop_speech', 'talk_enter'])
        self.assertEqual((r['result'], r['effects']), ({'code': 'ok', 'toast': 'BILLING OFF'},
                                                       ['stop_speech', 'talk_enter']))

    def test_a_late_report_keeps_only_the_turn_offs(self):
        r = self.rep(1, 'crowd', ['stop_speech', 'talk_leave'], inp='select', age_ms=STALE_MS + 1)
        self.assertEqual((r['snapshot']['mode'], r['effects']), ('crowd', ['talk_leave']))
        r = self.rep(2, 'talk', ['stop_speech', 'talk_enter'], inp='select', age_ms=STALE_MS + 1)
        self.assertEqual((r['snapshot']['mode'], r['effects']), ('talk', []))

    def test_bad_reports_are_refused(self):
        good = {'seq': 1, 'mode': 'talk', 'dir': None, 'host': None, 'return_to': None, 'by': 'asr', 'input': 'x',
                'effects': []}
        for bad in ({**good, 'mode': 'fly'}, {**good, 'mode': 'settings'}, {**good, 'mode': 'subtitle'},
                    {**good, 'seq': 'x'}, {**good, 'seq': True}, {**good, 'by': 'robot'}, {**good, 'effects': ['fly']},
                    {**good, 'effects': 'stop_speech'}, {**good, 'input': ''}, 'nope'):
            with self.assertRaises(ValueError, msg=bad):
                self.ms.report(bad, boot='b1', now_ms=1)
        with self.assertRaises(ValueError):
            self.ms.report(good, boot='', now_ms=1)
        self.assertEqual((self.ms.current()['mode'], self.db.count('mode_log')), ('talk', 0))

    def test_subtitle_and_settings_states(self):
        self.rep(1, 'subtitle', inp='direction', dir='en_zh')
        self.rep(2, 'settings', inp='open', dir='en_zh', return_to='subtitle')
        cur = self.ms.current()
        self.assertEqual((cur['mode'], cur['dir'], cur['return_to'], cur['look']['label']), ('settings', 'en_zh', 'subtitle', 'SET'))

    def test_robot_closed_only_for_the_latest_link(self):
        self.rep(1, 'talk', conn=4)
        self.ms.robot_closed('b1', 3, now_ms=50)               # an older link closing late
        self.assertTrue(self.ms.robot()['connected'])
        self.ms.robot_closed('b1', 4, now_ms=51)
        self.assertEqual((self.ms.robot()['connected'], self.ms.current()['mode']), (False, 'talk'))

    def test_closed_before_any_report_is_ignored(self):
        self.ms.robot_closed('b1', 7, now_ms=1)
        self.assertEqual(self.ms.robot()['conn'], None)



class RelinkTest(unittest.TestCase):
    """After a /mouth reconnect hello re-sends its current state: input null, by = the last change's source, no effects."""
    def test_relink_report_is_a_state_refresh(self):
        db = FakeDB(); ms = ModeStore(db, clock_ms=lambda: 0); seen = []
        ms.on_change.append(lambda o, n, e, m, t: seen.append(e))
        body = {'seq': 1, 'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None, 'by': 'touch',
                'input': 'select', 'effects': ['stop_speech', 'talk_leave']}
        ms.report(body, boot='b1', conn=1, now_ms=1)
        ms.report({**body, 'seq': 2, 'input': None, 'effects': []}, boot='b1', conn=2, now_ms=2)    # re-link, new seq
        dup = ms.report({**body, 'seq': 2, 'input': None, 'effects': []}, boot='b1', conn=3, now_ms=3)  # same seq again
        self.assertEqual((seen, ms.current()['mode'], ms.current()['by'], ms.robot()['conn'], dup.get('duplicate')),
                         ([['stop_speech', 'talk_leave']], 'crowd', 'touch', 2, True))
        self.assertEqual(db['mode_log'].docs[-1]['input'], 'report')


if __name__ == '__main__':
    unittest.main()
