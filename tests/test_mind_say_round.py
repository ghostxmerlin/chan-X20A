"""say_round: five lines written in parallel plus a Shanghainese fallback, one judge call, the check."""
import json
import random
import threading
import unittest
from pathlib import Path

from mind_fixtures import FakeDB, T0
from mind import say_prompts as P, say_round as R, settings, speak, speech_settings
from mind.store import Store

ROOT = Path(__file__).resolve().parents[1]
ENV = {'base_url': 'http://x', 'api_key': 'k'}


class NoShuffle(random.Random):
    def shuffle(self, x):
        pass


class RoutedChat:
    """Answers by prompt kind; per-direction overrides; thread-safe call log."""
    def __init__(self, scores=(3, 9, 5, 1, 7), weak=False, bad=(), check_pick=1):
        self.scores, self.weak, self.bad, self.check_pick = list(scores), weak, set(bad), check_pick
        self.calls, self.lock = [], threading.Lock()

    def kind(self, body):
        system = body['messages'][0]['content']
        if system.startswith(P.JUDGE_RULES[:20]): return 'judge', None
        if system.startswith(P.CHECK_RULES[:20]): return 'check', None
        if '【本次写一句地道的上海话】' in system: return 'huyu', None
        for d, text in P.DIRECTIONS.items():
            if text in system: return 'line', d
        return 'other', None

    def __call__(self, base_url, key, body, timeout=90):
        kind, d = self.kind(body)
        with self.lock:
            self.calls.append((kind, d, body.get('temperature')))
        if kind == 'line':
            content = '{bad json' if d in self.bad else json.dumps({'point': f'{d}点', 'device': '反话', 'text': f'这是{d}写的一句'})
        elif kind == 'huyu':
            content = json.dumps({'text': '侬讲讲看伐', 'gloss': '你说说看嘛'})
        elif kind == 'judge':
            content = json.dumps({'scores': self.scores, 'best': 2, 'weak': self.weak})
        else:
            content = json.dumps({'pick': self.check_pick})
        return {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}, 50, None


def ctx(now=T0, clear=True):
    return {'scope': {'id': 'office'}, 'time': '11-13 10:00', 'scene': {'text': '讨论', 'minute': now - 60000},
            'environment': '', 'memories': [], 'profile': {'topics': [], 'avoid': []}, 'recent': [],
            'evidence': [{'kind': 'speech', 'at': now - 5000, 'text': '客户又加需求了', 'quality': 'usable' if clear else 'uncertain'}]}


class SayRoundTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store, self.s = Store(self.db), settings.load(None)
        self.roles = speak.load_roles(ROOT / 'config/roles')

    def write(self, chat, **kw):
        return R.write_round(ENV, self.store, self.s, self.roles, ctx(), T0, chat=chat, rng=NoShuffle(), **kw)

    def test_round_writes_five_plus_huyu_and_ranks(self):
        chat = RoutedChat()
        r = self.write(chat)
        kinds = [c[0] for c in chat.calls]
        self.assertEqual(kinds.count('line'), 5)
        self.assertEqual(kinds.count('huyu'), 1)
        self.assertEqual(kinds.count('judge'), 1)
        self.assertEqual(R.ranked_keys(r), ['deadpan', 'worst', 'roast', 'comfort', 'sad'])
        self.assertEqual(r['judge']['best_key'], 'deadpan')
        self.assertEqual(self.db.count('say_rounds'), 1)
        self.assertEqual(r['moment_id'], r['rid'])

    def test_temperatures(self):
        chat = RoutedChat()
        self.write(chat)
        temps = {k: t for k, _, t in chat.calls}
        self.assertEqual(temps['line'], 0.65)
        self.assertEqual(temps['judge'], 0)

    def test_round_survives_two_failed_directions(self):
        r = self.write(RoutedChat(scores=(3, 9, 5), bad=('roast', 'sad')))
        dropped = {x['key']: x.get('dropped') for x in r['lines']}
        self.assertEqual(dropped['roast'], 'invalid')
        self.assertEqual(dropped['sad'], 'invalid')
        self.assertEqual(len(R.ranked_keys(r)), 3)

    def test_round_fails_with_fewer_than_two_lines(self):
        chat = RoutedChat(bad=('comfort', 'deadpan', 'roast', 'sad'))
        with self.assertRaises(RuntimeError):
            self.write(chat)
        self.assertNotIn('judge', [c[0] for c in chat.calls])

    def test_casting_from_role_config(self):
        r = self.write(RoutedChat())
        line = {x['key']: x for x in r['lines']}
        roast = speech_settings.role_config(self.roles['roast'], self.s)
        self.assertEqual((line['roast']['voice'], line['roast']['instruction']), (roast['voice'], roast['instruction']))
        self.assertTrue(line['huyu']['instruction'].startswith('请讲上海话。'))
        self.assertEqual(line['huyu']['gloss'], '你说说看嘛')

    def test_moment_id_passed_through(self):
        r = self.write(RoutedChat(), moment_id='rold')
        self.assertEqual(r['moment_id'], 'rold')

    def test_check_returns_none_when_model_says_zero(self):
        r = self.write(RoutedChat())
        self.assertIsNone(R.check_round(ENV, self.store, self.s, r, ctx(T0 + 20000), T0 + 20000, chat=RoutedChat(check_pick=0)))
        self.assertEqual(R.check_round(ENV, self.store, self.s, r, ctx(T0 + 20000), T0 + 20000, chat=RoutedChat(check_pick=2)), 'worst')

    def test_is_thin_flags(self):
        r = self.write(RoutedChat(weak=True))
        self.assertTrue(R.is_thin(r, ctx(clear=True), T0, self.s))
        r = self.write(RoutedChat(weak=False))
        self.assertFalse(R.is_thin(r, ctx(clear=True), T0, self.s))
        self.assertTrue(R.is_thin(r, ctx(clear=False), T0, self.s))


if __name__ == '__main__':
    unittest.main()
