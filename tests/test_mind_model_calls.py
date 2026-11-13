"""model_calls.run_json: the HTTP call runs outside the global lock, so a talk-show round can
write its lines in parallel (2026-11-13); the budget is still checked before every call."""
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from mind_fixtures import FakeDB, T0
from mind import model_calls, settings
from mind.store import Store

ENV = {'base_url': 'http://x', 'api_key': 'k'}
SCHEMA = model_calls.object_schema({'text': {'type': 'string'}})


class SlowChat:
    def __init__(self, delay=0.3):
        self.delay, self.calls, self.lock = delay, 0, threading.Lock()

    def __call__(self, base_url, key, body, timeout=90):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        return {'choices': [{'message': {'content': json.dumps({'text': 'ok'})}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}, int(self.delay * 1000), None


class ModelCallsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store, self.s = Store(self.db), settings.load(None)

    def call(self, chat):
        return model_calls.run_json(ENV, self.store, self.s, 'say_line', 'p', SCHEMA,
                                    [{'role': 'user', 'content': 'x'}], T0, chat=chat, model='gpt-6.1-terra')

    def test_parallel_calls_overlap(self):
        chat = SlowChat(0.3)
        started = time.monotonic()
        with ThreadPoolExecutor(5) as pool:
            results = list(pool.map(lambda _: self.call(chat), range(5)))
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertTrue(all(r[0] == {'text': 'ok'} for r in results))
        self.assertEqual(self.db.count('mind_runs'), 5)

    def test_budget_checked_before_each_call(self):
        self.s['budget_usd_per_day'] = 0.0
        chat = SlowChat(0)
        with self.assertRaises(model_calls.BudgetExceeded):
            self.call(chat)
        self.assertEqual(chat.calls, 0)


if __name__ == '__main__':
    unittest.main()
