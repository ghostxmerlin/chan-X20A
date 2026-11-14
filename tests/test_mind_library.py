import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind import settings
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
        v2 = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '乙'},
                          item_id=n['note_id'], expected_version=1, scope_id='office', now_ms=2)
        self.assertEqual(v2['origin'], 'owner')
        with self.assertRaises(ValueError):
            self.lib.restore('people_notes', n['note_id'], expected_version=2, restore_version=1, now_ms=3)

    def test_owner_edit_and_restore_over_fused_head_become_owner_versions(self):
        self.card(kind='owner')
        n = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '原版'},
                         item_id=None, expected_version=None, scope_id='office', now_ms=1)
        fused = {**n, 'version': 2, 'supersedes': 1, 'text': '融合', 'origin': 'fused', 'actor': 'worker',
                 'owner_original': {'text': '原版'}, 'derived_from': ['x'], 'fusion': {'run': 'r'}}
        self.db['people_notes'].docs.append(fused)
        r = self.lib.restore('people_notes', n['note_id'], expected_version=2, restore_version=1, now_ms=3)
        self.assertEqual((r['version'], r['text'], r['origin'], r['owner_original'], r['derived_from'], r['fusion']),
                         (3, '原版', 'owner', None, [], None))
        self.db['people_notes'].docs[2]['origin'] = 'fused'
        self.db['people_notes'].docs[2]['fusion'] = {'run': 'r'}
        e = self.lib.put('people_notes', {'person_id': 'owner', 'kind': 'trait', 'text': '再改'},
                         item_id=n['note_id'], expected_version=3, scope_id='office', now_ms=4)
        self.assertEqual((e['origin'], e['fusion'], e['owner_original'], e['derived_from']), ('owner', None, None, []))


class LibraryReadTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(); self.lib = Library(self.db)
        kw = dict(item_id=None, expected_version=None, scope_id='office')
        self.lib.put('people_cards', {'kind': 'owner', 'display': '主人'}, now_ms=100, **kw)
        self.jia = self.lib.put('people_cards', {'kind': 'regular', 'display': '人物甲'}, now_ms=200, **kw)['person_id']
        n1 = self.lib.put('people_notes', {'person_id': self.jia, 'kind': 'trait', 'text': '爱笑'}, now_ms=300, **kw)
        n2 = self.lib.put('people_notes', {'person_id': self.jia, 'kind': 'trait', 'text': '话少'}, now_ms=310, **kw)
        self.edited_note = n2['note_id']
        self.lib.put('people_notes', {'person_id': self.jia, 'kind': 'trait', 'text': '话不多'}, item_id=self.edited_note,
                     expected_version=1, scope_id='office', now_ms=320)
        self.climb = self.lib.put('knowledge_items', {'kind': 'commitment', 'name': '登山团体', 'subjects': ['owner']},
                                  now_ms=400, **kw)['kid']
        for title, ms, extra in (('第一次登山', 1000, {'topics': [self.climb]}), ('最近一次登山', 3000, {'topics': [self.climb]}),
                                 ('和甲吃饭', 2000, {'subjects': [self.jia]})):
            self.lib.put('memory_items', {'kind': 'event', 'title': title, 'statement': '说明',
                                          'when': {'type': 'point', 'start_ms': ms, 'end_ms': None}, **extra}, now_ms=500, **kw)

    def test_overview_counts_ids_not_versions(self):
        o = self.lib.overview(settings.load(None))
        self.assertEqual(o['collections']['people_notes']['total'], 2)
        self.assertEqual(o['collections']['people_notes']['by_origin'], {'owner': 2})
        self.assertEqual(o['voice_labels']['total'], 0)
        self.assertEqual(set(o['existing']), {'owner_voice_names', 'owner_face_names', 'settings_people', 'mind_memories'})

    def test_overview_counts_only_live_rows_of_current_scope(self):
        add = lambda lane, scope, mid, ver: self.db.add('mind_memories', {'lane': lane, 'scope_id': scope, 'memory_id': mid, 'version': ver})
        add('live', 'office', 'ma', 1); add('live', 'office', 'ma', 2); add('live', 'office', 'mb', 1)
        add('live', 'home', 'mc', 1); add('replay', 'office', 'md', 1)
        o = self.lib.overview(settings.load(None))
        self.assertEqual(o['existing']['mind_memories'], 2)

    def test_people_owner_first_with_note_counts(self):
        rows = self.lib.people()
        self.assertEqual(rows[0]['person_id'], 'owner')
        self.assertEqual(next(r for r in rows if r['person_id'] == self.jia)['notes'], 2)

    def test_person_view_gathers_notes_memories_knowledge(self):
        p = self.lib.person(self.jia)
        self.assertEqual(len(p['notes']), 2)
        self.assertEqual([m['title'] for m in p['memories']], ['和甲吃饭'])
        with self.assertRaises(KeyError):
            self.lib.person('p_' + '0' * 12)

    def test_memories_filters_and_paging(self):
        self.assertEqual(self.lib.memories(topic=self.climb)['total'], 2)
        self.assertEqual(self.lib.memories(person=self.jia)['total'], 1)
        self.assertEqual(self.lib.memories(limit=1)['items'][0]['title'], '最近一次登山')   # newest start_ms first
        with self.assertRaises(ValueError):
            self.lib.memories(limit=101)

    def test_q_is_plain_substring(self):
        self.assertEqual(self.lib.knowledge(q='登山')['total'], 1)
        self.assertEqual(self.lib.knowledge(q='.*')['total'], 0)
        self.assertEqual(self.lib.memories(q='$ne')['total'], 0)

    def test_item_history_owner_original_and_children(self):
        it = self.lib.item('people_notes', self.edited_note)
        self.assertEqual([v['version'] for v in it['versions']], [2, 1])
        self.assertEqual(it['owner_original']['version'], 2)
        self.assertEqual(len(self.lib.item('knowledge_items', self.climb)['children']), 2)
        with self.assertRaises(KeyError):
            self.lib.item('memory_items', 'e_' + '0' * 12)

    def test_reads_never_write(self):
        before = {c: len(self.db[c].docs) for c in ('people_cards', 'people_notes', 'memory_items', 'knowledge_items')}
        self.lib.overview(settings.load(None)); self.lib.people(); self.lib.person(self.jia)
        self.lib.memories(q='登山'); self.lib.knowledge(); self.lib.item('knowledge_items', self.climb)
        self.assertEqual(before, {c: len(self.db[c].docs) for c in before})


if __name__ == "__main__":
    unittest.main()
