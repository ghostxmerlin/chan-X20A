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
        m = O.fold_moments(self.db, 'office', T0 - 1, T0 + 10000)[0]
        self.assertEqual(m['ratings']['roast'][0], -1)

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
