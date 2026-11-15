"""Where hello is now (owner 2026-11-15, mind/place.py; spec docs/superpowers/specs/2026-11-15-place-contract.md): one
current place (free text or unknown, no default), the common choices, the local "我们现在在饭店" rule, the ask line, the
mind API, and every prompt that reads it. FakeDB, fake model: nothing is paid."""
import copy
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0                     # noqa: E402
from mind import api, eye, memory_extract, place, prompts, runner, settings, speech_context  # noqa: E402
from mind import realtime as rt                           # noqa: E402
from mind.records import Records                          # noqa: E402
from mind.store import Store                              # noqa: E402
from test_mind_chat import ChatBase, heard                # noqa: E402

MIN = 60_000
OFFICE_WORDS = ('办公桌', '工位', '默认地点')


class ParseTest(unittest.TestCase):
    def test_sentences_that_say_where_we_are(self):
        ch = place.DEFAULT_CHOICES
        cases = {'我们现在在饭店': '饭店', '我们到家了': '家里', 'hello，这里是湖边': '湖边', '现在在湖边呢': '湖边',
                 '你在车上': '车上', '咱们现在在车里': '车上', '我在开车': '车上', '我们在饭店吃饭': '饭店',
                 '我在公司开会': '公司', '我在上海': '上海', '我们在一家火锅店': '火锅店', '我们在会议室': '会议室',
                 '我们在湖边散步': '湖边', '我在机场等飞机': '机场'}
        for text, want in cases.items():
            self.assertEqual(place.parse_told(text, ch), want, text)

    def test_sentences_that_do_not(self):
        for text in ('我在想事情', '我昨天在饭店', '你在哪儿', '我们在开会', '我在看海', '他在饭店', '我在这里', '我在上班',
                     '这是我的手机', '我们在饭店吗', '如果我们在饭店', '', None, '我们去饭店'):
            self.assertIsNone(place.parse_told(text, place.DEFAULT_CHOICES), text)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()

    def test_unknown_until_set_and_null_sets_unknown(self):
        st = place.state(self.db, T0)
        self.assertEqual((st['current'], st['by'], st['choices']), (None, None, place.DEFAULT_CHOICES))
        place.set_current(self.db, ' 饭店 ', 'page', T0)
        self.assertEqual(place.state(self.db, T0 + 1)['current'], '饭店')
        place.set_current(self.db, None, 'robot', T0 + 10)
        st = place.state(self.db, T0 + 20)
        self.assertEqual((st['current'], st['by'], st['at']), (None, 'robot', T0 + 10))

    def test_robot_pick_must_be_a_choice_and_labels_are_checked(self):
        with self.assertRaises(ValueError):
            place.set_current(self.db, '湖边', 'robot', T0)
        place.set_current(self.db, '车上', 'robot', T0)
        for bad in ('', 'x' * 41, 'a\nb', 3):
            with self.assertRaises(ValueError):
                place.set_current(self.db, bad, 'page', T0)
        with self.assertRaises(ValueError):
            place.set_current(self.db, '家里', 'mind', T0)

    def test_page_rows_keep_their_old_shape(self):
        place.set_current(self.db, '家里', 'page', T0)
        place.set_current(self.db, '饭店', 'voice', T0 + 1, heard='我们现在在饭店')
        rows = self.db['mind_labels'].docs
        self.assertNotIn('by', rows[0])
        self.assertEqual((rows[1]['by'], rows[1]['heard']), ('voice', '我们现在在饭店'))

    def test_choices_limits(self):
        place.edit_choices(self.db, {'add': '湖边'}, T0)
        self.assertEqual(place.choices(self.db)[-1], '湖边')
        place.edit_choices(self.db, {'remove': '办公室'}, T0 + 1)
        self.assertNotIn('办公室', place.choices(self.db))
        with self.assertRaises(ValueError):
            place.edit_choices(self.db, {'add': '一二三四五六七八九'}, T0)          # 27 bytes > 24
        with self.assertRaises(ValueError):
            place.edit_choices(self.db, {'choices': [str(i) for i in range(9)]}, T0)
        st = place.edit_choices(self.db, {'choices': ['一二三四五六七八', '车上', '车上']}, T0 + 2)   # 24 bytes is fine
        self.assertEqual(st['choices'], ['一二三四五六七八', '车上'])
        self.assertEqual(st['rev'], T0 + 2)

    def test_told_changes_once(self):
        r = place.told(self.db, '我们现在在饭店', 'chat', T0)
        self.assertEqual((r['changed'], r['state']['current'], r['state']['by']), (True, '饭店', 'voice'))
        self.assertFalse(place.told(self.db, '我们在饭店呢', 'crowd', T0 + 1)['changed'])
        self.assertFalse(place.told(self.db, '今天天气不错', 'crowd', T0 + 2)['changed'])
        self.assertEqual(len(self.db['mind_labels'].docs), 1)

    def test_lines(self):
        self.assertIn('不知道', place.line(place.current(self.db, T0), 'Asia/Shanghai'))
        self.assertIn('不要假定在办公室或桌上', place.line(place.current(self.db, T0), 'Asia/Shanghai'))
        place.set_current(self.db, '车上', 'robot', T0)
        self.assertEqual(place.line(place.current(self.db, T0), 'Asia/Shanghai'),
                         '现在的地点：车上（主人在 hello 身上选的，16:40 起，以此为准）')
        place.set_current(self.db, None, 'page', T0 + MIN)
        self.assertIn('16:41 设成了未知', place.line(place.current(self.db, T0 + MIN), 'Asia/Shanghai'))

    def test_ask_line_when_unknown_at_most_once_per_gap(self):
        cur = place.current(self.db, T0)
        self.assertIn('问一句现在在哪儿', place.ask_line(self.db, cur, '', T0))
        self.assertIsNone(place.ask_line(self.db, cur, '', T0 + 5 * MIN))
        self.assertIsNotNone(place.ask_line(self.db, cur, '', T0 + 11 * MIN))

    def test_ask_line_when_the_picture_looks_like_another_kind_of_place(self):
        place.set_current(self.db, '办公室', 'page', T0)
        cur = place.current(self.db, T0)
        self.assertIsNone(place.ask_line(self.db, cur, '办公室工位', T0))
        self.assertIsNone(place.ask_line(self.db, cur, '看不出', T0))
        self.assertIsNone(place.ask_line(self.db, cur, '', T0))
        line = place.ask_line(self.db, cur, '车里', T0)
        self.assertIn('画面看着像车里', line)
        self.assertTrue(place.looks_changed('饭店', '室外街边'))
        self.assertFalse(place.looks_changed('湖边', '室外'))
        self.assertFalse(place.looks_changed('上海', '车里'))        # an unknown kind never claims a change

    def test_latest_seen_from_the_eye(self):
        self.db['eye_minutes'].insert_one({'minute': T0, 'version': 1, 'source': 'model', 'result': {'place_seen': '车里'}})
        self.assertEqual(place.latest_seen(self.db, T0 + 30_000), '车里')
        self.assertEqual(place.latest_seen(self.db, T0 + 10 * MIN), '')

    def test_self_facts_lose_the_old_desk_wording(self):
        old = {'self_facts': '我叫 hello，是主人放在办公桌上的桌面宠物机器人。我有一个麦克风。'}
        self.assertEqual(place.self_facts(old), '我叫 hello，是主人的桌面宠物机器人。我有一个麦克风。')
        self.assertNotIn('办公桌', settings.DEFAULTS['self_facts'])
        self.assertEqual(settings.DEFAULTS['place']['default'], '')


class PromptsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.s = settings.load(None)

    def test_runner_carries_unknown_and_by(self):
        db = self.db
        db.add('mind_labels', {'lane': 'live', 'kind': 'place', 'label': '饭店', 'at': T0 - MIN, 'by': 'voice'})
        db.add('mind_labels', {'lane': 'live', 'kind': 'place', 'label': None, 'at': T0 + 2 * MIN})
        store = Store(db)
        rec = Records(db)
        runner.process_minute(rec, store, T0, self.s, T0 + 10 * MIN)
        runner.process_minute(rec, store, T0 + 3 * MIN, self.s, T0 + 10 * MIN)
        self.assertEqual(store.latest('live', T0)['strong']['place'],
                         {'label': '饭店', 'source': 'owner_report', 'at': T0 - MIN, 'by': 'voice'})
        later = store.latest('live', T0 + 3 * MIN)
        self.assertEqual(later['strong']['place'], {'label': None, 'source': 'owner_report', 'at': T0 + 2 * MIN})
        u = prompts.render('v3', self.s, later['package'], later['strong'], None, [], {})[1]['content']
        self.assertIn('rule:place 地点：不知道（16:42 设成了未知；不要假定在办公室或桌上）', u)
        u0 = prompts.render('v3', self.s, store.latest('live', T0)['package'], store.latest('live', T0)['strong'], None, [], {})[1]['content']
        self.assertIn('rule:place 地点：饭店（有人跟 hello 说的，16:39，以此为准）', u0)

    def test_no_prompt_pins_the_office(self):
        for text in (prompts.V3_SYSTEM, eye.SYSTEM, memory_extract.SYSTEM):
            for w in ('办公桌', '工位桌'):
                self.assertNotIn(w, text)
        from mind import notes, say_prompts, scene_speech, attention, chat
        for text in (notes.NOTES_SYSTEM, say_prompts._BASE, say_prompts.JUDGE_RULES, say_prompts.CHECK_RULES,
                     scene_speech.RULES, scene_speech.RULES_V1, attention.PROMPT):
            for w in OFFICE_WORDS + ('桌上的小机器人',):
                self.assertNotIn(w, text)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'ear'))
        import realtime_link
        self.assertNotIn('办公桌', realtime_link.PERSONA)
        self.assertIn('现在的地点', realtime_link.PERSONA)
        roles = Path(__file__).resolve().parents[1] / 'config' / 'roles'
        for p in roles.glob('*.json'):
            self.assertNotIn('办公室', p.read_text(encoding='utf-8'))
            self.assertNotIn('桌面机器人', p.read_text(encoding='utf-8'))

    def test_eye_never_compares_to_an_assumed_place(self):
        s = copy.deepcopy(self.s)
        s['speech']['profile'] = 'home'
        m = eye.messages_for(s, T0, {'frames': [], 'events': []}, None)
        self.assertEqual(m[0]['content'], eye.SYSTEM)
        self.assertIn('不要拿画面跟某个地方比较', eye.SYSTEM)
        self.assertIn('place_seen', eye.EYE_SCHEMA['required'])
        ok = {'people_count': 0, 'people': [], 'activity': '', 'scene': '车窗外是街道', 'changes': '', 'confidence': 'mid'}
        out, err = eye._parse({'choices': [{'message': {'content': json.dumps(ok)}}]})   # place_seen missing: still usable
        self.assertIsNone(err)

    def test_speech_context_has_the_place(self):
        store = Store(self.db)
        ctx = speech_context.build(self.db, store, self.s, T0)
        self.assertEqual(ctx['place'], place.UNKNOWN_TEXT)
        place.set_current(self.db, '湖边', 'page', T0 - MIN)
        self.assertEqual(speech_context.build(self.db, store, self.s, T0)['place'][:8], '现在的地点：湖边')
        from mind import scene_speech, say_prompts
        role = {'id': 'deadpan', 'persona': 'p', 'dialect': '普通话', 'voice': 'wuxian'}
        sys_text = scene_speech.messages(role, {**ctx, 'recent': []}, 40, T0)[0]['content']
        self.assertIn('【地点】现在的地点：不知道', sys_text)
        self.assertIn('【地点】', say_prompts.judge_messages(ctx, T0, [])[0]['content'] + say_prompts._messages('x', ctx, {})[0]['content'])

    def test_memory_extract_gets_the_days_places(self):
        lo, hi = memory_extract.day_range('2026-11-10', 'Asia/Shanghai')
        self.db.add('mind_labels', {'lane': 'live', 'kind': 'place', 'label': '办公室', 'at': lo - 3600_000})
        self.db.add('mind_labels', {'lane': 'live', 'kind': 'place', 'label': '车上', 'at': lo + 18 * 3600_000, 'by': 'robot'})
        self.assertEqual(place.day_lines(self.db, lo, hi, 'Asia/Shanghai'), ['00:00 办公室', '18:00 车上'])
        material = memory_extract.gather(self.db, self.s, '2026-11-10')
        msgs = memory_extract.messages(self.s, material, {'lines': []}, [])
        self.assertIn('这一天的地点：00:00 办公室；18:00 车上', msgs[1]['content'])


class ChatPlaceTest(ChatBase):
    def test_told_in_chat_sets_the_place_and_the_reply_knows_it(self):
        r = self.chat.heard(heard('hello，我们现在在饭店'))
        self.assertTrue(r['accepted'])
        self.assertEqual(place.current(self.db)['label'], '饭店')
        self.chat.process_once()
        system = self.model.bodies[-1]['messages'][0]['content']
        self.assertIn('现在的地点：饭店（有人跟 hello 说的', system)
        self.assertNotIn('\n【可以问地点】', system)          # known and nothing looks different: no question

    def test_unknown_place_lets_hello_ask_once(self):
        self.chat.heard(heard('今天中午吃什么好呢', n=1))
        self.chat.process_once()
        first = self.model.bodies[-1]['messages'][0]['content']
        self.assertIn('现在的地点：不知道', first)
        self.assertIn('\n【可以问地点】', first)
        self.now[0] += 20_000
        self.chat.heard(heard('那就吃面吧', n=2))
        self.chat.process_once()
        self.assertNotIn('\n【可以问地点】', self.model.bodies[-1]['messages'][0]['content'])

    def test_far_or_own_voice_never_sets_the_place(self):
        self.chat.heard(heard('我们现在在饭店', n=1, loud_db=2.0, voice={'p': 0.5, 'band': 'voice', 'loud_db': 2.0}))
        self.chat.heard(heard('我们现在在饭店', n=2, self={'sounding': True, 'moving': False}))
        self.assertIsNone(place.current(self.db)['label'])

    def test_crowd_block_has_the_place(self):
        rc = rt.RealtimeContext(self.db, self.chat, lambda: self.ss.current()[0], clock_ms=lambda: self.now[0])
        text = rc.block()['text']
        self.assertIn('现在的地点：不知道', text)
        self.assertIn('【可以问地点】', text)
        self.assertNotIn('【可以问地点】', rc.block()['text'])     # at most once per 10 min


class ApiPlaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = FakeDB()
        cls.store = Store(cls.db)
        cls.server = api.make_server(cls.store, 'k', '127.0.0.1', 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def req(self, path, body=None):
        r = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', method='POST' if body is not None else 'GET',
                                   data=json.dumps(body).encode() if body is not None else None)
        r.add_header('Authorization', 'Bearer k')
        if body is not None:
            r.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b'{}')

    def test_get_set_unknown_choices_and_told(self):
        status, st = self.req('/place')
        self.assertEqual((status, st['current'], st['choices']), (200, None, place.DEFAULT_CHOICES))
        status, st = self.req('/place', {'label': '湖边'})                       # the page, as before
        self.assertEqual((status, st['ok'], st['current'], st['by']), (200, True, '湖边', 'page'))
        self.assertIsInstance(st['at'], int)
        self.assertEqual(self.req('/place', {'label': '湖边', 'source': 'robot'})[0], 400)   # not a choice
        self.assertEqual(self.req('/place', {'label': None, 'source': 'robot'})[1]['current'], None)
        self.assertEqual(self.req('/place', {'label': '车上', 'source': 'voice'})[0], 400)
        self.assertEqual(self.req('/place', {})[0], 400)
        self.assertEqual(self.req('/place/choices', {'add': '湖边'})[1]['choices'][-1], '湖边')
        self.assertEqual(self.req('/place/choices', {'add': 'x' * 25})[0], 400)
        status, r = self.req('/place/told', {'text': '我们到湖边了', 'source': 'crowd'})
        self.assertEqual((status, r['changed'], r['state']['current'], r['state']['by']), (200, True, '湖边', 'voice'))
        self.assertEqual(self.req('/place/told', {'text': 'x', 'source': 'page'})[0], 400)


if __name__ == '__main__':
    unittest.main()
