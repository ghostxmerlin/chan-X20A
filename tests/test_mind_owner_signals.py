"""owner_signals: page ratings, folding per moment (last mark per line wins), two-column stats."""
import unittest

from mind_fixtures import FakeDB, T0
from mind import owner_signals as O


def round_doc(rid, moment, best, at, scope='office', plays=()):
    return {'rid': rid, 'moment_id': moment, 'at': at, 'scope_id': scope, 'judge': {'best_key': best},
            'lines': [{'key': k} for k in ('comfort', 'deadpan', 'roast', 'sad', 'worst', 'huyu')],
            'plays': [{'key': k, 'why': w} for k, w in plays]}


class OwnerSignalsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()

    def rate(self, rid, line, polarity, count, at, device='page'):
        return O.put_signal(self.db, {'kind': 'rating', 'value': {'polarity': polarity, 'count': count},
                                      'refers_to': {'rid': rid, 'line': line}, 'source': {'device': device}}, at)

    def test_rating_validation(self):
        for bad in ({'kind': 'rating', 'value': {'polarity': 2, 'count': 1}, 'refers_to': {'rid': 'r', 'line': 'roast'}},
                    {'kind': 'rating', 'value': {'polarity': 1, 'count': 6}, 'refers_to': {'rid': 'r', 'line': 'roast'}},
                    {'kind': 'rating', 'value': {'polarity': 1, 'count': 1}, 'refers_to': None},
                    {'kind': 'round_none', 'refers_to': {'uid': 'u1'}},
                    {'kind': 'other'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                O.put_signal(self.db, bad, T0)
        doc = self.rate('r1', 'roast', 1, 3, T0)
        self.assertEqual((doc['who'], doc['source']['device'], doc['state']), ('owner', 'page', 'applied'))

    def test_last_rating_wins_per_line(self):
        self.db.add('say_rounds', round_doc('r1', 'r1', 'roast', T0))
        self.rate('r1', 'roast', 1, 3, T0 + 1000)
        self.rate('r1', 'roast', -1, 1, T0 + 2000)
        self.assertEqual(O.round_marks(self.db, 'r1')['ratings']['roast'], -1)

    def test_ratings_kept_per_round_in_one_moment(self):
        # Final review I5: a rating on round 2's roast must not count for round 1's roast.
        self.db.add('say_rounds', round_doc('r1', 'm1', 'roast', T0), round_doc('r2', 'm1', 'roast', T0 + 30000))
        self.rate('r2', 'roast', 1, 2, T0 + 31000)
        self.assertEqual(O.stats(self.db, 'office', T0 - 1, T0 + 60000)['page']['judge_hit_rate'], 0.0)
        self.assertEqual(O.round_marks(self.db, 'r1')['ratings'], {})
        self.assertEqual(O.round_marks(self.db, 'r2')['ratings'], {'roast': 2})

    def test_good_rate_weighted_by_strength(self):
        self.db.add('say_rounds', round_doc('r1', 'r1', 'roast', T0))
        self.rate('r1', 'roast', 1, 3, T0 + 1000)
        self.rate('r1', 'sad', -1, 2, T0 + 1000)
        self.assertAlmostEqual(O.stats(self.db, 'office', T0 - 1, T0 + 10000)['page']['good_rate'], 0.6)

    def test_judge_hit_counts_any_good_on_first_round_best(self):
        self.db.add('say_rounds', round_doc('r1', 'm1', 'roast', T0), round_doc('r2', 'm1', 'sad', T0 + 30000))
        self.rate('r2', 'sad', 1, 2, T0 + 31000)          # good on the second round's best: not a hit
        self.assertEqual(O.stats(self.db, 'office', T0 - 1, T0 + 60000)['page']['judge_hit_rate'], 0.0)
        self.rate('r1', 'roast', 1, 1, T0 + 32000)        # any good on the first round's best: a hit
        self.assertEqual(O.stats(self.db, 'office', T0 - 1, T0 + 60000)['page']['judge_hit_rate'], 1.0)

    def test_columns_split_by_source(self):
        self.db.add('say_rounds', round_doc('r1', 'r1', 'roast', T0))
        self.rate('r1', 'roast', 1, 2, T0 + 1000)
        self.rate('r1', 'roast', -1, 1, T0 + 2000, device='dualkey-ab')
        st = O.stats(self.db, 'office', T0 - 1, T0 + 10000)
        self.assertEqual(st['page']['good_rate'], 1.0)
        self.assertEqual(st['daily']['good_rate'], 0.0)

    def test_uid_rating_maps_through_utterance(self):
        self.db.add('say_rounds', round_doc('r1', 'r1', 'worst', T0))
        self.db.add('utterances', {'uid': 'u9', 'rid': 'r1', 'line': 'worst', 'at': T0})
        O.put_signal(self.db, {'kind': 'rating', 'value': {'polarity': 1, 'count': 1}, 'refers_to': {'uid': 'u9'},
                               'source': {'device': 'dualkey-ab'}}, T0 + 1000)
        self.assertEqual(O.stats(self.db, 'office', T0 - 1, T0 + 10000)['daily']['judge_hit_rate'], 1.0)

    def test_round_none_and_scopes_kept_apart(self):
        self.db.add('say_rounds', round_doc('r1', 'r1', 'roast', T0), round_doc('r2', 'r2', 'roast', T0, scope='home'))
        O.put_signal(self.db, {'kind': 'round_none', 'refers_to': {'rid': 'r1'}}, T0 + 1000)
        self.rate('r2', 'roast', 1, 1, T0 + 1000)
        office = O.stats(self.db, 'office', T0 - 1, T0 + 10000)
        self.assertEqual((office['page']['none_rate'], office['page']['moments']), (1.0, 1))
        self.assertEqual(O.stats(self.db, 'home', T0 - 1, T0 + 10000)['page']['good_rate'], 1.0)


if __name__ == '__main__':
    unittest.main()


class DeviceSignalsTest(unittest.TestCase):
    """DualKey signals relayed by the ear (2026-11-13): rating / mode / click, deduplicated, time-attributed."""
    def setUp(self):
        self.db = FakeDB()

    def dev(self, kind, value, seq=1, age=200, at=T0 + 10000):
        return O.put_signal(self.db, {'kind': kind, 'value': value, 'age_ms': age,
                                      'source': {'device': 'dualkey-ae1c', 'boot': 'b1', 'seq': seq}}, at)

    def test_rating_attaches_to_the_line_playing_then(self):
        self.db.add('utterances', {'uid': 'u1', 'rid': 'r1', 'line': 'roast', 'at': T0, 'play_start_at': T0 + 1000, 'play_end_at': T0 + 4000},
                    {'uid': 'u2', 'rid': 'r1', 'line': 'sad', 'at': T0 + 20000, 'play_start_at': T0 + 21000, 'play_end_at': T0 + 23000})
        doc = self.dev('rating', {'polarity': 1, 'count': 2, 'presses_ms': [0, 200], 'capped': False}, at=T0 + 9000)
        self.assertEqual(doc['refers_to'], {'uid': 'u1'})
        self.assertEqual(doc['at'], T0 + 8800)
        late = self.dev('rating', {'polarity': -1, 'count': 1, 'presses_ms': [0], 'capped': False}, seq=2, at=T0 + 40000)
        self.assertIsNone(late['refers_to'])            # nothing played in the last 12 s: rates the moment

    def test_rating_during_playback_is_attached_once_play_times_are_known(self):
        # 2026-11-13 live: play_start_at is written only after the line finished, so a press while hello
        # was still talking found nothing; it must be matched again when read.
        self.db.add('utterances', {'uid': 'u9', 'rid': 'r9', 'line': 'deadpan', 'at': T0})
        doc = self.dev('rating', {'polarity': 1, 'count': 1, 'presses_ms': [0], 'capped': False}, age=0, at=T0 + 5000)
        self.assertIsNone(doc['refers_to'])
        self.db['utterances'].update_one({'uid': 'u9'}, {'$set': {'play_start_at': T0 + 1000, 'play_end_at': T0 + 8000}})
        self.assertEqual(O.recent(self.db, T0, T0 + 60000)[-1]['refers_to'], {'uid': 'u9'})
        self.assertEqual(O.round_marks(self.db, 'r9')['ratings'], {'deadpan': 1})
        self.assertEqual(self.db['owner_signals'].find_one({'sid': doc['sid']})['refers_to'], {'uid': 'u9'})

    def test_mode_and_click_are_recorded(self):
        self.assertEqual(self.dev('mode', {'direction': 'left'})['value'], {'direction': 'left'})
        self.assertEqual(self.dev('click', {'type': 'single'}, seq=2)['kind'], 'click')
        with self.assertRaises(ValueError):
            self.dev('mode', {'direction': 'diagonal'}, seq=3)

    def test_duplicate_device_seq_is_returned_not_stored_twice(self):
        a = self.dev('click', {'type': 'single'}, seq=5)
        b = self.dev('click', {'type': 'single'}, seq=5)
        self.assertEqual(a['sid'], b['sid'])
        self.assertEqual(self.db.count('owner_signals'), 1)

    def test_recent_lists_newest_last(self):
        self.dev('click', {'type': 'single'}, seq=1, at=T0 + 1000)
        self.dev('mode', {'direction': 'up'}, seq=2, at=T0 + 2000)
        rows = O.recent(self.db, T0, T0 + 5000)
        self.assertEqual([r['kind'] for r in rows], ['click', 'mode'])
