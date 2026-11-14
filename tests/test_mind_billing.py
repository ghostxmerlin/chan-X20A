"""hello modes step 1: the billing master switch and the 0-9 presets, server half (server/mind/billing.py), task A3.

FakeCtx records every switch; it holds a real ModeStore (on FakeDB) and the real 提前写 / 连续说 workers
(say_prefetch.Worker, say_auto.Worker), so "连续说 on turns 提前写 on" and "提前写 off turns 连续说 off" behave
as they do live. ctx.mode() is `mode_value`, so a test can put hello outside talk without driving the store.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from mind_fixtures import FakeDB
from mind import mode_table, say_auto, say_prefetch
from mind.billing import Billing, apply_preset
from mind.mode_store import ModeStore


class _StateStore:
    """The two workers only call store.set_state."""
    def __init__(self):
        self.state = {}

    def set_state(self, **kw):
        self.state.update(kw)


class FakeCtx:
    def __init__(self, *, tingwu=False, eye=False, notes=False, mind=False, prefetch=False, auto=False, mode='talk',
                 mouth='clips', family=False):
        self.mode_store = ModeStore(FakeDB(), clock_ms=lambda: 0)
        st = _StateStore()
        self.say_worker = say_prefetch.Worker(st, settings_fn=dict, mouth_fn=lambda: 'tts', ctx_fn=None, write_fn=None)
        self.say_auto = say_auto.Worker(st, prefetch=self.say_worker, settings_fn=dict, mouth_fn=lambda: 'tts',
                                        play_fn=None)
        if prefetch:
            self.say_worker.set_enabled(True, 0)
        if auto:
            self.say_auto.set_enabled(True, 0)
        self.tingwu, self.eye, self.notes, self.mind = tingwu, eye, notes, mind
        self.mouth, self.family, self.mode_value, self.cancelled = mouth, family, mode, False
        self.calls = []

    # what Billing calls
    def set_switch(self, name, on):
        assert name in ('tingwu', 'eye', 'notes'), name
        self.calls.append((name, on))
        setattr(self, name, on)

    def switch_on(self, name):
        return getattr(self, name)

    def set_mind(self, on):
        self.calls.append(('mind', on))
        self.mind = on

    def set_mouth(self, mode):
        self.calls.append(('mouth', mode))
        self.mouth = mode

    def set_family(self, on):
        self.calls.append(('family', on))
        self.family = on

    def family_on(self):
        return self.family

    def cancel(self):
        self.calls.append(('cancel',))
        self.cancelled = True

    def mode(self):
        return self.mode_value

    # for the assertions
    @property
    def prefetch(self):
        return self.say_worker.enabled()

    @property
    def auto(self):
        return self.say_auto.enabled()

    def state(self):
        return dict(tingwu=self.tingwu, eye=self.eye, notes=self.notes, mind=self.mind, prefetch=self.prefetch,
                    auto=self.auto, cancelled=self.cancelled)


class BillingTest(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx(tingwu=True, eye=True, notes=True, mind=True, prefetch=True, auto=True, mode='talk')
        self.b = Billing(self.ctx)

    def test_off_turns_every_paid_switch_off_and_bumps_seq(self):
        s0 = self.ctx.mode_store.seq()
        self.b.off('quick', 1)
        self.assertEqual(self.ctx.state(), dict(tingwu=False, eye=False, notes=False, mind=False,
                                                prefetch=False, auto=False, cancelled=True))
        self.assertEqual((self.ctx.mode_store.seq(), self.b.allowed()), (s0 + 1, False))

    def test_on_restores_nothing(self):
        self.b.off('page', 1); self.b.on('page', 2)
        self.assertEqual((self.b.allowed(), self.ctx.tingwu), (True, False))

    def test_preset_one_from_robot_says_use_page(self):
        r = apply_preset(1, self.b, source='asr', now_ms=1)
        self.assertEqual((r['result']['code'], r['robot']), ('senses_on_page', {'hearing': 'local', 'vision': 'upload'}))
        self.assertFalse(self.b.allowed())

    def test_toggle_tingwu_needs_billing(self):
        self.b.off('page', 1)
        self.assertEqual(apply_preset(2, self.b, source='asr', now_ms=2)['result']['code'], 'billing_off')

    def test_toggle_prefetch_only_in_talk(self):
        self.ctx.mode_value = 'idle'
        self.assertEqual(apply_preset(3, self.b, source='asr', now_ms=1)['result']['code'], 'only_talk')

    def test_nine_is_everything_paid(self):
        self.b.off('page', 1)
        apply_preset(9, self.b, source='page', now_ms=2)
        self.assertEqual((self.b.allowed(), self.ctx.tingwu, self.ctx.eye, self.ctx.notes, self.ctx.mind,
                          self.ctx.mouth, self.ctx.prefetch, self.ctx.auto), (True, True, True, True, True, 'tts', True, True))

    def test_nine_outside_talk_skips_talk_behaviors(self):
        self.ctx.mode_value = 'idle'
        self.ctx.say_worker.set_enabled(False, 0)     # fixture: outside talk they are off (talk_leave did that)
        r = apply_preset(9, self.b, source='page', now_ms=1)
        self.assertEqual((self.ctx.prefetch, 'TALK' in r['result']['toast']), (False, True))

    def test_undefined(self):
        self.assertEqual(apply_preset(6, self.b, source='page', now_ms=1)['result']['code'], 'undefined_preset')

    def test_zero_in_talk_stays_off_until_next_entry(self):
        apply_preset(0, self.b, source='asr', now_ms=1); self.b.on('page', 2)
        self.assertEqual((self.ctx.prefetch, self.ctx.auto), (False, False))      # billing on restores nothing


class PresetDetailTest(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx(mode='talk')
        self.b = Billing(self.ctx)
        self.ms = self.ctx.mode_store

    def test_table_matches_spec_section_7(self):
        P = mode_table.PRESETS
        robot = {'hearing': 'local', 'vision': 'upload'}
        self.assertEqual(set(P), set(range(10)))
        self.assertEqual({n: (P[n]['server'], P[n]['robot']) for n in P},
                         {0: (('billing_off',), {}), 1: (('billing_off',), robot), 2: (('toggle_tingwu',), {}),
                          3: (('toggle_prefetch',), {}), 4: (('mode_idle',), {}), 5: ((), {}), 6: ((), {}),
                          7: ((), {}), 8: ((), {}),
                          9: (('billing_on', 'paid_senses_on', 'mouth_tts', 'talk_on'), robot)})
        self.assertTrue(all(isinstance(P[n]['name'], str) and P[n]['name'] for n in P))

    def test_table_validation_rejects_bad_rows(self):
        import copy
        for brk in (lambda t: t[2].update(server=('fly',)), lambda t: t[1].update(robot={'hearing': 'tingwu'}),
                    lambda t: t.pop(5), lambda t: t[0].update(robot={'smell': 'on'})):
            t = copy.deepcopy(mode_table.PRESETS)
            brk(t)
            with self.assertRaises(ValueError):
                mode_table._validate_presets(t)

    def test_page_preset_one_is_ok_and_hands_back_the_robot_half(self):
        self.ctx.tingwu = True
        r = apply_preset(1, self.b, source='page', now_ms=1)
        self.assertEqual((r['result']['code'], r['robot'], self.ctx.tingwu, self.b.allowed()),
                         ('ok', {'hearing': 'local', 'vision': 'upload'}, False, False))

    def test_presets_without_a_robot_half_hand_back_an_empty_one(self):
        self.assertEqual(apply_preset(0, self.b, source='asr', now_ms=1), {'result': {'code': 'ok', 'toast': 'PRESET 0'},
                                                                         'robot': {}})

    def test_robot_preset_nine_says_use_page_and_still_does_the_server_half(self):
        self.b.off('page', 1)
        r = apply_preset(9, self.b, source='quick', now_ms=2)
        self.assertEqual((r['result']['code'], self.b.allowed(), self.ctx.tingwu, self.ctx.prefetch),
                         ('senses_on_page', True, True, True))

    def test_toggle_tingwu_both_ways(self):
        r = apply_preset(2, self.b, source='asr', now_ms=1)
        self.assertEqual((r['result'], self.ctx.tingwu), ({'code': 'ok', 'toast': 'PRESET 2: TINGWU ON'}, True))
        r = apply_preset(2, self.b, source='asr', now_ms=2)
        self.assertEqual((r['result']['toast'], self.ctx.tingwu), ('PRESET 2: TINGWU OFF', False))

    def test_toggle_tingwu_off_still_works_with_billing_off(self):
        self.b.off('page', 1)
        self.ctx.tingwu = True                       # left on somehow: turning it off never needs the switch
        self.assertEqual((apply_preset(2, self.b, source='asr', now_ms=2)['result']['code'], self.ctx.tingwu), ('ok', False))

    def test_toggle_prefetch_in_talk(self):
        self.assertEqual(apply_preset(3, self.b, source='asr', now_ms=1)['result']['toast'], 'PRESET 3: PREFETCH ON')
        self.assertTrue(self.ctx.prefetch)
        self.ctx.say_auto.set_enabled(True, 2)
        self.assertEqual(apply_preset(3, self.b, source='asr', now_ms=3)['result']['toast'], 'PRESET 3: PREFETCH OFF')
        self.assertEqual((self.ctx.prefetch, self.ctx.auto), (False, False))        # 连续说 follows 提前写 off

    def test_toggle_prefetch_on_needs_billing(self):
        self.b.off('page', 1)
        self.assertEqual((apply_preset(3, self.b, source='asr', now_ms=2)['result']['code'], self.ctx.prefetch),
                         ('billing_off', False))

    def test_four_goes_to_idle_through_the_store(self):
        self.ctx.say_worker.set_enabled(True, 0)
        r = apply_preset(4, self.b, source='asr', now_ms=1)
        self.assertEqual((r['result']['code'], self.ms.current()['mode'], self.ms.current()['by']), ('ok', 'idle', 'asr'))
        self.assertEqual(apply_preset(4, self.b, source='asr', now_ms=2)['result']['code'], 'already')

    def test_every_preset_result_is_known_and_short(self):
        from mind.modes import CODES
        for billing in (True, False):
            for mode in ('talk', 'idle'):
                for source in ('page', 'asr'):
                    for n in range(10):
                        ctx = FakeCtx(mode=mode); b = Billing(ctx)
                        if not billing:
                            b.off('page', 0)
                        r = apply_preset(n, b, source=source, now_ms=1)
                        self.assertIn(r['result']['code'], CODES)
                        t = r['result']['toast']
                        self.assertTrue(t.isascii() and 0 < len(t) <= 32, (n, mode, source, t))

    def test_toasts_name_the_preset(self):
        self.assertEqual(apply_preset(9, self.b, source='page', now_ms=1)['result'], {'code': 'ok', 'toast': 'PRESET 9'})
        self.assertEqual(apply_preset(7, self.b, source='page', now_ms=1)['result']['toast'], 'PRESET UNDEFINED')


class StoreWiringTest(unittest.TestCase):
    """Billing installs itself as the store's preset_fn / billing_fn: device inputs go through dedup and the log."""
    def setUp(self):
        self.ctx = FakeCtx(tingwu=True, eye=True, notes=True, mind=True, prefetch=True, auto=True, mode='talk')
        self.b = Billing(self.ctx)
        self.ms = self.ctx.mode_store

    def test_robot_preset_two_resent_toggles_once(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=7, now_ms=5)
        r1 = self.ms.apply('digit', 2, **kw)
        r2 = self.ms.apply('digit', 2, **kw)
        self.assertEqual((r1['result']['code'], r2.get('duplicate'), r2['result'], self.ctx.tingwu),
                         ('ok', True, r1['result'], False))
        self.ms.apply('digit', 2, **dict(kw, dev_seq=8))                    # a new input does toggle back
        self.assertTrue(self.ctx.tingwu)

    def test_robot_preset_three_resent_toggles_once(self):
        kw = dict(source='asr', device='robot', boot='b1', dev_seq=9, now_ms=5)
        self.ms.apply('digit', 3, **kw); self.ms.apply('digit', 3, **kw)
        self.assertFalse(self.ctx.prefetch)

    def test_preset_result_and_robot_half_come_back_through_apply(self):
        r = self.ms.apply('digit', 1, source='page', now_ms=1)
        self.assertEqual((r['result']['code'], r['robot'], r['snapshot']['billing']),
                         ('ok', {'hearing': 'local', 'vision': 'upload'}, False))

    def test_billing_input_runs_the_off_actions(self):
        s0 = self.ms.seq()
        r = self.ms.apply('billing', False, source='quick', device='robot', boot='b1', dev_seq=1, now_ms=2)
        self.assertEqual((r['result']['code'], r['snapshot']['billing'], r['snapshot']['seq']), ('ok', False, s0 + 1))
        self.assertEqual(self.ctx.state(), dict(tingwu=False, eye=False, notes=False, mind=False,
                                                prefetch=False, auto=False, cancelled=True))
        self.assertEqual(self.ms.apply('billing', False, source='page', now_ms=3)['result']['code'], 'already')
        r = self.ms.apply('billing', True, source='page', now_ms=4)
        self.assertEqual((r['result']['code'], r['snapshot']['billing'], self.ctx.tingwu), ('ok', True, False))

    def test_billing_off_comes_before_the_switches(self):
        order = []
        self.ctx.set_switch = lambda name, on: order.append((name, self.ms.billing()))
        self.b.off('page', 1)
        self.assertTrue(order and all(billing is False for _, billing in order))


class TalkEnterTest(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx(mode='talk', family=True)
        self.b = Billing(self.ctx)

    def test_talk_enter_turns_writing_and_auto_on_and_family_off(self):
        self.assertTrue(self.b.talk_enter(1))
        self.assertEqual((self.ctx.prefetch, self.ctx.auto, self.ctx.family), (True, True, False))

    def test_talk_enter_leaves_family_alone_when_already_off(self):
        self.ctx.family = False
        self.b.talk_enter(1)
        self.assertNotIn(('family', False), self.ctx.calls)

    def test_talk_enter_with_billing_off_does_nothing(self):
        self.b.off('page', 1)
        self.ctx.calls.clear()
        self.assertFalse(self.b.talk_enter(2))
        self.assertEqual((self.ctx.prefetch, self.ctx.auto, self.ctx.calls), (False, False, []))

    def test_talk_enter_superseded_before_it_starts_does_nothing(self):
        self.assertFalse(self.b.talk_enter(1, is_current=lambda: False))
        self.assertEqual((self.ctx.prefetch, self.ctx.auto, self.ctx.family), (False, False, True))

    def test_talk_enter_superseded_while_running_undoes_itself(self):
        answers = iter((True, False))                   # current when it starts, replaced by the time it is done
        self.assertFalse(self.b.talk_enter(1, is_current=lambda: next(answers)))
        self.assertEqual((self.ctx.prefetch, self.ctx.auto), (False, False))

    def test_talk_enter_outside_talk_does_nothing(self):
        self.ctx.mode_value = 'idle'
        self.assertFalse(self.b.talk_enter(1))
        self.assertEqual(self.ctx.prefetch, False)


if __name__ == '__main__':
    unittest.main()
