"""HTTP routes of the one-button talk show: /say/round, /say/play, /signal, /say/prefetch, /say/rounds, /say/stats."""
import json
import threading
import unittest
import urllib.error
import urllib.request

from mind_fixtures import FakeDB, T0
from mind import api, say_prefetch
from mind.store import Store
from mind.settings_store import SettingsStore
from test_mind_speak import FakeTts, ROOT


def stored_round(rid='r1'):
    return {'rid': rid, 'moment_id': rid, 'at': T0, 'written_at': T0, 'scope_id': 'office',
            'signature': {'at': T0, 'texts': [], 'scene_minute': None}, 'context': {'secret': 'x'},
            'lines': [{'key': 'roast', 'text': '一句', 'voice': 'clone-voice-id', 'dialect': '普通话', 'instruction': 'i', 'rate': 1.0,
                       'score': 7, 'dropped': None}],
            'judge': {'order': ['roast'], 'best_key': 'roast', 'weak': False}, 'plays': [], 'checks': [], 'timings': {}}


class SayApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        self.tts = FakeTts()
        self.worker = say_prefetch.Worker(self.store, settings_fn=lambda: self.ss.current()[0], mouth_fn=lambda: 'tts',
                                          ctx_fn=lambda s, now: None, write_fn=lambda c, n: None)
        self.server = api.make_server(self.store, 'k', port=0, ctx={
            'settings_store': self.ss, 'tts': self.tts, 'roles_dir': str(ROOT / 'config/roles'), 'say_worker': self.worker,
            'env': {'base_url': 'http://unused.invalid', 'api_key': 'unused'}})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}' + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Bearer k', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_round_needs_tts_mouth(self):
        self.assertEqual(self.call('/say/round', {})[0], 409)

    def test_signal_validation_and_store(self):
        self.assertEqual(self.call('/signal', {'kind': 'rating', 'value': {'polarity': 3, 'count': 1}})[0], 400)
        status, doc = self.call('/signal', {'kind': 'rating', 'value': {'polarity': 1, 'count': 2},
                                            'refers_to': {'rid': 'r1', 'line': 'roast'}})
        self.assertEqual((status, doc['source']['device']), (200, 'page'))
        self.assertEqual(self.db.count('owner_signals'), 1)

    def test_prefetch_switch(self):
        status, body = self.call('/say/prefetch', {'enabled': True})
        self.assertEqual((status, body['enabled']), (200, True))
        self.assertTrue(self.worker.enabled())
        self.assertEqual(self.call('/say/prefetch', {'enabled': 'yes'})[0], 400)

    def test_rounds_hide_clone_ids_and_context(self):
        self.db.add('say_rounds', stored_round())
        status, rows = self.call(f'/say/rounds?from={T0 - 1}&to={T0 + 1}')
        self.assertEqual(status, 200)
        text = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn('clone-voice-id', text)
        self.assertNotIn('secret', text)
        self.assertEqual(rows[0]['lines'][0]['text'], '一句')

    def test_rounds_carry_triggered_flag_and_own_marks(self):
        # Final review I3/I5: the page shows the latest round that was actually triggered, with its own marks.
        self.db.add('say_rounds', stored_round('r1'), {**stored_round('r2'), 'at': T0 + 1, 'triggers': [{'at': T0 + 1, 'why': 'stock'}]})
        self.call('/signal', {'kind': 'rating', 'value': {'polarity': 1, 'count': 2}, 'refers_to': {'rid': 'r2', 'line': 'roast'}})
        status, rows = self.call(f'/say/rounds?from={T0 - 1}&to={T0 + 10}')
        by = {r['rid']: r for r in rows}
        self.assertEqual((by['r1']['triggered'], by['r2']['triggered']), (False, True))
        self.assertEqual((by['r1']['marks']['ratings'], by['r2']['marks']['ratings']), ({}, {'roast': 2}))

    def test_explain_round_by_rid(self):
        self.db.add('say_rounds', stored_round('rab12'))
        status, body = self.call('/say/explain?rid=rab12')
        self.assertEqual((status, body['rid']), (200, 'rab12'))
        self.assertNotIn('clone-voice-id', json.dumps(body, ensure_ascii=False))

    def test_recent_signals_listed(self):
        self.call('/signal', {'kind': 'mode', 'value': {'direction': 'up'}, 'source': {'device': 'dualkey-ae1c', 'boot': 'b', 'seq': 1}})
        status, rows = self.call('/signals?from=0&to=99999999999999')
        self.assertEqual(status, 400)                       # range at most 24 h
        import time as _t
        now = int(_t.time() * 1000)
        status, rows = self.call(f'/signals?from={now - 60000}&to={now + 1000}')
        self.assertEqual((status, rows[-1]['kind'], rows[-1]['source']['device']), (200, 'mode', 'dualkey-ae1c'))

    def test_recent_scene_minutes(self):
        import time as _t
        m = (int(_t.time() * 1000) // 60000) * 60000 - 60000
        self.db.add('mind_scenes', {'lane': 'live', 'minute': m, 'version': 1, 'source': 'model',
                                    'scene': {'people_doing': {'text': '主人在工位打字', 'confidence': 'high'}, 'summary': '安静工作'}})
        status, rows = self.call(f'/scenes?from={m - 1}&to={m + 60000}')
        self.assertEqual((status, rows[-1]['text'], rows[-1]['summary']), (200, '主人在工位打字', '安静工作'))
        self.assertEqual((rows[-1]['topic'], rows[-1]['sound']), ([], ''))

    def test_scene_minutes_carry_hearing(self):
        # 2026-11-14 owner: "情景摘要，为何只有视觉部分啊" — topic and sound_scene entries go out too.
        import time as _t
        m = (int(_t.time() * 1000) // 60000) * 60000 - 60000
        ent = lambda dim, text: {'key': f'{dim}.{text}', 'dimension': dim, 'text': text}
        self.db.add('mind_scenes', {'lane': 'live', 'minute': m, 'version': 1, 'source': 'model',
                                    'scene': {'people_doing': {'text': '两人讨论'}, 'summary': '讨论',
                                              'entries': [ent('topic', '交期'), ent('activity', '打字'), ent('topic', '客户需求'),
                                                          ent('sound_scene', '有人说话，偶尔敲键盘'), ent('topic', '芯片'), ent('topic', '午饭')]}})
        status, rows = self.call(f'/scenes?from={m - 1}&to={m + 60000}')
        self.assertEqual(status, 200)
        self.assertEqual(rows[-1]['topic'], ['交期', '客户需求', '芯片'])
        self.assertEqual(rows[-1]['sound'], '有人说话，偶尔敲键盘')
        self.assertEqual((rows[-1]['text'], rows[-1]['summary']), ('两人讨论', '讨论'))

    def test_stats_shape(self):
        status, body = self.call('/say/stats?days=7')
        self.assertEqual(status, 200)
        self.assertIn('page', body)
        self.assertIn('daily', body)

    def test_play_unknown_round_404(self):
        self.call('/switches', {'name': 'mouth', 'mode': 'tts'})
        self.assertEqual(self.call('/say/play', {'rid': 'rnone', 'line': 'roast'})[0], 404)


if __name__ == '__main__':
    unittest.main()
