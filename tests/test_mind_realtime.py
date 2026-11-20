"""围观 realtime spike (2026-11-14), mind half: the context block (mind/realtime.py, GET /realtime/context), the realtime
switch in billing (0 off, 9 on, mind start off) and a crowd tap that greets instead of stopping. FakeDB; nothing paid."""
import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0
from mind import api, chat as chat_mod, runner, speak
from mind import realtime as rt
from mind.settings_store import SettingsStore
from mind.store import LANE, Store
from test_mind_billing import FakeCtx
from mind.billing import Billing, apply_preset

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        s, v = self.ss.current()
        s = json.loads(json.dumps(s))
        s['owner'].update({'face_names': ['boss_face'], 'aliases': ['阿大'], 'display': '主人'})
        s['people'] = [{'display': '同事甲', 'voice_names': [], 'face_names': ['colleague_face']}]
        self.ss.save(s, 'test', expected_version=v)
        self.now = T0 + 30_000
        self.chat = chat_mod.Chat(self.db, self.store, settings_fn=lambda: self.ss.current()[0],
                                  roles_fn=lambda: speak.load_roles(str(ROOT / 'config/roles')), tts=None, env=None,
                                  clock_ms=lambda: self.now, log=lambda *a, **k: None, threaded=False)
        self.rc = rt.RealtimeContext(self.db, self.chat, lambda: self.ss.current()[0], clock_ms=lambda: self.now)

    def put_scene(self, minute):
        self.db['mind_scenes'].insert_one({'lane': LANE, 'minute': minute, 'version': 1, 'source': 'model', 'scene': {
            'summary': '几个人围着桌子聊天', 'people_doing': {'text': '大家在商量午饭'},
            'entries': [{'dimension': 'topic', 'text': '午饭吃面'}, {'dimension': 'sound_scene', 'text': '说笑声'}]}})

    def vision(self, at, event, face, name=None, session='v1'):
        doc = {'session': session, 'at': at, 'event': event, 'face': face}
        if name:
            doc['name'] = name
        self.db['vision'].insert_one(doc)


class ContextTest(Base):
    def test_block_has_scene_notes_people_and_owner(self):
        self.put_scene(T0)
        self.db['conversations'].insert_one({'start_ms': self.now - 60_000, 'end_ms': None, 'title': '午饭', 'notes': '想吃面'})
        self.vision(self.now - 50_000, 'face_named', 1, 'boss_face')
        self.vision(self.now - 40_000, 'face_named', 2, 'guest_face')
        self.vision(self.now - 30_000, 'face_named', 3, 'colleague_face')
        self.vision(self.now - 20_000, 'face_lost', 3)                 # gone again: not visible
        self.db['people_cards'].insert_one({'person_id': 'p_000000000001', 'version': 1, 'display': '客人小王',
                                            'links': {'voice_names': [], 'face_names': ['guest_face']},
                                            'treatment': {'tease': 'never'}, 'status': 'active'})
        self.db['people_notes'].insert_one({'note_id': 'n_000000000001', 'version': 1, 'person_id': 'p_000000000001',
                                            'text': '喜欢猫', 'status': 'active', 'changed_at': 1})
        self.db['eye_minutes'].insert_one({'minute': T0, 'version': 1, 'source': 'model', 'result': {
            'people_count': 2, 'people': [{'who': '主人', 'where': '桌前', 'doing': '笑'}], 'activity': '聊天'}})
        b = self.rc.block()
        t = b['text']
        for want in ('此刻的情景：大家在商量午饭', '在聊：午饭吃面', '声音：说笑声', '对话纪要：午饭：想吃面',
                     '看到的人（M12 认出）：客人小王、主人（阿大）', '画面：主人桌前笑。', '认识的人：客人小王（喜欢猫）。不要调侃他。'):
            self.assertIn(want, t)
        self.assertNotIn('同事甲', t)
        self.assertEqual(b['owner'], {'face_names': ['boss_face'], 'call': '阿大', 'voice_names': ['me']})   # 亲口算本人
        self.assertEqual(b['names'], {'colleague_face': '同事甲', 'guest_face': '客人小王'})
        self.assertLessEqual(len(t), rt.MAX_CHARS)

    def test_recent_timeline_notes_and_full_photo_line(self):
        for i, doing in enumerate(['主人在调电路', '主人在调电路', '同事来拿饭', '大家在聊午饭']):
            self.db['mind_scenes'].insert_one({'lane': LANE, 'minute': self.now - (20 - i) * 60_000, 'version': 1,
                                               'source': 'model', 'scene': {'people_doing': {'text': doing}}})
        self.db['mind_scenes'].insert_one({'lane': LANE, 'minute': self.now - 40 * 60_000, 'version': 1,
                                           'source': 'model', 'scene': {'people_doing': {'text': '太早了'}}})
        self.db['mind_scenes'].insert_one({'lane': 'replay', 'minute': self.now - 5 * 60_000, 'version': 1,
                                           'source': 'model', 'scene': {'people_doing': {'text': '别的通道'}}})
        for k, (title, notes) in enumerate([('调试', '在查舵机'), ('午饭', '想吃面')]):
            self.db['conversations'].insert_one({'start_ms': self.now - (50 - 20 * k) * 60_000, 'end_ms': self.now - 1,
                                                 'title': title, 'notes': notes})
        self.db['eye_minutes'].insert_one({'minute': self.now - 30_000, 'version': 1, 'source': 'model', 'result': {
            'people_count': 1, 'people': [], 'activity': '主人在看屏幕', 'scene': '办公桌前，台灯亮着',
            'changes': '有人从右边走过'}})
        t = self.rc.block()['text']
        line = next(x for x in t.splitlines() if x.startswith('最近的情景'))
        self.assertEqual(line.count('主人在调电路'), 1)                   # one line per change
        self.assertLess(line.index('主人在调电路'), line.index('大家在聊午饭'))
        self.assertNotIn('太早了', t)
        self.assertNotIn('别的通道', t)
        notes = next(x for x in t.splitlines() if x.startswith('最近的纪要'))
        self.assertLess(notes.index('午饭：想吃面'), notes.index('调试：在查舵机'))   # newest first
        self.assertIn('画面：主人在看屏幕。办公桌前，台灯亮着。这一分钟的变化：有人从右边走过。', t)

    def test_timeline_starts_at_the_scope(self):
        self.db['mind_scenes'].insert_one({'lane': LANE, 'minute': self.now - 20 * 60_000, 'version': 1,
                                           'source': 'model', 'scene': {'people_doing': {'text': '换地方以前'}}})
        self.db['mind_scenes'].insert_one({'lane': LANE, 'minute': self.now - 5 * 60_000, 'version': 1,
                                           'source': 'model', 'scene': {'people_doing': {'text': '换地方以后'}}})
        since = rt.speech_settings.scope(self.ss.current()[0]).get('since') or 0
        lines = rt.recent_scenes(self.db, LANE, self.now, 'Asia/Shanghai', since_ms=self.now - 10 * 60_000)
        self.assertEqual([x.split(' ', 1)[1] for x in lines], ['换地方以后'])
        self.assertGreaterEqual(len(rt.recent_scenes(self.db, LANE, self.now, 'Asia/Shanghai', since_ms=since)), 1)

    def test_empty_database_gives_time_only(self):
        b = self.rc.block()
        self.assertTrue(b['text'].startswith('现在：'))
        self.assertEqual((b['visible'], b['names']), ([], {'colleague_face': '同事甲'}))

    def test_burst_looks_line_newest_first_within_three_minutes(self):
        from mind import eye
        for at, text in ((self.now - 190_000, '太旧'), (self.now - 30_000, '一个人在倒水'), (self.now - 2_000, '主人在笑')):
            self.db['vision_looks'].insert_one({'kind': 'look', 'lid': str(at), 'at': at, 'text': text})
        line = next(x for x in self.rc.block()['text'].splitlines() if x.startswith('注视：'))
        self.assertLess(line.index('主人在笑'), line.index('一个人在倒水'))
        self.assertNotIn('太旧', line)
        self.assertEqual(len(eye.recent_looks(self.db, self.now, n=1)), 1)

    def test_old_faces_and_stale_scene_are_left_out(self):
        self.put_scene(self.now - 10 * 60_000)
        self.vision(self.now - rt.VISIBLE_MS - 1000, 'face_named', 1, 'boss_face')
        self.assertNotIn('看到的人', self.rc.block()['text'])
        self.assertNotIn('此刻的情景', self.rc.block()['text'])

    def test_vision_off_clears_that_session(self):
        self.vision(self.now - 5000, 'face_named', 1, 'boss_face')
        self.vision(self.now - 4000, 'vision_off', None)
        self.assertEqual(rt.visible_faces(self.db, self.now), [])

    def test_a_carried_name_is_not_a_recognition(self):
        # security review + owner 2026-11-15: the ear's guessed (carried) name never counts as 认出
        self.vision(self.now - 5000, 'face_carried', 2, 'boss_face')
        self.assertEqual(rt.visible_faces(self.db, self.now), [])
        self.assertNotIn('看到的人', self.rc.block()['text'])

    def test_memories_at_most_three(self):
        self.rc._cache['memories'] = (self.rc.mono(), ['a1', 'b2', 'c3', 'd4'])
        self.assertIn('你记得（只在相关时提）：a1；b2；c3\n', self.rc.block()['text'] + '\n')

    def test_a_failing_part_is_left_out(self):
        self.chat._load_scene = lambda now: (_ for _ in ()).throw(RuntimeError('db'))
        self.assertTrue(self.rc.block()['text'].startswith('现在：'))


class EndpointTest(Base):
    def test_get_realtime_context_over_http(self):
        self.vision(self.now - 1000, 'face_named', 1, 'boss_face')
        self.chat.clock_ms = lambda: self.now
        server = api.make_server(self.store, 'k', port=0, ctx={'settings_store': self.ss, 'chat': self.chat})
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/realtime/context',
                                     headers={'Authorization': 'Bearer k'})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.load(r)
        self.assertEqual(set(body), {'text', 'owner', 'names', 'visible', 'at'})
        self.assertEqual(body['owner']['face_names'], ['boss_face'])
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/switches',
                                     headers={'Authorization': 'Bearer k'})
        with urllib.request.urlopen(req, timeout=5) as r:
            sw = json.load(r)
        self.assertEqual(sw['realtime'], {'on': False, 'at': None})          # default off
        self.assertFalse(sw['realtime_smart_turn']['on'])                    # 智能断句: no document = off (owner 2026-11-20)
        self.assertFalse(sw['realtime_intent']['on'])                        # 工具分类器: likewise
        self.assertTrue(sw['realtime_answer_all']['on'])                     # 点屏幕聊天: on (owner: 暂时退回全手动)
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/switches', method='POST',
                                     data=json.dumps({'name': 'realtime_smart_turn', 'on': False}).encode(),
                                     headers={'Authorization': 'Bearer k', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertFalse(json.load(r)['realtime_smart_turn']['on'])


class BillingRealtimeTest(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx(mode='crowd')
        self.b = Billing(self.ctx)

    def test_zero_off_nine_on_and_the_power_on_nine_too(self):
        self.ctx.realtime = True
        self.b.off('page', 1)
        self.assertFalse(self.ctx.realtime)
        apply_preset(9, self.b, source='page', now_ms=2)
        self.assertTrue(self.ctx.realtime)
        apply_preset(9, self.b, source='asr', now_ms=3)
        self.assertTrue(self.ctx.realtime)
        self.b.off('page', 5)
        apply_preset(9, self.b, source='boot', now_ms=6)         # 0.5.21: the robot's power-on 智能 turns it on too
        self.assertTrue(self.ctx.realtime)
        self.assertTrue(self.ctx.tingwu)


class CrowdTapTest(unittest.TestCase):
    def test_a_tap_with_stop_speech_stops_in_crowd_too(self):     # owner 2026-11-19 (was: crowd tap = greet)
        db, store = FakeDB(), Store(FakeDB())
        ss = SettingsStore(db, '/nonexistent.json')
        stops = []

        class Tts:
            def stop(self):
                stops.append(1)
                return []
        logs = []
        ms, _, _ = runner.wire_modes(db, store, settings_store=ss, tts=Tts(), say_worker=None, say_auto=None,
                                     say_round_fn=None, log=lambda *a, **k: logs.append(' '.join(map(str, a))))
        rep = lambda seq, mode, inp: ms.report({'seq': seq, 'mode': mode, 'dir': None, 'host': None, 'return_to': None,
                                                'by': 'touch', 'input': inp, 'effects': ['stop_speech']},
                                               boot='b1', conn=1)
        rep(1, 'crowd', 'select')
        n = len(stops)
        rep(2, 'crowd', 'tap')                                   # hello was speaking: her rules attach stop_speech
        self.assertGreater(len(stops), n)


if __name__ == '__main__':
    unittest.main()
