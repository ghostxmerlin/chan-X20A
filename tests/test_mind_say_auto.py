"""连续说 (owner 2026-11-14): every 2–3 minutes play the pre-written lines the judge scored high.

speak.auto_play plays only existing stock (one check call at most, never a new write); say_auto.Worker owns
the switch, the random wake interval and the state shown on the page.
"""
import random
import threading
import unittest
from unittest.mock import patch

from test_mind_speech_v2 import SpeechCase, ENV, ROOT
from test_mind_speak import FakeTts
from test_mind_say_round import RoutedChat, NoShuffle
from mind import say_auto, say_prefetch, say_round, speak, speech_context, speech_settings


class EventTts(FakeTts):
    """FakeTts that logs say/status in order with the sleeps, so gaps can be checked."""
    def __init__(self, log, **kw):
        super().__init__(**kw)
        self.log = log

    def say(self, text, **kw):
        self.log.append(('say', text))
        return super().say(text, **kw)

    def status(self, uid, wait=0):
        self.log.append(('done', uid))
        return super().status(uid, wait)


class AutoCase(SpeechCase):
    def setUp(self):
        super().setUp()
        self.s['speech']['pipeline'] = 'round'
        self.roles = speak.load_roles(ROOT / 'config/roles')
        self.log, self.chat = [], RoutedChat()
        self.tts = EventTts(self.log)
        self.prefetch = say_prefetch.Worker(self.store, settings_fn=lambda: self.s, mouth_fn=lambda: 'tts',
                                            ctx_fn=lambda s, now: speech_context.build(self.db, self.store, s, now),
                                            write_fn=lambda ctx, now: None)
        self.on_sleep = None

    def sleep(self, secs):
        self.log.append(('sleep', secs))
        if self.on_sleep:
            self.on_sleep()

    def stock(self, at, scores=(3, 9, 5, 1, 7)):
        ctx = speech_context.build(self.db, self.store, self.s, at)
        r = say_round.write_round(ENV, self.store, self.s, self.roles, ctx, at, chat=RoutedChat(scores=scores), rng=NoShuffle())
        self.prefetch.put(ctx['scope']['id'], r)
        return r

    def auto(self, now=None, chat=None):
        with patch.object(speak, 'CHAT', chat or self.chat):
            return speak.auto_play(ENV, self.store, self.s, self.tts, now or self.now, worker=self.prefetch,
                                   sleep=self.sleep, clock=lambda: (now or self.now) / 1000)

    def text(self, r, key):
        return say_round.line(r, key)['text']

    def said(self):
        return [x[1] for x in self.log if x[0] == 'say']


class AutoPlayTest(AutoCase):
    def test_only_high_score_lines_play(self):
        self.heard(age=8000)
        r = self.stock(self.now - 2000)                    # comfort 3, deadpan 9, roast 5, sad 1, worst 7
        res = self.auto()
        self.assertEqual((res['reason'], res['played']), ('played', ['deadpan']))
        self.assertEqual(self.said(), [self.text(r, 'deadpan')])
        self.assertEqual(self.chat.calls, [])
        u = self.db['utterances'].find_one({'line': 'deadpan'})
        self.assertEqual((u['why'], u['rid'], u['pipeline']), ('auto', r['rid'], 'round'))
        row = self.db['say_rounds'].find_one({'rid': r['rid']})
        self.assertEqual([(t['why'], t['source']) for t in row['triggers']], [('auto', 'auto')])
        self.assertEqual([(p['key'], p['why']) for p in row['plays']], [('deadpan', 'auto')])

    def test_several_high_lines_in_judge_order_10s_apart(self):
        self.heard(age=8000)
        r = self.stock(self.now - 2000, scores=(8, 9, 5, 1, 8))
        res = self.auto()
        self.assertEqual(res['played'], ['deadpan', 'comfort', 'worst'])
        t = {k: self.text(r, k) for k in res['played']}
        self.assertEqual(self.log, [('say', t['deadpan']), ('done', 'u1'), ('sleep', 10),
                                    ('say', t['comfort']), ('done', 'u2'), ('sleep', 10),
                                    ('say', t['worst']), ('done', 'u3')])

    def test_min_score_setting(self):
        self.s['speech']['round']['auto_min_score'] = 7
        self.heard(age=8000)
        self.stock(self.now - 2000)
        self.assertEqual(self.auto()['played'], ['deadpan', 'worst'])

    def test_nothing_to_play_costs_nothing(self):
        cases = {}
        self.heard(age=8000)
        cases['none'] = self.auto()['reason']
        self.stock(self.now - speak.STOCK_TTL_MS - 1000)
        cases['stale'] = self.auto()['reason']
        r = self.stock(self.now - 2000)
        r['triggers'] = [{'at': self.now - 1000, 'why': 'stock', 'source': 'page'}]
        cases['triggered'] = self.auto()['reason']
        r = self.stock(self.now - 2000)
        r['plays'] = [{'key': 'deadpan', 'at': self.now - 1000, 'why': 'page'}]
        cases['played'] = self.auto()['reason']
        self.stock(self.now - 2000)
        self.s['speech']['round']['family'] = True
        cases['family'] = self.auto()['reason']
        self.s['speech']['round']['family'] = False
        self.stock(self.now - 2000, scores=(3, 7, 5, 1, 7))
        cases['low'] = self.auto()['reason']
        self.assertEqual(cases, {'none': 'no_stock', 'stale': 'no_stock', 'triggered': 'no_stock', 'played': 'no_stock',
                                 'family': 'no_stock', 'low': 'no_high_score'})
        self.assertEqual((self.said(), self.chat.calls), ([], []))

    def test_quiet_room_is_thin(self):
        self.stock(self.now - 2000)                       # nobody spoke in the last minute
        self.assertEqual(self.auto()['reason'], 'thin')
        self.assertEqual((self.said(), self.chat.calls), ([], []))

    def test_family_round_in_family_mode_plays(self):
        self.s['speech']['round']['family'] = True
        self.stock(self.now - 2000)                       # quiet room, but family mode is never thin
        res = self.auto()
        self.assertEqual(res['played'], ['deadpan'])
        self.assertIn(self.tts.said[0][1]['voice'], say_round.FAMILY_VOICES)

    def test_newer_speech_checks_once_and_plays_from_the_pick(self):
        self.heard(id=1, age=20000)
        r = self.stock(self.now - 15000, scores=(8, 9, 5, 1, 8))
        self.heard(id=2, age=3000, text='客户又说下周交付')
        chk = RoutedChat(check_pick=2)                     # of [deadpan, comfort, worst]
        res = self.auto(chat=chk)
        self.assertEqual([c[0] for c in chk.calls], ['check'])
        self.assertEqual(res['played'], ['comfort', 'worst'])
        checked = chk.bodies[0]['messages'][-1]['content']
        self.assertNotIn(self.text(r, 'roast'), checked)  # only the high-score lines are offered

    def test_check_none_plays_nothing_and_is_not_rechecked(self):
        self.heard(id=1, age=20000)
        r = self.stock(self.now - 15000)
        self.heard(id=2, age=3000, text='客户又说下周交付')
        chk = RoutedChat(check_pick=0)
        self.assertEqual(self.auto(chat=chk)['reason'], 'checked_none')
        self.assertEqual(self.auto(now=self.now + 150_000, chat=chk)['reason'], 'no_stock')
        self.assertEqual(([c[0] for c in chk.calls], self.said()), (['check'], []))
        row = self.db['say_rounds'].find_one({'rid': r['rid']})
        self.assertEqual([(t['why'], t['source']) for t in row['triggers']], [('auto_skip', 'auto')])

    def test_stop_between_lines(self):
        self.heard(age=8000)
        self.stock(self.now - 2000, scores=(8, 9, 5, 1, 8))
        self.on_sleep = speak.cancel_pending              # 别说了 during the 10 s gap
        res = self.auto()
        self.assertEqual((res['played'], res['stopped']), (['deadpan'], True))
        self.assertEqual(len(self.said()), 1)

    def test_stop_just_before_the_next_enqueue(self):
        self.heard(age=8000)
        self.stock(self.now - 2000, scores=(8, 9, 5, 1, 8))
        calls = []
        original = speak._speak_line
        def stop_then(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                speak.cancel_pending()
            return original(*a, **k)
        with patch.object(speak, '_speak_line', stop_then):
            res = self.auto()
        self.assertEqual((res['played'], len(self.said())), (['deadpan'], 1))

    def test_manual_round_holding_the_lock_stops_the_sequence(self):
        self.heard(age=8000)
        self.stock(self.now - 2000, scores=(8, 9, 5, 1, 8))
        held = []
        def click_starts():
            if not held:
                held.append(speak._SAY_LOCK.acquire(blocking=False))
        self.on_sleep = click_starts
        try:
            res = self.auto()
        finally:
            if held and held[0]:
                speak._SAY_LOCK.release()
        self.assertEqual(held, [True])                    # auto did not hold the lock across the gap
        self.assertEqual((res['played'], res['stopped']), (['deadpan'], True))

    def test_manual_round_during_the_gap_stops_the_sequence(self):
        self.heard(age=8000)
        self.stock(self.now - 2000, scores=(8, 9, 5, 1, 8))
        def click():
            self.on_sleep = None
            with patch.object(say_round, 'random', type('R', (), {'Random': NoShuffle})):
                speak.say_round(ENV, self.store, self.s, self.roles, self.tts, self.now + 3000, worker=self.prefetch, wait=True)
        self.on_sleep = click
        with patch.object(speak, 'CHAT', self.chat):
            res = speak.auto_play(ENV, self.store, self.s, self.tts, self.now, worker=self.prefetch,
                                  sleep=self.sleep, clock=lambda: self.now / 1000)
        self.assertEqual((res['played'], res['stopped']), (['deadpan'], True))
        self.assertEqual(len(self.said()), 2)             # auto's first line + the click's line, nothing after

    def test_prefetch_writing_skips_without_holding_the_say_lock(self):
        # Review M1: waiting for 提前写 while holding the say lock would refuse a manual 说一轮 with nothing speaking.
        self.heard(age=8000)
        self.stock(self.now - 2000)
        gate, res = threading.Event(), {}
        def prefetch_writes():
            with self.prefetch.writing():
                self.prefetch._busy = True
                gate.wait(2)
                self.prefetch._busy = False
        pt = threading.Thread(target=prefetch_writes)
        pt.start()
        while not self.prefetch.busy():
            pass
        at = threading.Thread(target=lambda: res.update(self.auto()))
        at.start()
        at.join(0.3)
        held = speak._SAY_LOCK.locked()
        gate.set()
        pt.join()
        at.join()
        self.assertFalse(held)                            # a manual round could take the lock meanwhile
        self.assertEqual((res['reason'], self.said()), ('busy', []))

    def test_stop_during_the_check_call_plays_and_marks_nothing(self):
        # Review M2: 别说了 while the check call runs.
        for pick in (0, 1):
            with self.subTest(pick=pick):
                self.heard(id=1, age=20000)
                r = self.stock(self.now - 15000)
                self.heard(id=2, age=3000, text='客户又说下周交付')
                inner = RoutedChat(check_pick=pick)
                def stopping(*a, **k):
                    out = inner(*a, **k)
                    speak.cancel_pending()
                    return out
                res = self.auto(chat=stopping)
                self.assertEqual((res['reason'], res['played']), ('stopped', []))
                self.assertEqual([c[0] for c in inner.calls], ['check'])
                self.assertEqual(self.said(), [])
                self.assertFalse(r.get('triggers') or r.get('plays'))
                row = self.db['say_rounds'].find_one({'rid': r['rid']})
                self.assertFalse(row.get('triggers') or row.get('plays'))

    def test_busy_at_wake_skips(self):
        self.heard(age=8000)
        self.stock(self.now - 2000)
        self.assertTrue(speak._SAY_LOCK.acquire(blocking=False))
        try:
            self.assertEqual(self.auto()['reason'], 'busy')
        finally:
            speak._SAY_LOCK.release()
        self.assertEqual(self.said(), [])

    def test_no_mouth(self):
        self.tts = EventTts(self.log, connected=False)
        self.heard(age=8000)
        self.stock(self.now - 2000)
        self.assertEqual(self.auto()['reason'], 'no_mouth')
        self.assertEqual(self.said(), [])

    def test_auto_min_score_setting_validates(self):
        speech = speech_settings.defaults()
        self.assertEqual(speech['round']['auto_min_score'], 8)
        mem = {'learning': True, 'apply': True}
        self.assertEqual(speech_settings.validate(speech, mem), [])
        for bad in (0, 11, 7.5, True, '8'):
            speech['round']['auto_min_score'] = bad
            self.assertTrue(speech_settings.validate(speech, mem), bad)
        for good in (1, 10):
            speech['round']['auto_min_score'] = good
            self.assertEqual(speech_settings.validate(speech, mem), [], good)


class FixedRng:
    def __init__(self, value):
        self.value, self.calls = value, []

    def uniform(self, lo, hi):
        self.calls.append((lo, hi))
        return self.value


class AutoWorkerTest(AutoCase):
    def setUp(self):
        super().setUp()
        self.mouth = 'tts'
        self.plays = []
        self.t = self.now
        self.w = self.make()

    def make(self, rng=None):
        def play(s, now_ms, is_current):
            self.plays.append(now_ms)
            return {'reason': 'played', 'played': ['deadpan'], 'stopped': False}
        return say_auto.Worker(self.store, prefetch=self.prefetch, settings_fn=lambda: self.s, mouth_fn=lambda: self.mouth,
                               play_fn=play, clock=lambda: self.t / 1000, sleep=self.sleep, rng=rng or FixedRng(150))

    def test_off_after_construction(self):
        self.assertFalse(self.w.enabled())
        self.assertFalse(self.store.state()['say_auto']['on'])
        self.w.set_enabled(True, self.now)
        w2 = self.make()                                   # a restart
        self.assertFalse(w2.enabled())
        self.assertFalse(self.store.state()['say_auto']['on'])
        self.assertEqual(w2.tick(self.now + 600_000), None)
        self.assertEqual(self.plays, [])

    def test_enabling_auto_enables_prefetch(self):
        self.assertFalse(self.prefetch.enabled())
        self.w.set_enabled(True, self.now)
        self.assertTrue((self.w.enabled(), self.prefetch.enabled()) == (True, True))
        self.w.set_enabled(False, self.now)
        self.assertTrue(self.prefetch.enabled())          # turning 连续说 off leaves 提前写 as it is

    def test_disabling_prefetch_disables_auto(self):
        self.w.set_enabled(True, self.now)
        self.prefetch.set_enabled(False, self.now + 1000)
        self.assertFalse(self.w.enabled())
        self.assertIsNone(self.w.next_at)
        self.assertFalse(self.store.state()['say_auto']['on'])

    def test_wakes_on_the_random_interval(self):
        rng = FixedRng(150)
        self.w = self.make(rng)
        self.w.set_enabled(True, self.now)
        self.assertEqual((rng.calls, self.w.next_at), ([(120, 180)], self.now + 150_000))
        self.assertIsNone(self.w.tick(self.now + 149_000))
        self.t = self.now + 150_000 + 40_000              # the playback took 40 s
        self.assertEqual(self.w.tick(self.now + 150_000), 'played')
        self.assertEqual(self.plays, [self.now + 150_000])
        self.assertEqual(self.w.next_at, self.t + 150_000)      # counted from the end of the wake
        self.assertEqual(self.w.last, {'reason': 'played', 'at': self.now + 150_000, 'n': 1, 'stopped': False})

    def test_real_rng_stays_in_120_to_180_s(self):
        self.w = self.make(random.Random(7))
        for i in range(200):
            self.w.set_enabled(True, self.now)
            self.assertTrue(120_000 <= self.w.next_at - self.now <= 180_000)

    def test_needs_round_pipeline_and_tts_mouth(self):
        self.w.set_enabled(True, self.now)
        self.mouth = 'clips'
        self.assertEqual(self.w.tick(self.now + 200_000), 'off')
        self.mouth, self.s['speech']['pipeline'] = 'tts', 'v2'
        self.assertEqual(self.w.tick(self.now + 400_000), 'off')
        self.assertEqual(self.plays, [])
        self.assertEqual(self.w.last['reason'], 'off')

    def test_status_for_the_page(self):
        self.w.set_enabled(True, self.now)
        st = self.w.status()
        self.assertEqual((st['on'], st['next_at'], st['last']), (True, self.now + 150_000, None))
        self.w.set_enabled(False, self.now)
        self.assertEqual((self.w.status()['on'], self.w.status()['next_at']), (False, None))

    def test_is_current_follows_the_switch(self):
        seen = []
        def play(s, now_ms, is_current):
            seen.append(is_current())
            self.w.set_enabled(False, now_ms)
            seen.append(is_current())
            return {'reason': 'played', 'played': [], 'stopped': True}
        self.w.play_fn = play
        self.w.set_enabled(True, self.now)
        self.w.tick(self.now + 200_000)
        self.assertEqual(seen, [True, False])


if __name__ == '__main__':
    unittest.main()
