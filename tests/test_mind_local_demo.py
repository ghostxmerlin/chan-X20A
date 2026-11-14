"""scripts/mind-local-demo.py: the page demo runs mind's real mode API on FakeDB, with nothing paid behind it."""
import importlib.util
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('mind_local_demo', os.path.join(ROOT, 'scripts', 'mind-local-demo.py'))
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

from mind import api, mode_store   # noqa: E402  (the demo module put server/ on sys.path)


class LocalDemoTest(unittest.TestCase):
    def setUp(self):
        self.saved = mode_store.SETTINGS_TIMEOUT_MS
        self.now = [int(time.time() * 1000)]          # the API stamps inputs with the real clock
        store, self.ctx = demo.build(settings_timeout_s=30, clock_ms=lambda: self.now[0])
        self.server = api.make_server(store, 'k', port=0, ctx=self.ctx)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        mode_store.SETTINGS_TIMEOUT_MS = self.saved

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer k', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def workers(self):
        return self.ctx['say_worker'].enabled(), self.ctx['say_auto'].enabled()

    def test_starts_in_talk_with_nothing_running(self):
        st, r = self.call('/mode')
        self.assertEqual((st, r['snapshot']['mode'], r['robot']['connected']), (200, 'talk', False))
        self.assertEqual(self.workers(), (False, False))       # a restart is not an entry into talk

    def test_switching_and_talk_entry(self):
        _, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'crowd'}})
        self.assertEqual(r['snapshot']['mode'], 'crowd')
        _, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'crowd'}})
        self.assertEqual(r['snapshot']['mode'], 'idle')          # picking the current mode leaves it
        _, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'talk'}})
        self.assertEqual(r['snapshot']['mode'], 'talk')
        self.assertEqual(self.workers(), (True, True))           # entering talk: 提前写 + 连续说 on
        self.call('/mode', {'input': 'select', 'value': {'mode': 'idle'}})
        self.assertEqual(self.workers(), (False, False))

    def test_forward_in_talk_costs_nothing(self):
        _, r = self.call('/mode', {'input': 'forward'})
        self.assertEqual(r['result']['code'], 'not_implemented')  # no say_round_fn: no model, no tts

    def test_preset_zero_then_paid_switches_refuse(self):
        _, r = self.call('/preset', {'n': 0, 'source': 'page'})
        self.assertFalse(r['snapshot']['billing'])
        st, r = self.call('/switches', {'name': 'tingwu', 'on': True})
        self.assertEqual((st, r['code']), (409, 'billing_off'))
        _, r = self.call('/preset', {'n': 9, 'source': 'page'})
        self.assertEqual(r['robot'], {'hearing': 'tingwu', 'vision': 'recognize'})
        self.assertTrue(r['snapshot']['billing'])

    def test_settings_returns_after_the_short_timeout(self):
        _, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'settings'}})
        self.assertEqual(r['snapshot']['return_to'], 'talk')
        ms = self.ctx['mode_store']
        self.now[0] += 29_000
        self.assertIsNone(ms.tick())
        self.now[0] += 2_000
        ms.tick()
        self.assertEqual(ms.current()['mode'], 'talk')
        self.assertEqual(ms.current()['by'], 'timeout')

    def test_nothing_paid_is_configured(self):
        self.assertNotIn('env', self.ctx)
        self.assertNotIn('tts', self.ctx)
        with self.assertRaises(RuntimeError):
            self.ctx['say_worker'].write_fn()


if __name__ == '__main__':
    unittest.main()
