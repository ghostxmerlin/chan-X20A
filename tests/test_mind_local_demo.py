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

from mind import api   # noqa: E402  (the demo module put server/ on sys.path)


class LocalDemoTest(unittest.TestCase):
    def setUp(self):
        self.now = [int(time.time() * 1000)]
        store, self.ctx = demo.build(settings_timeout_s=30, clock_ms=lambda: self.now[0])
        self.server = api.make_server(store, 'k', port=0, ctx=self.ctx)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.hserver = demo.hello_server(self.ctx['hello'], 'h', 0)
        threading.Thread(target=self.hserver.serve_forever, args=(0.02,), daemon=True).start()
        self.hbase = f'http://127.0.0.1:{self.hserver.server_address[1]}'

    def tearDown(self):
        for s in (self.server, self.hserver):
            s.shutdown()
            s.server_close()

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

    def hello_call(self, path, body=None):
        req = urllib.request.Request(self.hbase + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer h', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def select(self, mode, **v):
        return self.hello_call('/v1/control', {'target': 'mode', 'input': 'select', 'value': {'mode': mode, **v}})

    def test_starts_in_talk_as_hello_just_booted(self):
        st, r = self.call('/mode')
        self.assertEqual((st, r['snapshot']['mode'], r['snapshot']['by'], r['robot']['connected']), (200, 'talk', 'boot', True))
        self.assertEqual(self.workers(), (True, True))                # hello's boot is an entry into talk

    def test_switching_goes_through_hello_and_reaches_mind(self):
        self.assertEqual(self.select('crowd')[1]['result']['code'], 'ok')
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'crowd')
        self.assertEqual(self.workers(), (False, False))              # leaving talk
        self.select('crowd')
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'idle')     # picking the current mode leaves it
        self.select('talk')
        self.assertEqual((self.call('/mode')[1]['snapshot']['mode'], self.workers()), ('talk', (True, True)))
        t = self.hello_call('/v1/telemetry')[1]
        self.assertEqual((t['mode']['mode'], t['mode']['seq'], t['boot_id']), ('talk', 4, self.ctx['hello'].boot))

    def test_digits_four_to_eight_are_hello_selects(self):
        self.hello_call('/v1/control', {'target': 'mode', 'input': 'digit', 'value': 8})
        snap = self.call('/mode')[1]['snapshot']
        self.assertEqual((snap['mode'], snap['dir']), ('subtitle', 'en_zh'))

    def test_page_cannot_switch_on_mind(self):
        st, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'crowd'}})
        self.assertEqual((st, r['code']), (409, 'on_hello'))

    def test_forward_in_talk_costs_nothing(self):
        self.hello_call('/v1/control', {'target': 'mode', 'input': 'forward', 'value': None})
        row = self.ctx['mode_store'].db['mode_log'].docs[-1]
        self.assertEqual((row['input'], row['code']), ('forward', 'not_implemented'))  # no say_round_fn: no model, no tts

    def test_preset_zero_then_paid_switches_refuse(self):
        _, r = self.call('/preset', {'n': 0, 'source': 'page'})
        self.assertFalse(r['snapshot']['billing'])
        st, r = self.call('/switches', {'name': 'tingwu', 'on': True})
        self.assertEqual((st, r['code']), (409, 'billing_off'))
        _, r = self.call('/preset', {'n': 9, 'source': 'page'})
        self.assertEqual(r['robot'], {'hearing': 'tingwu', 'vision': 'recognize'})
        self.assertTrue(r['snapshot']['billing'])

    def test_settings_returns_after_the_short_timeout_on_hello(self):
        self.select('settings')
        self.assertEqual(self.call('/mode')[1]['snapshot']['return_to'], 'talk')
        hello = self.ctx['hello']
        self.now[0] += 29_000
        hello.tick()
        self.assertEqual(self.call('/mode')[1]['snapshot']['mode'], 'settings')
        self.now[0] += 2_000
        hello.tick()
        snap = self.call('/mode')[1]['snapshot']
        self.assertEqual((snap['mode'], snap['by']), ('talk', 'timeout'))

    def test_hello_needs_its_token_and_knows_only_mode(self):
        req = urllib.request.Request(self.hbase + '/v1/info')
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 401)
        self.assertEqual(self.hello_call('/v1/control', {'target': 'yaw_step', 'delta_deg': 45})[0], 404)
        self.assertEqual(self.hello_call('/v1/control', {'target': 'mode', 'input': 'billing', 'value': False})[0], 400)

    def test_nothing_paid_is_configured(self):
        self.assertNotIn('env', self.ctx)
        self.assertNotIn('tts', self.ctx)
        with self.assertRaises(RuntimeError):
            self.ctx['say_worker'].write_fn()


if __name__ == '__main__':
    unittest.main()
