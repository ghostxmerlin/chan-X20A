import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0
from mind import settings

DAY = 86_400_000
WINDOW = 600_000


def fact(at, **kw):
    return {'key': 'owner_reclines', 'statement': '主人经常坐在靠背椅上', 'kind': 'habit',
            'subject_id': 'owner', 'modality': 'vision', 'relation': 'support',
            'refs': [{'c': 'eye_minutes', 'minute': at, 'version': 1}], **kw}


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('mind.memory'), 'memory store is missing')
        self.mod = importlib.import_module('mind.memory')
        self.db = FakeDB()
        self.mem = self.mod.MemoryStore(self.db)
        self.mem.ensure_indexes()
        self.s = settings.load(None)

    def observe(self, at, obs=None, scope='office', version='v1', coverage=None):
        self.mem.observe(scope, at, obs if obs is not None else [fact(at)],
                         coverage if coverage is not None else {'vision_s': 600, 'hearing_s': 600},
                         version, at + WINDOW, self.s)
        self.mem.reconcile(scope, at + WINDOW, self.s)

    def seed(self, kind='habit'):
        for day in range(3):
            for slot in range(2):
                at = T0 + day * DAY + slot * WINDOW
                self.observe(at, [fact(at, kind=kind)])
        return self.mem.list('office')['items'][0]

    def test_three_days_and_six_independent_windows_promote(self):
        row = self.seed()
        self.assertEqual(row['status'], 'known')
        self.assertEqual(row['subject_id'], 'owner')
        self.assertEqual(row['support_days'], 3)
        self.assertEqual(row['observed_windows'], 6)
        self.assertTrue(row['evidence_refs'])

    def test_same_window_and_same_day_never_create_a_habit(self):
        for i in range(8):
            self.observe(T0, version=f'v{i}')
        row = self.mem.list('office')['items'][0]
        self.assertEqual((row['status'], row['support_days'], row['observed_windows']), ('candidate', 1, 1))
        self.assertEqual(len(self.mem.history(row['memory_id'])['items']), 1)

    def test_revision_retracts_replaced_evidence(self):
        self.seed()
        at = T0 + 2 * DAY + WINDOW
        self.observe(at, [], version='corrected')
        row = self.mem.list('office')['items'][0]
        self.assertEqual(row['observed_windows'], 5)
        self.assertEqual(row['status'], 'candidate')

    def test_repeated_support_refreshes_metrics_without_spamming_history(self):
        row = self.seed()
        at = T0 + 2*DAY + 2*WINDOW
        self.observe(at)
        current = self.mem.list('office')['items'][0]
        self.assertEqual(current['version'], row['version'])
        self.assertEqual(current['latest_evidence']['observed_windows'], 7)
        self.assertEqual(current['latest_evidence']['last_seen'], at)

    def test_correction_and_disable_survive_automatic_observations(self):
        row = self.seed()
        edited = self.mem.edit(row['memory_id'], row['version'], 'correct', statement='主人喜欢换位置办公', now_ms=T0+3*DAY)
        self.observe(T0+4*DAY)
        row = self.mem.list('office')['items'][0]
        self.assertEqual(row['statement'], '主人喜欢换位置办公')
        self.assertEqual(row['version'], edited['version'])
        disabled = self.mem.edit(row['memory_id'], row['version'], 'disable', now_ms=T0+5*DAY)
        self.observe(T0+6*DAY)
        self.assertEqual(self.mem.list('office')['items'][0]['status'], 'disabled')
        self.assertEqual(self.mem.recall('office', T0+6*DAY), [])
        restored = self.mem.edit(row['memory_id'], disabled['version'], 'restore', restore_version=edited['version'], now_ms=T0+7*DAY)
        self.assertEqual(restored['statement'], '主人喜欢换位置办公')
        self.assertGreater(restored['version'], disabled['version'])

    def test_stale_manual_edit_does_not_overwrite(self):
        row = self.seed()
        changed = self.mem.edit(row['memory_id'], row['version'], 'confirm')
        with self.assertRaises(self.mod.MemoryConflict):
            self.mem.edit(row['memory_id'], row['version'], 'disable')
        self.assertEqual(self.mem.list('office')['items'][0]['version'], changed['version'])

    def test_old_automatic_memory_exits_recall_but_history_remains(self):
        row = self.seed()
        self.mem.reconcile('office', T0+40*DAY, self.s)
        self.assertEqual(self.mem.list('office')['items'][0]['status'], 'review')
        self.assertEqual(self.mem.recall('office', T0+40*DAY), [])
        self.assertGreater(self.mem.history(row['memory_id'])['total'], 1)

    def test_scope_and_replay_are_isolated(self):
        self.seed()
        self.assertEqual(self.mem.list('home')['total'], 0)
        other = self.mod.MemoryStore(self.db, lane='replay')
        self.assertEqual(other.list('office')['total'], 0)

    def test_routine_needs_five_days_with_fifteen_minutes_coverage(self):
        # Use 10:00 local time so both ten-minute windows belong to the same hour.
        start = int(settings.local_time(T0, self.s['tz']).replace(hour=10, minute=0, second=0).timestamp()*1000)
        for day in range(5):
            for slot in range(2):
                at = start+day*DAY+slot*WINDOW
                self.observe(at, [fact(at, kind='routine', key='discussion', subject_id='room',
                                       modality='hearing', statement='办公室有人讨论')])
        # 2026-11-10 Tue through Sat: workdays and restdays cannot be mixed.
        rows = self.mem.list('office')['items']
        self.assertFalse(any(r['status']=='known' for r in rows))
        at = start+6*DAY
        for slot in range(2):
            self.observe(at+slot*WINDOW, [fact(at+slot*WINDOW, kind='routine', key='discussion', subject_id='room',
                                             modality='hearing', statement='办公室有人讨论')])
        row = next(r for r in self.mem.list('office')['items'] if r.get('day_type')=='workday')
        self.assertEqual((row['status'], row['observed_days'], row['activity_days']), ('known', 5, 5))

    def test_no_sensor_coverage_cannot_establish_activity_or_absence(self):
        self.observe(T0, coverage={'vision_s': 0, 'hearing_s': 0})
        self.assertEqual(self.mem.list('office')['total'], 0)

    def test_bad_sources_and_empty_references_are_rejected(self):
        with self.assertRaises(ValueError):
            self.observe(T0, [fact(T0, refs=[{'c':'utterances', 'uid':'tts'}])])
        with self.assertRaises(ValueError):
            self.observe(T0, [fact(T0, refs=[])])


if __name__ == '__main__':
    unittest.main()
