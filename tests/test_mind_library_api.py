import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind import api
from mind.store import Store
from mind.settings_store import SettingsStore


class LibraryApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.server = api.make_server(Store(self.db), 'k', port=0, ctx={'settings_store': SettingsStore(self.db, '/nonexistent.json')})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None, token='k'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}' + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_create_read_edit_conflict(self):
        s, card = self.call('/db/person', {'fields': {'kind': 'regular', 'display': '人物甲'}})
        self.assertEqual((s, card['version']), (200, 1))
        pid = card['person_id']
        body = {'id': pid, 'expected_version': 1, 'fields': {'kind': 'regular', 'display': '人物乙'}}
        self.assertEqual(self.call('/db/person', body)[0], 200)
        self.assertEqual(self.call('/db/person', body)[0], 409)
        self.assertEqual(self.call('/db/item?c=people_cards&id=' + pid)[1]['versions'][0]['display'], '人物乙')
        self.assertEqual(self.call('/db/people')[1]['items'][0]['person_id'], pid)

    def test_notes_bulk_entry_shape_and_restore(self):
        self.call('/db/person', {'fields': {'kind': 'owner', 'display': '主人'}})
        ids = [self.call('/db/person/note', {'fields': {'person_id': 'owner', 'kind': 'trait', 'text': t}})[1]['note_id']
               for t in ('爱笑', '怕冷')]
        self.assertEqual(len(self.call('/db/person?id=owner')[1]['notes']), 2)
        self.call('/db/person/note', {'id': ids[0], 'expected_version': 1,
                                      'fields': {'person_id': 'owner', 'kind': 'trait', 'text': '改过'}})
        s, r = self.call('/db/person/note', {'id': ids[0], 'expected_version': 2, 'restore_version': 1})
        self.assertEqual((s, r['text']), (200, '爱笑'))

    def test_bad_input_codes(self):
        self.assertEqual(self.call('/db/person?id=%24ne')[0], 400)
        self.assertEqual(self.call('/db/person?id=p_' + '0' * 12)[0], 404)
        self.assertEqual(self.call('/db/item?c=sessions&id=owner')[0], 400)
        self.assertEqual(self.call('/db/memories?limit=101')[0], 400)
        self.assertEqual(self.call('/db/memory', {'fields': {'kind': 'event'}, 'evil': 1})[0], 400)
        self.assertEqual(self.call('/db/knowledge', {'fields': {'kind': 'term', 'name': 'x', 'origin': 'fused'}})[0], 400)

    def test_writes_only_library_collections_and_no_model_calls(self):
        self.call('/db/overview')   # first settings read seeds mind_settings (existing behaviour); not a library write
        before = {k: len(v.docs) for k, v in self.db.collections.items()}
        self.call('/db/knowledge', {'fields': {'kind': 'work', 'name': '工作背景', 'statement': '占位'}})
        changed = {k for k, v in self.db.collections.items() if len(v.docs) != before.get(k, 0)}
        self.assertEqual(changed, {'knowledge_items'})
        self.assertEqual(self.db.count('mind_runs'), 0)

    def test_overview(self):
        o = self.call('/db/overview')[1]
        self.assertEqual(o['collections']['memory_items']['total'], 0)
        self.assertIn('voice_labels', o)

    def test_reason_bounded_and_typed_and_auth(self):
        s, card = self.call('/db/person', {'fields': {'kind': 'regular', 'display': '人物甲'}, 'reason': '因' * 500})
        self.assertEqual((s, len(card['reason'])), (200, 200))
        self.assertEqual(self.call('/db/person', {'fields': {'kind': 'regular', 'display': '人物乙'}, 'reason': 5})[0], 400)
        self.assertEqual(self.call('/db/people', token='bad')[0], 401)
        self.assertEqual(self.call('/db/person/note', {'fields': 'x'})[0], 400)
        self.assertEqual(self.call('/db/person', {'id': 'p_' + '0' * 12, 'expected_version': 1, 'fields': {'kind': 'regular', 'display': 'x'}})[0], 404)

    def test_query_params_validated(self):
        self.assertEqual(self.call('/db/memories?offset=x')[0], 400)
        self.assertEqual(self.call('/db/memories?from=1&to=9&kind=event')[0], 200)
        self.assertEqual(self.call('/db/knowledge?q=x&limit=5')[1]['limit'], 5)
        self.assertEqual(self.call('/db/item?c=people_cards&id=%24ne')[0], 400)
        self.assertEqual(self.call('/db/person')[0], 400)


if __name__ == '__main__':
    unittest.main()
