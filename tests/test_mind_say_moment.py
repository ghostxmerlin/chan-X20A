"""Same moment: within 90 s, and half of the sentences heard in the last 60 s are shared."""
import unittest

from mind_fixtures import T0
from mind import say_moment as M


def ctx(texts_ages, scene_minute=None):
    ev = [{'kind': 'speech', 'at': T0 - age, 'text': t, 'quality': 'uncertain'} for t, age in texts_ages]
    return {'evidence': ev, 'scene': {'minute': scene_minute} if scene_minute else None}


class SayMomentTest(unittest.TestCase):
    def sig(self, texts_ages, at=T0, scene_minute=None):
        return M.signature(ctx(texts_ages, scene_minute), at)

    def test_signature_keeps_only_last_90_seconds(self):
        s = self.sig([('老话', 95000), ('新话一', 70000), ('新话二', 5000)])
        self.assertEqual(s['texts'], sorted(['新话一', '新话二']))

    def test_half_shared_within_90s_is_same(self):
        prev = self.sig([('甲', 30000), ('乙', 20000)], at=T0 - 60000)
        cur = self.sig([('乙', 20000), ('丙', 5000)])
        prev['texts'] = ['甲', '乙']
        self.assertTrue(M.same_moment(prev, cur))

    def test_over_90s_is_new(self):
        prev = {'at': T0 - 91000, 'texts': ['甲'], 'scene_minute': None}
        cur = {'at': T0, 'texts': ['甲'], 'scene_minute': None}
        self.assertFalse(M.same_moment(prev, cur))

    def test_less_than_half_shared_is_new(self):
        prev = {'at': T0 - 30000, 'texts': ['甲', '乙', '丙'], 'scene_minute': None}
        cur = {'at': T0, 'texts': ['甲', '丁', '戊'], 'scene_minute': None}
        self.assertFalse(M.same_moment(prev, cur))

    def test_no_sentences_compares_scene_minute(self):
        prev = {'at': T0 - 30000, 'texts': [], 'scene_minute': T0 - 60000}
        self.assertTrue(M.same_moment(prev, {'at': T0, 'texts': [], 'scene_minute': T0 - 60000}))
        self.assertFalse(M.same_moment(prev, {'at': T0, 'texts': [], 'scene_minute': T0}))

    def test_no_previous_is_new(self):
        self.assertFalse(M.same_moment(None, {'at': T0, 'texts': ['甲'], 'scene_minute': None}))


if __name__ == '__main__':
    unittest.main()
