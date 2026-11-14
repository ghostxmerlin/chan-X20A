"""Long-term memory phase A: library write path, extraction job, recall pool, reads, API (placeholder text only)."""
import copy
import json
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, tingwu
from mind import api, chat as chat_mod, memory_extract as mx, recall, speech_context
from mind.library import Library, LibraryConflict, derived_id, extract_key, recall_row
from mind.settings_store import SettingsStore
from mind.store import Store

ENV = {'base_url': 'http://x', 'api_key': 'k'}
DAY = '2026-11-10'          # T0 is 2026-11-10 16:40 +08:00
REFS = [{'c': 'conversations', 'key': {'cid': 'c1'}, 'version': 2},
        {'c': 'speech', 'key': {'session': 'S', 'id': 'a'}, 'pos': [16000, 32000]}]


def fields(**kw):
    f = {'kind': 'event', 'title': '标题甲', 'statement': '内容甲', 'when': {'type': 'point', 'start_ms': T0, 'end_ms': None},
         'derived_from': copy.deepcopy(REFS), 'people': [{'id': 'owner', 'role': 'about'}], 'confidence': 'high',
         'basis': 'reported', 'salience': 2, 'sensitivity': [], 'emotion': None}
    f.update(kw)
    return f


def put(lib, lane='live', key='k1', **kw):
    if 'derived_from' not in kw:          # each key its own sentence: distinct memories do not share sources
        kw['derived_from'] = [REFS[0], {'c': 'speech', 'key': {'session': 'S', 'id': key}, 'pos': [16000, 32000]}]
    return lib.put_derived(fields(**kw), lane=lane, key=key, run={'job_id': 'j', 'run_id': 'r', 'model': 'm',
                                                                  'prompt_version': 'p', 'at': T0},
                           now_ms=T0, scope_id='office', day=DAY)


class LibraryWritePathTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.lib = Library(self.db)

    def test_created_with_provenance_lane_and_a_stable_id(self):
        action, rec = put(self.lib)
        self.assertEqual(action, 'created')
        self.assertEqual(rec['memory_id'], derived_id('live', 'k1'))
        self.assertRegex(rec['memory_id'], r'^e_[a-f0-9]{12}$')
        for k, v in (('origin', 'extracted'), ('actor', 'worker'), ('lane', 'live'), ('day', DAY), ('extract_key', 'k1'),
                     ('status', 'active'), ('confidence', 'high'), ('salience', 2), ('subjects', ['owner'])):
            self.assertEqual(rec[k], v, k)
        self.assertEqual(rec['derived_from'][1]['pos'], [16000, 32000])
        row = self.db['memory_recall'].find_one({'memory_id': rec['memory_id']})
        self.assertTrue(row['recallable'] and row['auto'])
        self.assertEqual(row['person_ids'], ['owner'])

    def test_rerun_is_idempotent(self):
        put(self.lib)
        self.assertEqual(put(self.lib)[0], 'unchanged')
        self.assertEqual(self.db.count('memory_items'), 1)
        action, rec = put(self.lib, statement='内容乙')
        self.assertEqual((action, rec['version']), ('updated', 2))
        self.assertEqual(self.db.count('memory_recall'), 1)

    def test_owner_disabled_stays_disabled_and_owner_edits_are_never_overwritten(self):
        _, rec = put(self.lib)
        self.lib.review(rec['memory_id'], 1, 'wrong', now_ms=T0 + 1)
        action, head = put(self.lib, statement='新的说法')
        self.assertEqual((action, head['status'], head['version']), ('blocked', 'disabled', 2))
        _, rec2 = put(self.lib, key='k2')
        edited = self.lib.put('memory_items', {'kind': 'event', 'title': '主人改的', 'statement': '主人的说法', 'subjects': ['owner'],
                                               'when': {'type': 'point', 'start_ms': T0}},
                              item_id=rec2['memory_id'], expected_version=1, scope_id='office', now_ms=T0 + 2)
        self.assertEqual((edited['origin'], edited['actor'], edited['lane'], edited['extract_key']), ('owner', 'owner', 'live', 'k2'))
        self.assertEqual(edited['derived_from'], rec2['derived_from'])
        self.assertEqual(put(self.lib, key='k2', statement='又一种说法')[0], 'blocked')
        self.assertEqual(self.lib.head('memory_items', rec2['memory_id'])['statement'], '主人的说法')
        self.assertEqual(self.db.count('memory_items'), 4)          # nothing deleted, nothing added by the blocked runs

    def test_same_as_merges_across_days_and_follows_merged_into(self):
        _, a = put(self.lib, key='ka')
        _, b = put(self.lib, key='kb', title='标题乙')
        f = fields(statement='合并后的说法', when={'type': 'point', 'start_ms': T0 + 86_400_000, 'end_ms': None},
                   derived_from=[{'c': 'chat_turns', 'key': {'tid': 't1'}}])
        action, rec = self.lib.put_derived(f, lane='live', key='kc', run=None, now_ms=T0 + 5, scope_id='office', day='2026-11-11',
                                           target_id=a['memory_id'])
        self.assertEqual((action, rec['memory_id'], rec['day']), ('updated', a['memory_id'], DAY))
        self.assertEqual(rec['when']['type'], 'span')
        self.assertEqual(len(rec['derived_from']), 3)
        self.assertIn('kc', rec['extract_keys'])
        self.assertEqual(self.lib.find_by_key('live', 'kc')['memory_id'], a['memory_id'])
        self.lib.review(b['memory_id'], 1, 'merge', into=a['memory_id'], now_ms=T0 + 6)
        self.assertEqual(self.lib.head('memory_items', b['memory_id'])['merged_into'], a['memory_id'])
        action, rec = put(self.lib, key='kb', title='标题乙', statement='再说一次')
        self.assertEqual((action, rec['memory_id']), ('updated', a['memory_id']))     # into the merge target
        self.assertFalse(self.db['memory_recall'].find_one({'memory_id': b['memory_id']})['recallable'])

    def test_review_rules(self):
        _, a = put(self.lib, key='ka')
        _, r = put(self.lib, lane='replay', key='ka')
        with self.assertRaises(LibraryConflict):
            self.lib.review(a['memory_id'], 9, 'confirm', now_ms=T0)
        for into in (a['memory_id'], r['memory_id'], 'e_000000000000', 'x'):
            with self.assertRaises(ValueError):
                self.lib.review(a['memory_id'], 1, 'merge', into=into, now_ms=T0)
        with self.assertRaises(ValueError):
            self.lib.review(a['memory_id'], 1, 'delete', now_ms=T0)
        c = self.lib.review(a['memory_id'], 1, 'confirm', now_ms=T0)
        self.assertEqual((c['actor'], c['owner_confirmed'], c['version']), ('owner', True, 2))
        self.assertEqual(put(self.lib, key='ka', statement='变了')[0], 'blocked')

    def test_refs_people_and_fields_are_validated(self):
        bad = [dict(derived_from=[{'c': 'sessions_all', 'key': {'x': 1}}]), dict(derived_from=[{'c': 'speech', 'key': {'session': 'S'}}]),
               dict(derived_from=[{'c': 'recording', 'key': {'session': 'S'}}]),
               dict(derived_from=[{'c': 'speech', 'key': {'session': 'S', 'id': 'a'}, 'pos': [5, 1]}]), dict(derived_from=[]),
               dict(people=[{'id': '$ne', 'role': 'about'}]), dict(people=[{'id': 'owner', 'role': 'boss'}]),
               dict(sensitivity=['secret']), dict(salience=7), dict(confidence='sure'), dict(emotion={'label': 'x', 'basis': 'said'})]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                put(self.lib, **b)
        ok = [{'c': c, 'key': k} for c, k in (('mind_scenes', {'lane': 'live', 'minute': T0}), ('mind_segments', {'lane': 'live', 'start': T0}),
                                              ('eye_minutes', {'minute': T0}), ('chat_turns', {'tid': 't1'}))]
        ok.append({'c': 'recording', 'key': {'session': 'S'}, 'pos': [0, 16000]})
        self.assertEqual(put(self.lib, derived_from=ok)[0], 'created')
        self.assertEqual(self.db.count('memory_items'), 1)

    def test_shared_sentences_block_a_reworded_copy_of_a_disabled_memory(self):
        refs = [{'c': 'speech', 'key': {'session': 'S', 'id': i}} for i in ('x1', 'x2')]
        _, a = put(self.lib, key='ka', derived_from=refs)
        self.lib.review(a['memory_id'], 1, 'wrong', now_ms=T0)
        for kind in ('event', 'told'):
            self.assertEqual(put(self.lib, key='kz' + kind, kind=kind, title='全新的标题', derived_from=refs[:1])[0], 'blocked')
        _, b = put(self.lib, key='kb', derived_from=[{'c': 'speech', 'key': {'session': 'S', 'id': 'y1'}}])
        action, rec = put(self.lib, key='kc', title='又一个标题', statement='更完整的说法',
                          derived_from=[{'c': 'speech', 'key': {'session': 'S', 'id': 'y1'}}, {'c': 'chat_turns', 'key': {'tid': 't9'}}])
        self.assertEqual((action, rec['memory_id']), ('updated', b['memory_id']))      # half its sentences: the same
        self.assertEqual(self.db.count('memory_recall'), 2)

    def test_recall_sync_never_goes_back_a_version(self):
        _, rec = put(self.lib)
        _, rec2 = put(self.lib, statement='第二版')
        self.assertFalse(self.lib._sync_recall(rec))
        self.assertEqual(self.db['memory_recall'].docs[0]['version'], 2)
        self.db['memory_recall'].docs[0].update(version=1, statement='旧的')
        self.lib._resync(rec['memory_id'])
        self.assertEqual(self.db['memory_recall'].docs[0]['statement'], '第二版')

    def test_low_confidence_is_stored_but_not_recallable(self):
        _, rec = put(self.lib, confidence='low')
        self.assertFalse(recall_row(rec)['recallable'])

    def test_extract_key_ignores_ref_order_and_title_punctuation(self):
        a = extract_key('event', REFS, '标题，甲')
        self.assertEqual(a, extract_key('event', list(reversed(REFS)), '标题甲'))
        self.assertNotEqual(a, extract_key('told', REFS, '标题甲'))


class LibraryReadPathTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.lib = Library(self.db)

    def test_memories_read_the_recall_copy_with_lanes_paging_and_people(self):
        for i in range(25):
            put(self.lib, key=f'k{i}', title=f'标题{i}', when={'type': 'point', 'start_ms': T0 + i, 'end_ms': None},
                people=[{'id': 'unknown', 'role': 'speaker', 'name_text': '称呼甲'}] if i == 3 else [{'id': 'owner', 'role': 'about'}])
        put(self.lib, lane='replay', key='r1')
        page = self.lib.memories(offset=20, limit=20)
        self.assertEqual((page['total'], len(page['items'])), (25, 5))
        self.assertEqual(self.lib.memories(limit=1)['items'][0]['title'], '标题24')       # newest first
        self.assertEqual(self.lib.memories(lane='replay')['total'], 1)
        self.assertEqual(self.lib.memories(person='owner')['total'], 24)
        self.assertEqual(self.lib.memories(q='标题3')['total'], 1)
        self.assertEqual(self.lib.overview({'speech': None})['collections']['memory_items']['total'], 25)

    def test_rebuild_recall_from_memory_items(self):
        _, rec = put(self.lib)
        put(self.lib, statement='第二版')
        self.db['memory_recall'].docs.clear()
        self.assertEqual(self.lib.rebuild_recall(), 1)
        self.assertEqual(self.db['memory_recall'].docs[0]['version'], 2)

    def test_changes_and_sources(self):
        self.db.add('conversations', {'cid': 'c1', 'start_ms': T0, 'end_ms': T0 + 60_000, 'title': '纪要标题', 'notes': '纪要内容', 'version': 2})
        self.db.add('speech', speech('S', 'a', T0 + 1000, '原话甲', pos=[16000, 32000]))
        _, rec = put(self.lib, derived_from=copy.deepcopy(REFS))
        self.lib.put('memory_items', {'kind': 'event', 'title': '手写', 'statement': '手写的', 'when': {'type': 'point', 'start_ms': T0}},
                     item_id=None, expected_version=None, scope_id='office', now_ms=T0)
        ch = self.lib.changes()
        self.assertEqual([r['memory_id'] for r in ch['items']], [rec['memory_id']])
        src = self.lib.sources(rec['memory_id'])['sources']
        self.assertEqual((src[0]['kind'], src[0]['title']), ('对话纪要', '纪要标题'))
        self.assertEqual((src[1]['text'], src[1]['session'], src[1]['pos']), ('原话甲', 'S', [16000, 32000]))


# ---------------------------------------------------------------- the extraction job ----
class ExtractModel:
    """A fake luna: answers from the material it is given (first conversation + its first sentence)."""

    def __init__(self, items=None):
        self.calls, self.items, self.fail = [], items, False

    def __call__(self, base, key, body, timeout=90):
        self.calls.append(copy.deepcopy(body))
        if self.fail:
            return None, 100, 'TimeoutError: timed out'
        text = body['messages'][-1]['content']
        if self.items is not None:
            items = self.items(text) if callable(self.items) else self.items
        else:
            items = [item(evidence=[tok(text, 'c'), tok(text, 's')])]
        return {'choices': [{'message': {'content': json.dumps({'items': items}, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 10000, 'completion_tokens': 1000}}, 900, None


def tok(text, prefix, n=0):
    """The n-th material id with this prefix in a prompt (block codes: c3fa9, s3fa9.2, g81bc, t0d2e.1, o77aa)."""
    return re.findall(rf'(?<![\w.])({prefix}[0-9a-f]{{4,}}(?:\.\d+)?)(?= )', text)[n]


def item(**kw):
    x = {'same_as': '', 'kind': 'event', 'title': '一件事', 'statement': '主人说起一件事', 'evidence': [],
         'people': [{'id': 'owner', 'role': 'about', 'name_text': ''}, {'id': 'unknown', 'role': 'mentioned', 'name_text': '称呼乙'}],
         'confidence': 'high', 'basis': 'reported', 'salience': 2, 'sensitivity': [], 'emotion': ''}
    x.update(kw)
    return x


class _JobBase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB().add('sessions', session('S', T0 - 3600_000))
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent/settings.json')
        self.s = self.ss.current()[0]
        self.model = ExtractModel()
        self.now = [T0 + 2 * 86_400_000]
        self.material()

    def material(self):
        self.db.add('speech', speech('S', 'a', T0 + 1000, '我们周末去爬山吧', band='known', name='me', pos=[16000, 48000]),
                    speech('S', 'b', T0 + 5000, '好啊几点出发', pos=[64000, 96000]),
                    speech('S', 'c', T0 + 3600_000, '这是一段没有纪要的话吧', pos=[1, 2]),
                    speech('S', 'd', T0 + 3600_000 + 2000, '对的没有纪要', pos=[3, 4]))
        self.db.add('tingwu', tingwu('S', 1, T0 + 1500, '听悟的版本'))
        self.db.add('conversations', {'cid': 'c1', 'start_ms': T0, 'end_ms': T0 + 60_000, 'last_ms': T0 + 5000, 'version': 3,
                                      'title': '周末安排', 'notes': '两人约周末爬山', 'confidence': 'high',
                                      'refs': [['speech', 'S', 'a'], ['speech', 'S', 'b'], ['tingwu', 'S', 1]]})
        self.db.add('mind_segments', {'lane': 'live', 'start': T0, 'end': T0 + 600_000, 'version': 1,
                                      'summary': {'title': '段标题旧', 'text': '旧', 'confidence': 'mid'}},
                    {'lane': 'live', 'start': T0, 'end': T0 + 600_000, 'version': 2,
                     'summary': {'title': '段标题', 'text': '在工位聊天', 'confidence': 'mid'}})
        self.db.add('chat_turns', {'tid': 't1', 'at': T0 + 7200_000, 'text': '你记得周末的事吗', 'reply': '记得', 'state': 'answered',
                                   'skip': None, 'speaker': {'band': 'unknown'}},
                    {'tid': 't2', 'at': T0 + 7200_500, 'text': '回声', 'state': 'skipped', 'skip': 'hello_voice'})
        self.db.add('utterances', {'uid': 'u1', 'at': T0 + 3000, 'text': '这是hello自己说过的一句台词'})
        self.db.add('speech', speech('S', 'e', T0 + 4000, '这是hello自己说过的一句台词'))

    def run_day(self, lane='live', mode='backfill', **kw):
        return mx.run_day(self.db, self.store, self.s, ENV, DAY, lane=lane, mode=mode, now_fn=lambda: self.now[0],
                          chat=self.model, **kw)


class ExtractJobTest(_JobBase):
    def test_material_groups_sentences_and_leaves_out_tingwu_and_hello(self):
        m = mx.gather(self.db, self.s, DAY)
        text = '\n'.join(x for b in m['blocks'] for x in b['lines'])
        self.assertEqual([b['kind'] for b in m['blocks']], ['conv', 'segment', 'raw', 'chat'])
        for want in ('周末安排', '我们周末去爬山吧', '主人：', '在工位聊天', '没有纪要的一段话', '你记得周末的事吗', 'hello 答：记得'):
            self.assertIn(want, text)
        for never in ('听悟的版本', 'hello自己说过', '回声', '段标题旧'):
            self.assertNotIn(never, text)

    def test_extracts_with_refs_and_is_idempotent(self):
        x = self.run_day()
        self.assertEqual((x['state'], x['calls'], x['counts']['created']), ('done', 1, 1))
        self.assertEqual(len(self.model.calls), 1)
        body = self.model.calls[0]
        self.assertEqual((body['model'], body['reasoning_effort']), ('gpt-6-luna', 'low'))
        rec = self.db['memory_items'].docs[0]
        cs = {r['c'] for r in rec['derived_from']}
        self.assertEqual(cs, {'conversations', 'speech'})
        sp = next(r for r in rec['derived_from'] if r['c'] == 'speech')
        self.assertEqual((sp['key'], sp['pos']), ({'session': 'S', 'id': 'a'}, [16000, 48000]))
        self.assertIn({'id': 'unknown', 'role': 'mentioned', 'name_text': '称呼乙'}, rec['people'])
        self.assertIn({'id': 'owner', 'role': 'speaker'}, rec['people'])          # from the voiceprint
        self.assertEqual((rec['lane'], rec['day'], rec['run']['model']), ('live', DAY, 'gpt-6-luna'))
        again = self.run_day()
        self.assertEqual((again['calls'], again['skipped'], again['blocks']), (0, 4, 4))
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.db.count('memory_items'), 1)

    def test_changed_input_reruns_and_maps_to_the_same_memory(self):
        self.run_day()
        self.db['conversations'].docs[0]['notes'] = '两人约周末爬山，定了时间'
        x = self.run_day()
        self.assertEqual(x['calls'], 1)
        self.assertEqual(x['counts']['unchanged'], 1)
        self.assertEqual(self.db.count('memory_items'), 1)

    def test_disabled_stays_disabled_on_a_rerun(self):
        self.run_day()
        rec = self.db['memory_items'].docs[0]
        Library(self.db).review(rec['memory_id'], 1, 'wrong', now_ms=self.now[0])
        self.db['conversations'].docs[0]['title'] = '周末安排二'
        self.model.items = lambda text: [item(evidence=[tok(text, 'c'), tok(text, 's')], statement='又说了一遍', title='换了说法的标题')]
        x = self.run_day()
        self.assertEqual(x['counts']['blocked'], 1)
        self.assertIn('主人否定过', self.model.calls[-1]['messages'][-1]['content'])
        self.assertEqual(Library(self.db).head('memory_items', rec['memory_id'])['status'], 'disabled')
        self.assertEqual(self.db.count('memory_recall'), 1)

    def test_replay_lane_never_touches_live(self):
        self.run_day(lane='replay')
        self.assertEqual(self.db['memory_items'].docs[0]['lane'], 'replay')
        self.assertEqual(Library(self.db).memories()['total'], 0)
        self.assertEqual(recall.library_rows(self.db), [])
        self.assertTrue(all(r['lane'] == 'replay' for r in self.db['mind_runs'].docs))
        self.run_day(lane='live')
        self.assertEqual(self.db.count('memory_items'), 2)
        with self.assertRaises(ValueError):
            self.run_day(lane='replay', mode='daily')

    def test_ungrounded_items_are_dropped_one_by_one(self):
        self.model.items = lambda text: [item(evidence=['c99']), item(evidence=['o1']), item(evidence=[]),
                                         item(evidence=[tok(text, 's')], people=[{'id': 'p_000000000000', 'role': 'about', 'name_text': ''}]),
                                         item(evidence=[tok(text, 'g')], kind='weird')]
        x = self.run_day()
        self.assertEqual((x['counts']['created'], x['counts']['dropped']), (1, 4))
        self.assertEqual(self.db['memory_items'].docs[0]['people'], [{'id': 'owner', 'role': 'speaker'}])

    def test_child_and_others_flags_and_low_confidence(self):
        self.model.items = lambda text: [item(evidence=[tok(text, 's')], sensitivity=['child'], title='孩子的事'),
                                         item(evidence=[tok(text, 'g')], confidence='low', title='听不清的事')]
        x = self.run_day()
        self.assertEqual((x['flags'], x['counts']['low']), ({'child': 1}, 1))
        rows = {r['title']: r for r in self.db['memory_recall'].docs}
        self.assertFalse(rows['听不清的事']['recallable'])
        self.assertEqual(rows['孩子的事']['sensitivity'], ['child'])

    def test_caps_and_switch_stop_before_any_call(self):
        self.db['switches'].docs.append({'_id': 'memory_extract', 'on': False})
        self.assertEqual(self.run_day()['reason'], 'switch_off')
        self.db['switches'].docs.clear()
        self.db['library_jobs'].docs.append({'_id': 'spend:backfill', 'usd': mx.BACKFILL_CAP_USD})
        self.assertEqual(self.run_day()['reason'], 'capped')
        self.assertEqual(self.model.calls, [])
        today = mx.local_day(self.now[0], self.s['tz'])
        self.db['library_jobs'].docs.append({'_id': f'spend:daily:{today}', 'usd': mx.DAILY_CAP_USD})
        self.assertEqual(self.run_day(mode='daily')['reason'], 'capped')
        self.store.set_state(enabled=False)
        self.assertEqual(mx.gate(self.db, self.store, self.s, ENV, 'daily', self.now[0]), 'mind_off')
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.db.count('memory_items'), 0)

    def test_spend_is_recorded_and_the_cap_is_enforced_across_chunks(self):
        old = mx.MAX_CHUNK_CHARS
        mx.MAX_CHUNK_CHARS = 60                               # one block per chunk
        self.addCleanup(setattr, mx, 'MAX_CHUNK_CHARS', old)
        self.model.items = []
        per_call = (10000 * 0.1 + 1000 * 0.5) / 1_000_000
        self.db['library_jobs'].docs.append({'_id': 'spend:backfill', 'usd': mx.BACKFILL_CAP_USD - per_call * 1.5})
        x = self.run_day()
        self.assertEqual((x['calls'], x['state'], x['reason']), (2, 'stopped', 'capped'))
        self.assertGreaterEqual(mx.spent(self.db, 'backfill', ''), mx.BACKFILL_CAP_USD)

    def test_a_paid_answer_switched_off_meanwhile_is_kept_and_written_later_without_a_new_call(self):
        def answer(text):
            self.db['switches'].update_one({'_id': 'memory_extract'}, {'$set': {'on': False}}, upsert=True)
            return [item(evidence=[tok(text, 's')])]
        self.model.items = answer
        x = self.run_day()
        self.assertEqual((x['state'], x['reason'], self.db.count('memory_items')), ('stopped', 'switch_off', 0))
        self.db['switches'].update_one({'_id': 'memory_extract'}, {'$set': {'on': True}})
        x = self.run_day()
        self.assertEqual((x['calls'], x['counts']['created'], len(self.model.calls)), (0, 1, 1))
        job = self.db['library_jobs'].find_one({'_id': f'extract:live:{DAY}'})
        self.assertTrue(all(c.get('result') is None for c in job['chunks'].values()))     # no text kept once written

    def test_model_error_marks_the_job_for_retry(self):
        self.model.fail = True
        x = self.run_day()
        self.assertEqual(x['state'], 'error')
        self.assertGreater(self.db['library_jobs'].find_one({'_id': f'extract:live:{DAY}'})['next_retry'], self.now[0])

    def test_same_as_from_the_prompt_merges_into_an_existing_memory(self):
        self.run_day()
        mid = self.db['memory_items'].docs[0]['memory_id']
        self.db['conversations'].docs[0]['notes'] = '变了'
        self.model.items = lambda text: [item(evidence=[tok(text, 'c')], same_as=mid, title='完全不同的标题', statement='合并说法')]
        x = self.run_day()
        self.assertEqual(x['counts']['updated'], 1)
        self.assertEqual(self.db.count('memory_recall'), 1)
        self.assertEqual(Library(self.db).head('memory_items', mid)['statement'], '合并说法')

    def test_backfill_prints_counts_never_text(self):
        logs = []
        t = mx.backfill(self.db, self.store, self.s, ENV, [DAY, '2026-11-11'], lane='replay', now_fn=lambda: self.now[0],
                        chat=self.model, log=logs.append)
        self.assertEqual((t['state'], t['days'], t['counts']['created']), ('done', 2, 1))
        out = '\n'.join(logs)
        for secret in ('爬山', '一件事', '主人说起', '称呼乙', '周末'):
            self.assertNotIn(secret, out)
        self.assertIn('created 1', out)
        logs.clear()
        mx.backfill(self.db, self.store, self.s, ENV, [DAY], lane='replay', now_fn=lambda: self.now[0], chat=self.model,
                    log=logs.append, dry_run=True)
        self.assertIn('dry run', logs[0])
        self.assertNotIn('爬山', logs[0])
        self.assertEqual(len(self.model.calls), 1)

    def test_backfill_command_refuses_live_without_the_flag_before_connecting(self):
        for argv, why in ((['--lane', 'live'], 'yes-live'), (['--to', '2026-11-01'], 'from <= to')):
            args = ['backfill', '--from', DAY, '--to', DAY, '--hubmix-env', '/nonexistent']
            if argv[0] == '--to':
                args[4] = argv[1]
            else:
                args += argv
            with self.assertRaises(SystemExit) as e:
                mx.main(args)
            self.assertIn(why, str(e.exception))


class ReviewFixesJobTest(_JobBase):
    """Review of f7a59ac: stable block fingerprints, never revive, attempts, whole day, leases."""

    def test_an_earlier_conversation_costs_one_call_and_revives_nothing(self):
        self.run_day()
        mid = self.db['memory_items'].docs[0]['memory_id']
        Library(self.db).review(mid, 1, 'wrong', now_ms=self.now[0])
        calls = len(self.model.calls)
        self.db.add('speech', speech('S', 'x0', T0 - 600_000, '更早的一段话里说起别的事'))
        self.db.add('conversations', {'cid': 'c0', 'start_ms': T0 - 600_000, 'end_ms': T0 - 540_000, 'last_ms': T0 - 600_000,
                                      'version': 1, 'title': '更早的事', 'notes': '说起别的', 'confidence': 'mid',
                                      'refs': [['speech', 'S', 'x0']]})
        # the model, seeing the old sentences again, would re-extract the disabled memory under a new wording
        self.model.items = lambda text: [item(evidence=[tok(text, 'c'), tok(text, 's')], title='另一种说法完全不同')]
        x = self.run_day()
        self.assertEqual(len(self.model.calls) - calls, 1)                     # only the new conversation's block
        sent = self.model.calls[-1]['messages'][-1]['content']
        self.assertIn('更早的一段话', sent)
        self.assertNotIn('我们周末去爬山吧', sent)
        self.assertEqual(x['counts']['created'], 1)
        self.assertEqual(Library(self.db).head('memory_items', mid)['status'], 'disabled')
        # and the old block, sent again after its notes change, cannot bring the disabled memory back
        self.db['conversations'].docs[0]['notes'] = '两人约周末爬山，改了时间'
        self.model.items = lambda text: [item(evidence=[tok(text, 'c'), tok(text, 's')], kind='told', title='完全换了的标题')]
        x = self.run_day()
        self.assertEqual((x['counts']['blocked'], x['counts']['created']), (1, 0))
        self.assertEqual(sum(1 for r in self.db['memory_recall'].docs if r['status'] == 'active'), 1)

    def test_block_text_does_not_depend_on_other_blocks(self):
        before = {b['fp'] for b in mx.gather(self.db, self.s, DAY)['blocks']}
        self.db.add('mind_segments', {'lane': 'live', 'start': T0 - 3600_000, 'end': T0 - 3000_000, 'version': 1,
                                      'summary': {'title': '更早的段', 'text': '更早', 'confidence': 'mid'}})
        after = {b['fp'] for b in mx.gather(self.db, self.s, DAY)['blocks']}
        self.assertEqual(len(after - before), 1)
        self.assertTrue(before <= after)

    def test_a_failing_chunk_is_given_up_after_three_attempts(self):
        self.model.fail = True
        for i in range(mx.MAX_ATTEMPTS - 1):
            self.assertEqual(self.run_day()['state'], 'error')
        x = self.run_day()
        self.assertEqual((x['state'], x['counts']['gave_up']), ('done', 1))
        job = self.db['library_jobs'].find_one({'_id': f'extract:live:{DAY}'})
        self.assertTrue(all(v['state'] == 'gave_up' for v in job['blocks'].values()))
        self.assertEqual(self.run_day()['calls'], 0)

    def test_whole_day_only_an_hour_after_midnight(self):
        lo, hi = mx.day_range(DAY, self.s['tz'])
        self.model.items = []
        self.now[0] = hi + 30 * 60_000
        self.run_day()
        self.assertFalse(self.db['library_jobs'].find_one({'_id': f'extract:live:{DAY}'})['whole_day'])
        self.now[0] = hi + 2 * 3600_000
        self.db['conversations'].docs[0]['notes'] = '改了'
        self.run_day()
        self.assertTrue(self.db['library_jobs'].find_one({'_id': f'extract:live:{DAY}'})['whole_day'])

    def test_backfill_renews_the_lease_per_chunk(self):
        old = mx.MAX_CHUNK_CHARS
        mx.MAX_CHUNK_CHARS = 60
        self.addCleanup(setattr, mx, 'MAX_CHUNK_CHARS', old)
        self.model.items = []
        seen = []
        real = mx._lease
        self.addCleanup(setattr, mx, '_lease', real)
        mx._lease = lambda db, owner, now: seen.append(now) or real(db, owner, now)
        mx.backfill(self.db, self.store, self.s, ENV, [DAY], lane='replay', now_fn=lambda: self.now[0], chat=self.model,
                    log=lambda m: None)
        self.assertEqual(len(seen), 1 + 4)                                      # take + once per chunk (4 blocks)

    def test_on_demand_only_today_or_from_the_daily_start(self):
        self.db['library_jobs'].docs.append({'_id': 'config:extract', 'daily_since': '2026-11-12'})
        now = T0 + 3 * 86_400_000                                               # 2026-11-13
        with self.assertRaises(ValueError):
            mx.request_run(self.db, self.s, now, '2026-11-11')
        self.assertEqual(mx.request_run(self.db, self.s, now, '2026-11-12')['state'], 'pending')
        self.assertEqual(mx.request_run(self.db, self.s, now)['day'], '2026-11-13')


class DailyTickTest(_JobBase):
    def tick(self, now):
        return mx.tick(self.db, self.store, self.ss, ENV, now, chat=self.model, now_fn=lambda: now)

    def test_daily_starts_at_the_deploy_day_and_waits_for_a_quiet_room(self):
        self.assertEqual(self.tick(T0 + 3 * 3600_000)['state'], 'idle')          # first tick: daily_since = today
        self.assertEqual(self.model.calls, [])
        nxt = T0 + 86_400_000
        self.db.add('speech', speech('S', 'z', nxt - 1000, '现在有人在说话'))
        self.assertEqual(self.tick(nxt)['state'], 'room_busy')
        r = self.tick(nxt + mx.IDLE_MS + 1)
        self.assertEqual((r['state'], r['day']), ('done', DAY))
        self.assertEqual(self.tick(nxt + mx.IDLE_MS + 2)['state'], 'idle')

    def test_on_demand_request_runs_at_once(self):
        self.tick(T0)
        mx.request_run(self.db, self.s, T0 + 1000)
        r = self.tick(T0 + 2000)
        self.assertEqual((r['state'], r['day']), ('done', DAY))
        self.assertEqual(self.db['library_jobs'].find_one({'_id': 'request:live'})['state'], 'done')
        calls = len(self.model.calls)
        nxt = T0 + 86_400_000 + mx.IDLE_MS + 10      # the next day: the partial day is run again; unchanged chunks are free
        r = self.tick(nxt)
        self.assertEqual((r['state'], r['day'], r['calls']), ('done', DAY, 0))
        self.assertEqual(len(self.model.calls), calls)
        self.assertEqual(self.tick(nxt + 1)['state'], 'idle')
        with self.assertRaises(ValueError):
            mx.request_run(self.db, self.s, T0, '2099-01-01')

    def test_a_day_in_backoff_does_not_stall_the_next(self):
        self.tick(T0)
        self.db['library_jobs'].update_one({'_id': 'config:extract'}, {'$set': {'daily_since': '2026-11-09'}})
        now = T0 + 86_400_000 + mx.IDLE_MS + 10
        self.db['library_jobs'].docs.append({'_id': 'extract:live:2026-11-09', 'state': 'error', 'next_retry': now + 1000})
        r = self.tick(now)
        self.assertEqual((r['state'], r['day']), ('done', DAY))

    def test_learning_off_means_no_daily_run(self):
        self.s['memory']['learning'] = False
        self.ss.current = lambda: (self.s, 1)
        mx.request_run(self.db, self.s, T0)
        self.assertEqual(self.tick(T0 + 1)['state'], 'learning_off')
        self.assertEqual(self.model.calls, [])


# ---------------------------------------------------------------- reads: chat, talk show, realtime ----
class RecallPoolTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.lib = Library(self.db)
        self.s = SettingsStore(self.db, '/nonexistent/settings.json').current()[0]

    def add(self, key, statement, **kw):
        return put(self.lib, key=key, statement=statement, title=kw.pop('title', '标题' + key), **kw)[1]

    def test_pool_merges_habits_first_then_library_and_skips_low_off_and_replay(self):
        self.db.add('mind_memories', {'lane': 'live', 'scope_id': 'office', 'memory_id': 'm1', 'version': 1, 'status': 'known',
                                      'statement': '习惯甲', 'kind': 'habit', 'manual': True, 'last_seen': T0})
        self.add('a', '记忆甲')
        self.add('b', '记忆乙', confidence='low')
        off = self.add('c', '记忆丙')
        self.lib.review(off['memory_id'], 1, 'wrong', now_ms=T0)
        put(self.lib, lane='replay', key='d', statement='试跑的')
        pool = recall.load_pool(self.db, self.s, T0)
        self.assertEqual([(m['source'], m['text']) for m in pool], [('habit', '习惯甲'), ('library', '记忆甲')])
        self.s['memory']['apply'] = False
        self.assertEqual(recall.load_pool(self.db, self.s, T0), [])

    def test_chat_picks_at_most_three_related_and_the_speaker_first(self):
        for i in range(6):
            self.add(f'k{i}', f'爬山计划第{i}次')
        self.add('p', '爬山计划访客', people=[{'id': 'p_0123456789ab', 'role': 'speaker'}], salience=0)
        pool = recall.load_pool(self.db, self.s, T0)
        got = chat_mod.related_memories(pool, '周末爬山吗', person_id='p_0123456789ab')
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]['text'], '爬山计划访客')
        self.assertEqual(chat_mod.related_memories(pool, '今天吃什么'), [])

    def test_prompt_text_marks_child_and_others(self):
        self.add('c', '孩子的考试', sensitivity=['child'])
        self.add('o', '同事说的事', sensitivity=['others_said'])
        pool = {m['text']: m for m in recall.load_pool(self.db, self.s, T0)}
        self.assertIn('别开玩笑', recall.prompt_text(pool['孩子的考试']))
        self.assertIn('只提意思', recall.prompt_text(pool['同事说的事']))
        self.assertEqual(recall.plain(list(pool.values()), crowd=True), [])

    def test_talk_show_takes_one_related_never_child_and_cools_down(self):
        self.add('c', '孩子要考试了', sensitivity=['child'], salience=3)
        self.add('a', '下周考试复习', salience=1)
        self.add('b', '考试成绩出来', salience=2)
        rows = recall.library_rows(self.db)
        got = recall.pick_for_talk(self.db, rows, ['明天考试'], T0)
        self.assertEqual([m['statement'] for m in got], ['考试成绩出来'])
        recall.note_uses(self.db, got, 'talk', T0)
        self.assertEqual([m['statement'] for m in recall.pick_for_talk(self.db, rows, ['明天考试'], T0 + 1)], ['下周考试复习'])
        recall.note_uses(self.db, recall.pick_for_talk(self.db, rows, ['明天考试'], T0 + 1), 'talk', T0 + 1)
        self.assertEqual(recall.pick_for_talk(self.db, rows, ['明天考试'], T0 + 2), [])
        self.assertEqual(len(recall.pick_for_talk(self.db, rows, ['明天考试'], T0 + recall.TALK_COOLDOWN_MS + 2)), 1)

    def test_talk_show_lines_carry_the_usage_note(self):
        self.add('u', '考试的安排变了', people=[{'id': 'unknown', 'role': 'speaker'}])
        got = recall.pick_for_talk(self.db, recall.library_rows(self.db), ['考试'], T0)
        self.assertEqual(len(got), 1)
        self.assertIn('只接他说的话，不评价他本人', got[0]['statement'])
        self.assertIn('只提意思', got[0]['statement'])

    def test_realtime_crowd_gets_habit_memories_only(self):
        self.db.add('mind_memories', {'lane': 'live', 'scope_id': 'office', 'memory_id': 'm1', 'version': 1, 'status': 'known',
                                      'statement': '习惯甲', 'kind': 'habit', 'manual': True, 'last_seen': T0})
        self.add('w', '下周的工作计划')
        pool = recall.load_pool(self.db, self.s, T0)
        self.assertEqual(recall.plain(pool, crowd=True), ['习惯甲'])
        self.assertEqual(len(recall.plain(pool)), 2)

    def test_private_memories_reach_chat_only_for_the_owner(self):
        self.add('h', '主人最近身体不太舒服', sensitivity=['private'])
        self.add('k', '孩子最近身体很好', sensitivity=['child'])
        pool = recall.load_pool(self.db, self.s, T0)
        ask = {'text': '你主人最近身体怎么样'}
        for person in (None, {'person_id': 'p_0123456789ab', 'display': '访客甲'}):
            system = chat_mod.messages({'memories': pool, 'person': person}, ask, [], {'max_chars': 40}, None)[0]['content']
            self.assertNotIn('不太舒服', system)
            self.assertIn('孩子最近身体很好', system)                      # child stays (annotated)
        system = chat_mod.messages({'memories': pool, 'person': {'person_id': 'owner', 'display': '主人'}}, ask, [],
                                   {'max_chars': 40}, None)[0]['content']
        self.assertIn('不太舒服', system)

    def test_talk_show_respects_the_person_card(self):
        card = self.lib.put('people_cards', {'kind': 'regular', 'display': '人物甲', 'treatment': {'tease': 'never', 'quote': 'gist'}},
                            item_id=None, expected_version=None, scope_id='office', now_ms=T0)
        self.add('x', '人物甲说考试的事', people=[{'id': card['person_id'], 'role': 'speaker'}])
        self.assertEqual(recall.pick_for_talk(self.db, recall.library_rows(self.db), ['考试'], T0), [])

    def test_speech_context_adds_one_library_memory_to_the_round(self):
        self.add('a', '周末爬山的计划')
        self.add('c', '孩子周末爬山', sensitivity=['child'], salience=3)
        db, store = self.db, Store(self.db)
        db.add('sessions', session('S', T0 - 600_000))
        db.add('speech', speech('S', 'n', T0 - 10_000, '周末去爬山吗'))
        ctx = speech_context.build(db, store, self.s, T0)
        lib = [m for m in ctx['memories'] if m.get('source') == 'library']
        self.assertEqual([m['statement'] for m in lib], ['周末爬山的计划'])

    def test_chat_prompt_reads_library_memories_within_the_limits(self):
        self.add('a', '周末爬山的计划')
        self.add('c', '孩子周末爬山', sensitivity=['child'])
        pool = recall.load_pool(self.db, self.s, T0)
        msgs = chat_mod.messages({'memories': pool}, {'text': '周末爬山去不去'}, [], {'max_chars': 40}, None)
        system = msgs[0]['content']
        self.assertIn('周末爬山的计划', system)
        self.assertIn('孩子周末爬山（孩子的事：别开玩笑，别照原话说）', system)


# ---------------------------------------------------------------- API ----
class ExtractApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.server = api.make_server(Store(self.db), 'k', port=0, ctx={'settings_store': SettingsStore(self.db, '/nonexistent.json')})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.lib = Library(self.db)

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

    def test_review_changes_sources_switch_and_run(self):
        _, a = put(self.lib, key='a')
        _, b = put(self.lib, key='b', title='标题乙')
        st, page = self.call('/db/changes?lane=live')
        self.assertEqual((st, page['total']), (200, 2))
        self.assertEqual(self.call('/db/changes?lane=nope')[0], 400)
        self.assertEqual(self.call('/db/sources?id=' + a['memory_id'])[0], 200)
        self.assertEqual(self.call('/db/sources?id=p_0123456789ab')[0], 400)
        st, r = self.call('/db/memory/review', {'id': a['memory_id'], 'expected_version': 1, 'action': 'confirm'})
        self.assertEqual((st, r['actor']), (200, 'owner'))
        self.assertEqual(self.call('/db/memory/review', {'id': a['memory_id'], 'expected_version': 1, 'action': 'wrong'})[0], 409)
        self.assertEqual(self.call('/db/memory/review', {'id': b['memory_id'], 'expected_version': 1, 'action': 'merge',
                                                         'into': a['memory_id']})[0], 200)
        self.assertEqual(self.call('/db/memory/review', {'id': b['memory_id'], 'expected_version': 2, 'action': 'delete'})[0], 400)
        self.assertEqual(self.call('/db/memory/review', {'id': b['memory_id'], 'expected_version': 2, 'action': 'wrong', 'x': 1})[0], 400)
        st, x = self.call('/db/extract')
        self.assertEqual((st, x['on'], x['daily_cap_usd'], x['backfill_cap_usd']), (200, True, 0.2, 2.0))
        st, x = self.call('/db/extract/switch', {'on': False})
        self.assertEqual((st, x['on']), (200, False))
        self.assertEqual(self.call('/db/extract/switch', {'on': 'yes'})[0], 400)
        st, x = self.call('/db/extract/run', {})
        self.assertEqual((st, x['requested']['state']), (200, 'pending'))
        self.assertEqual(self.call('/db/extract/run', {'day': '11-14'})[0], 400)
        self.assertEqual(self.db.count('memory_items'), 4)


if __name__ == '__main__':
    unittest.main()
