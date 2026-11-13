import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind.library import Library, LibraryConflict, voice_label
from mind.store import Store


class LibraryWriteTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(); self.lib = Library(self.db)

    def card(self, **kw):
        return self.lib.put('people_cards', {'kind': 'regular', 'display': '人物甲', **kw},
                            item_id=None, expected_version=None, scope_id='office', now_ms=1000)

    def test_create_and_append_versions(self):
        a = self.card()
        self.assertRegex(a['person_id'], r'^p_[a-f0-9]{12}$')
        self.assertEqual((a['version'], a['supersedes'], a['actor']), (1, None, 'owner'))
        self.assertEqual(a['treatment'], {'tease': 'words_only', 'quote': 'gist'})
        b = self.lib.put('people_cards', {'kind': 'regular', 'display': '人物乙'}, item_id=a['person_id'],
                         expected_version=1, scope_id='office', now_ms=2000)
        self.assertEqual((b['version'], b['supersedes']), (2, 1))
        self.assertEqual(self.db.count('people_cards'), 2)
        self.assertEqual(self.lib.head('people_cards', a['person_id'])['display'], '人物乙')

    def test_concurrent_edit_conflicts_and_keeps_old_version(self):
        a = self.card()
        upd = dict(item_id=a['person_id'], expected_version=1, scope_id='office', now_ms=2000)
        self.lib.put('people_cards', {'kind': 'regular', 'display': '甲一'}, **upd)
        with self.assertRaises(LibraryConflict):
            self.lib.put('people_cards', {'kind': 'regular', 'display': '甲二'}, **upd)
        self.assertEqual([d['display'] for d in self.db['people_cards'].docs], ['人物甲', '甲一'])

    def test_owner_card_is_unique(self):
        self.assertEqual(self.card(kind='owner')['person_id'], 'owner')
        with self.assertRaises(LibraryConflict):
            self.card(kind='owner')

    def test_server_fields_and_unknown_fields_rejected(self):
        self.card(kind='owner')          # the person exists, so only the extra field can be the reason
        for extra in ({'origin': 'fused'}, {'fusion': {}}, {'version': 9}, {'actor': 'worker'},
                      {'owner_original': {}}, {'evil': 1}):
            with self.assertRaises(ValueError):
                self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '爱笑', **extra},
                             item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.assertEqual(self.db.count('people_notes'), 0)

    def test_note_provenance_defaults(self):
        self.card(kind='owner')
        n = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '爱笑'},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.assertEqual((n['origin'], n['owner_original'], n['derived_from'], n['fusion'], n['status']),
                         ('owner', None, [], None, 'active'))

    def test_references_must_exist(self):
        bad = [('people_notes', {'person_id': 'p_' + 'a' * 12, 'kind': 'trait', 'text': 'x'}),
               ('memory_items', {'kind': 'event', 'title': 't', 'statement': 's', 'subjects': ['p_' + 'b' * 12],
                                 'when': {'type': 'point', 'start_ms': 1, 'end_ms': None}}),
               ('memory_items', {'kind': 'event', 'title': 't', 'statement': 's', 'topics': ['k_' + 'c' * 12],
                                 'when': {'type': 'point', 'start_ms': 1, 'end_ms': None}})]
        for c, f in bad:
            with self.assertRaises(ValueError):
                self.lib.put(c, f, item_id=None, expected_version=None, scope_id='office', now_ms=1)

    def test_limits_and_enums(self):
        self.card(kind='owner')
        for f in ({'text': ''}, {'text': '字' * 201}, {'kind': 'health'}):
            with self.assertRaises(ValueError):
                self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '爱笑', **f},
                             item_id=None, expected_version=None, scope_id='office', now_ms=1)
        with self.assertRaises(ValueError):   # span must not end before it starts
            self.lib.put('memory_items', {'kind': 'event', 'title': 't', 'statement': 's',
                                          'when': {'type': 'span', 'start_ms': 5, 'end_ms': 4}},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)

    def test_kind_and_person_cannot_change(self):
        a = self.card()
        with self.assertRaises(ValueError):
            self.lib.put('people_cards', {'kind': 'visitor', 'display': '人物甲'}, item_id=a['person_id'],
                         expected_version=1, scope_id='office', now_ms=2)

    def test_restore_owner_version(self):
        self.card(kind='owner')
        n = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '原版'},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '改过'},
                     item_id=n['note_id'], expected_version=1, scope_id='office', now_ms=2)
        r = self.lib.restore('people_notes', n['note_id'], expected_version=2, restore_version=1, now_ms=3)
        self.assertEqual((r['version'], r['text'], r['origin'], r['supersedes']), (3, '原版', 'owner', 2))
        with self.assertRaises(KeyError):
            self.lib.restore('people_notes', n['note_id'], expected_version=3, restore_version=9, now_ms=4)

    def test_indexes_unique_on_id_and_version(self):
        self.lib.ensure_indexes()
        self.assertIn(([('kid', 1), ('version', 1)], True), self.db['knowledge_items'].indexes)
        self.assertIn(([('lid', 1)], True), self.db['voice_labels'].indexes)

    def test_store_ensure_indexes_creates_library_indexes(self):
        Store(self.db).ensure_indexes()
        self.assertTrue(self.db['people_cards'].indexes)

    def test_voice_label_schema(self):
        lab = voice_label({'target': {'session': '20261112/x-c1', 'speech_id': 's-1', 'pos': [0, 16000]},
                           'person_id': 'owner', 'verdict': 'is', 'via': 'single', 'review_day': '2026-11-12',
                           'page_version': 1}, now_ms=5)
        self.assertRegex(lab['lid'], r'^l_[a-f0-9]{12}$')
        self.assertEqual((lab['actor'], lab['at'], lab['group'], lab['target']['tingwu_n']), ('owner', 5, None, None))
        base = {'target': {'session': 's', 'speech_id': 'x', 'pos': [0, 8]}, 'person_id': 'owner', 'verdict': 'is',
                'via': 'single', 'review_day': '2026-11-12', 'page_version': 1}
        voice_label(base, now_ms=5)
        for bad in ({'verdict': 'is', 'person_id': None}, {'target': {'session': 's', 'speech_id': 'x', 'pos': [5, 5]}},
                    {'review_day': '昨天'}, {'verdict': 'maybe'}, {'lid': 'l_' + 'a' * 12}, {'group': {'kind': 'x', 'key': 'k'}}):
            with self.assertRaises(ValueError):
                voice_label({**base, **bad}, now_ms=5)

    def test_knowledge_and_memory_roundtrip(self):
        self.card(kind='owner')
        k = self.lib.put('knowledge_items', {'kind': 'place', 'name': '地点甲', 'subjects': ['owner']},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.assertRegex(k['kid'], r'^k_[a-f0-9]{12}$')
        self.assertEqual((k['scope_ids'], k['when'], k['origin']), (['office'], {'valid_from': None, 'valid_to': None}, 'owner'))
        m = self.lib.put('memory_items', {'kind': 'event', 'title': '事', 'statement': '说明', 'topics': [k['kid']],
                                          'when': {'type': 'point', 'start_ms': 1, 'end_ms': 9}},
                         item_id=None, expected_version=None, scope_id='office', now_ms=2)
        self.assertEqual((m['when']['end_ms'], m['source']), (None, {'kind': 'owner', 'refs': []}))

    def test_restore_refuses_non_owner_version(self):
        self.card(kind='owner')
        n = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '甲'},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        self.db['people_notes'].docs[0]['origin'] = 'fused'
        self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '乙'},
                     item_id=n['note_id'], expected_version=1, scope_id='office', now_ms=2)
        with self.assertRaises(ValueError):
            self.lib.restore('people_notes', n['note_id'], expected_version=2, restore_version=1, now_ms=3)


if __name__ == "__main__":
    unittest.main()
