"""say_prefetch.Worker: pre-write a round when a stretch of speech ends (user, 2026-11-13)."""
import threading
import unittest

from mind_fixtures import FakeDB, T0
from mind import say_prefetch as SP, settings
from mind.store import Store

HOUR = 3600_000


class Harness:
    def __init__(self, test):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.s = settings.load(None)
        self.s['speech']['pipeline'] = 'round'
        self.mouth = 'tts'
        self.speech = []                   # (at, text) of clear sentences
        self.writes = []
        self.block = None
        self.worker = SP.Worker(self.store, settings_fn=lambda: self.s, mouth_fn=lambda: self.mouth,
                                ctx_fn=self.ctx, write_fn=self.write)

    def ctx(self, s, now_ms):
        return {'scope': {'id': 'office'}, 'scene': None,
                'evidence': [{'kind': 'speech', 'at': at, 'text': t, 'quality': 'usable'} for at, t in self.speech
                             if s['speech'].get('scope_since', 0) <= at <= now_ms]}   # like speech_context: scope start bounds it

    def write(self, ctx, now_ms):
        if self.block:
            self.block.wait(2)
        texts = sorted(e['text'] for e in ctx['evidence'] if e['at'] >= now_ms - 90000)
        r = {'rid': f'r{len(self.writes)}', 'written_at': now_ms, 'scope_id': 'office',
             'signature': {'at': now_ms, 'texts': texts, 'scene_minute': None}}
        self.writes.append(r)
        return r


class SayPrefetchTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness(self)
        self.w = self.h.worker
        self.w.set_enabled(True, T0)

    def test_off_costs_nothing(self):
        self.w.set_enabled(False, T0)
        self.h.speech = [(T0 + 1000, '甲')]
        self.assertEqual({self.w.step(T0 + 10000 + i * 1000) for i in range(100)}, {'off'})
        self.assertEqual(self.h.writes, [])

    def test_writes_after_speech_ends(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.assertEqual(self.w.step(T0 + 5000), 'waiting')
        self.assertEqual(self.w.step(T0 + 7000), 'wrote')
        self.assertEqual(self.w.stock('office')['rid'], 'r0')

    def test_last_step_is_kept_for_the_page(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.w.step(T0 + 5000)
        self.assertEqual(self.w.last, {'reason': 'waiting', 'at': T0 + 5000})
        self.w.step(T0 + 7000)
        self.assertEqual(self.w.last['reason'], 'wrote')

    def test_spacing_30s(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.w.step(T0 + 7000)
        self.h.speech.append((T0 + 20000, '完全不同的新话题'))
        self.assertEqual(self.w.step(T0 + 26000), 'spacing')
        self.h.speech.append((T0 + 100000, '又一个新话题'))
        self.assertEqual(self.w.step(T0 + 106000), 'wrote')     # > 90 s later: a new moment

    def test_same_moment_not_rewritten(self):
        self.h.speech = [(T0 + 1000, '甲'), (T0 + 2000, '乙')]
        self.w.step(T0 + 8000)
        self.h.speech.append((T0 + 30000, '乙'))
        self.assertEqual(self.w.step(T0 + 40000), 'same_moment')

    def test_needs_tts_mouth_and_round_pipeline(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.h.mouth = 'clips'
        self.assertEqual(self.w.step(T0 + 7000), 'off')
        self.h.mouth, self.h.s['speech']['pipeline'] = 'tts', 'v2'
        self.assertEqual(self.w.step(T0 + 7000), 'off')

    def test_budget_window_resets_next_hour(self):
        start = (T0 // HOUR) * HOUR
        self.h.db.add('mind_runs', {'run_id': 'x', 'step': 'say_line', 'at': start + 1000, 'cost_usd': 0.61})
        self.h.speech = [(start + 2000, '甲')]
        self.assertEqual(self.w.step(start + 9000), 'budget')
        self.h.speech.append((start + HOUR + 1000, '新的一小时'))
        self.assertEqual(self.w.step(start + HOUR + 8000), 'wrote')

    def test_disable_invalidates_stock(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.w.step(T0 + 7000)
        self.w.set_enabled(False, T0 + 8000)
        self.assertIsNone(self.w.stock('office'))

    def test_profile_change_invalidates_stock(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.w.step(T0 + 7000)
        self.h.s['speech']['scope_since'] = T0 + 7500
        self.w.step(T0 + 8000)
        self.assertIsNone(self.w.stock('office'))

    def test_restart_starts_off(self):
        self.assertTrue(self.h.store.state()['say_prefetch']['on'])
        w2 = SP.Worker(self.h.store, settings_fn=lambda: self.h.s, mouth_fn=lambda: 'tts', ctx_fn=self.h.ctx, write_fn=self.h.write)
        self.assertFalse(w2.enabled())
        self.assertFalse(self.h.store.state()['say_prefetch']['on'])

    def test_click_during_prefetch_reuses_one_round(self):
        self.h.speech = [(T0 + 1000, '甲')]
        self.h.block = threading.Event()
        t = threading.Thread(target=self.w.step, args=(T0 + 7000,))
        t.start()
        threading.Timer(0.2, self.h.block.set).start()
        while not self.w.busy():
            pass
        with self.w.writing():
            got = self.w.stock('office')
        t.join()
        self.assertEqual(got['rid'], 'r0')
        self.assertEqual(len(self.h.writes), 1)


if __name__ == '__main__':
    unittest.main()


class SayPrefetchRaceTest(unittest.TestCase):
    """Final review C1/I6: re-check under the lock; back off after a failed write."""
    def setUp(self):
        self.h = Harness(self)
        self.w = self.h.worker
        self.w.set_enabled(True, T0)

    def test_click_first_then_worker_writes_once(self):
        self.h.speech = [(T0 + 1000, '甲')]
        click_round = {'rid': 'rclick', 'written_at': T0 + 6500, 'scope_id': 'office',
                       'signature': {'at': T0 + 6500, 'texts': ['甲'], 'scene_minute': None}}
        real = self.w._lock
        worker = self.w

        class ClickFirst:
            def __enter__(self):
                worker.put('office', click_round)      # the click wrote while the worker was deciding
                return real.__enter__()

            def __exit__(self, *a):
                return real.__exit__(*a)
        self.w._lock = ClickFirst()
        self.assertIn(self.w.step(T0 + 7000), ('nothing_new', 'spacing', 'same_moment'))
        self.assertEqual(self.h.writes, [])
        self.assertEqual(self.w.stock('office')['rid'], 'rclick')

    def test_failed_write_backs_off(self):
        self.h.speech = [(T0 + 1000, '甲')]
        def boom(ctx, now):
            raise RuntimeError('这一轮没写出足够的句子')
        self.w.write_fn = boom
        with self.assertRaises(RuntimeError):
            self.w.step(T0 + 7000)
        self.assertEqual(self.w.step(T0 + 13000), 'spacing')
