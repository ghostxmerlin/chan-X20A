"""say_prompts: one line per direction, the Shanghainese fallback, the judge and the check."""
import json
import unittest

from mind_fixtures import T0
from mind import say_prompts as P

ROLE = {'id': 'roast', 'voice': 'tang', 'dialect': '普通话', 'persona': 'x'}


def ctx(evidence=()):
    return {'time': '11-13 10:00', 'scene': {'text': '大家在讨论'}, 'memories': [], 'environment': '办公室',
            'profile': {'topics': [], 'avoid': []}, 'recent': [], 'evidence': list(evidence)}


def heard(text, age_ms, quality='usable'):
    return {'kind': 'speech', 'at': T0 - age_ms, 'text': text, 'quality': quality, 'subject_id': 'unknown'}


def history(n=6):
    return {d: [{'text': f'{d}句{i}', 'device': '反话', 'point': f'点{i}'} for i in range(n)] for d in P.DIRECTIONS}


def strict(schema):
    return schema['additionalProperties'] is False and set(schema['required']) == set(schema['properties'])


class SayPromptsTest(unittest.TestCase):
    def test_line_prompt_names_only_its_direction(self):
        system = P.line_messages('roast', ROLE, ctx(), T0, history())[0]['content']
        self.assertIn(P.DIRECTIONS['roast'], system)
        self.assertIn('只写', system)
        for other in ('comfort', 'deadpan', 'sad', 'worst'):
            self.assertNotIn(P.DIRECTIONS[other], system)

    def test_line_prompt_carries_direction_history(self):
        payload = json.loads(P.line_messages('roast', ROLE, ctx(), T0, history())[1]['content'])
        self.assertEqual(payload['这个方向最近写过'], [f'roast句{i}' for i in range(2, 6)])
        self.assertEqual(payload['这个方向上一句的笑法'], '反话')

    def test_line_prompt_states_dialect(self):
        system = P.line_messages('sad', {**ROLE, 'id': 'sad', 'voice': 'xiaohei', 'dialect': '四川话'}, ctx(), T0, history())[0]['content']
        self.assertIn('四川话', system)

    def test_huyu_prompt_asks_for_authentic_shanghainese_and_gloss(self):
        system = P.huyu_messages(ctx(), T0, history())[0]['content']
        self.assertIn('上海话', system)
        self.assertIn('gloss', system)

    def test_judge_listing_shuffled_with_voice_and_dialect(self):
        lines = [{'text': '甲句', 'voice_label': '汤师爷', 'dialect': '普通话'},
                 {'text': '乙句', 'voice_label': '小黑', 'dialect': '四川话'}]
        user = P.judge_messages(ctx([heard('刚说的话', 5000)]), T0, lines)[1]['content']
        self.assertIn('1. 甲句（汤师爷，普通话）', user)
        self.assertIn('2. 乙句（小黑，四川话）', user)
        self.assertIn('刚说的话', user)

    def test_check_prompt_lists_new_speech_only_after_round(self):
        c = ctx([heard('写之前的话', 40000), heard('写之后的新话', 3000)])
        user = P.check_messages(c, T0, [{'text': '候选一'}, {'text': '候选二'}], since_ms=T0 - 20000)[1]['content']
        self.assertIn('写之后的新话', user)
        self.assertNotIn('写之前的话', user)
        self.assertIn('1. 候选一', user)

    def test_schemas_are_strict(self):
        for schema in (P.LINE_SCHEMA, P.HUYU_SCHEMA, P.JUDGE_SCHEMA, P.CHECK_SCHEMA):
            self.assertTrue(strict(schema))


if __name__ == '__main__':
    unittest.main()
