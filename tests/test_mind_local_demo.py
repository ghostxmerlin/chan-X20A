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

    def test_starts_in_docking_as_hello_just_booted(self):
        # owner 2026-11-18: hello boots into crowd (shown as Docking, badge DOCK), not an entry into talk
        st, r = self.call('/mode')
        self.assertEqual((st, r['snapshot']['mode'], r['snapshot']['by'], r['robot']['connected']), (200, 'crowd', 'boot', True))
        self.assertEqual(r['snapshot']['look']['label'], 'DOCK')
        self.assertEqual(self.workers(), (False, False))              # 提前写 / 连续说 stay off outside talk

    def test_switching_goes_through_hello_and_reaches_mind(self):
        # Docking is the only mode (owner 2026-11-20): another mode is refused on hello, Docking again is already
        self.assertEqual(self.select('talk')[1]['result']['code'], 'removed_mode')
        self.assertEqual(self.select('crowd')[1]['result']['code'], 'already')
        self.assertEqual((self.call('/mode')[1]['snapshot']['mode'], self.workers()), ('crowd', (False, False)))
        t = self.hello_call('/v1/telemetry')[1]
        self.assertEqual((t['mode']['mode'], t['mode']['seq'], t['boot_id']), ('crowd', 1, self.ctx['hello'].boot))
    def test_digits_select_nothing_but_docking(self):
        r = self.hello_call('/v1/control', {'target': 'mode', 'input': 'digit', 'value': 8})[1]
        self.assertEqual((r['result']['code'], self.call('/mode')[1]['snapshot']['mode']), ('no_action', 'crowd'))
        r = self.hello_call('/v1/control', {'target': 'mode', 'input': 'digit', 'value': 5})[1]
        self.assertEqual(r['result']['code'], 'already')
    def test_page_cannot_switch_on_mind(self):
        st, r = self.call('/mode', {'input': 'select', 'value': {'mode': 'crowd'}})
        self.assertEqual((st, r['code']), (409, 'on_hello'))

    def test_forward_costs_nothing(self):
        rows = len(self.ctx['mode_store'].db['mode_log'].docs)
        r = self.hello_call('/v1/control', {'target': 'mode', 'input': 'forward', 'value': None})[1]
        self.assertEqual(r['result']['code'], 'no_action')                     # the old talk-show word does nothing
        self.assertEqual(len(self.ctx['mode_store'].db['mode_log'].docs), rows)
    def test_preset_zero_then_single_switches_still_work(self):
        _, r = self.call('/preset', {'n': 0, 'source': 'page'})
        self.assertFalse(r['snapshot']['billing'])
        self.assertEqual(self.workers(), (False, False))
        st, r = self.call('/switches', {'name': 'tingwu', 'on': True})          # billing gates nothing
        self.assertEqual((st, r['tingwu']['on'], r['billing']['on']), (200, True, False))
        _, r = self.call('/preset', {'n': 9, 'source': 'page'})
        self.assertEqual(r['robot'], {'hearing': 'tingwu', 'vision': 'recognize'})
        self.assertTrue(r['snapshot']['billing'])
        self.assertEqual(self.workers(), (False, False))              # 提前写 / 连续说 belonged to talk (gone)

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
        self.assertEqual(self.ctx['chat'].chat_fn, demo.demo_model)
        self.assertIsInstance(self.ctx['chat'].tts, demo.DemoTts)
        with self.assertRaises(RuntimeError):
            self.ctx['say_worker'].write_fn()


if __name__ == '__main__':
    unittest.main()
