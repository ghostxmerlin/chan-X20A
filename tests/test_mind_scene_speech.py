import json
import unittest
from unittest.mock import patch
from test_mind_speech_v2 import SpeechCase, ENV, ROOT, FakeChat, FakeTts
from mind import speak


class SceneSpeechTest(SpeechCase):
    def scene(self, age=60000, **kw):
        self.db.add('mind_scenes', {'lane':'live','minute':self.now-age,'version':1,
            'source':'model','scene':{'people_doing':{'text':'大家讨论软件的交付时间','confidence':'high'},
            'summary':'客户要求明天交付'},**kw})

    def test_current_page_scene_is_in_context(self):
        self.scene()
        self.assertIn('大家讨论',self.context()['scene']['text'])

    def test_stale_carried_and_previous_scope_scenes_are_not_refreshed(self):
        self.scene(carried_from=self.now-900000,source='carried',last_call_minute=self.now-900000)
        self.assertIsNone(self.context().get('scene'))
        self.db['mind_scenes'].docs.clear();self.scene()
        self.s['speech']['scope_since']=self.now-30000
        self.assertIsNone(self.context().get('scene'))

    def test_scene_built_with_current_disabled_sources_remains_available(self):
        import copy
        self.s['sources']['sounds']=False
        self.db.add('mind_settings',{'lane':'live','version':4,'settings':copy.deepcopy(self.s)})
        self.scene(settings_version=4)
        self.assertIsNotNone(self.context().get('scene'))
        self.s['sources']['vision']=False
        self.assertIsNone(self.context().get('scene'))

    def test_scene_alone_generates_once_and_forwards_dialect(self):
        self.scene();fake=FakeChat();fake.reply_next(json.dumps({'text':'活路翻倍，钱没涨，造孽哦。'}))
        tts=FakeTts()
        with patch.object(speak,'CHAT',fake):
            rec=speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now,wait=True)
        self.assertEqual(len(fake.calls),1)
        self.assertIn('大家讨论',fake.calls[0]['messages'][1]['content'])
        self.assertEqual(fake.calls[0]['reasoning_effort'],'none')
        self.assertEqual(fake.calls[0]['model'],'gpt-6.1-terra')
        self.assertEqual([r['step'] for r in self.db['mind_runs'].docs],['say'])
        self.assertIn('四川话',tts.said[0][1]['instruction'])
        self.assertIn('server_ms',rec)

    def test_invalid_or_repeated_output_is_not_retried_or_truncated(self):
        self.scene()
        for text in ('长'*70,'以前说过的话'):
            with self.subTest(text=text):
                self.db.add('utterances',{'at':self.now-1000,'role':'sad','state':'done','text':'以前说过的话'})
                fake=FakeChat();fake.reply_next(json.dumps({'text':text}));tts=FakeTts()
                with patch.object(speak,'CHAT',fake),self.assertRaises(RuntimeError):
                    speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now)
                self.assertEqual(len(fake.calls),1);self.assertEqual(tts.said,[])

    def test_stop_during_single_generation_does_not_play(self):
        self.scene();tts=FakeTts();fake=FakeChat();fake.reply_next(json.dumps({'text':'先吃饭，吃完再改。'}))
        def stop(*a,**k):
            response=fake(*a,**k);speak.cancel_pending();return response
        with patch.object(speak,'CHAT',stop),self.assertRaises(speak.NotConnected):
            speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now)
        self.assertEqual(tts.said,[])


class SceneSayV2Test(SpeechCase):
    def scene(self):
        self.db.add('mind_scenes', {'lane':'live','minute':self.now-60000,'version':1,'source':'model',
            'scene':{'people_doing':{'text':'大家讨论软件的交付时间','confidence':'high'},'summary':'客户要求明天交付'}})

    def say(self, reply):
        fake=FakeChat();fake.reply_next(json.dumps(reply,ensure_ascii=False));tts=FakeTts()
        with patch.object(speak,'CHAT',fake):
            rec=speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'worst',tts,self.now,wait=True)
        return rec,fake.calls[0]

    def test_point_and_device_are_kept_and_rotated(self):
        self.scene()
        rec,call=self.say({'point':'明天交付','device':'最坏预言','text':'明天交付？后天怕是要量产咯。'})
        self.assertEqual((rec['focus'],rec['joke_type'],rec['prompt']),('明天交付','最坏预言','scene-say-v2'))
        self.assertEqual(call['response_format']['json_schema']['schema']['required'],['point','device','text'])
        self.db['utterances'].docs[-1]['state']='done'
        _,call=self.say({'point':'交付','device':'顺推','text':'交付完了是不是还要包售后。'})
        payload=json.loads(call['messages'][1]['content'])
        self.assertEqual(payload['最近用过的手法'],['最坏预言'])
        self.assertIn('不是同事',call['messages'][0]['content'])
        self.assertIn('乌鸦嘴。（本角色擅长）',call['messages'][0]['content'])

    def test_unknown_device_is_not_stored(self):
        self.scene()
        rec,_=self.say({'point':'x','device':'乱写','text':'明天交付？后天怕是要量产咯。'})
        self.assertIsNone(rec['joke_type'])


class OverusedTest(unittest.TestCase):
    def test_repeated_fragments_except_live_topic_words(self):
        from mind import scene_speech
        recent=[{'text':t} for t in ('我申请留在桌上。','板子要申请羽绒服了。','花听了都想请假','喝口水都要请假哦','精心呵护还要保姆')]
        out=scene_speech.overused({'recent':recent,'evidence':[{'text':'精心呵护，天天浇水'}]})
        self.assertEqual(set(out),{'申请','请假'})

    def test_evidence_is_newest_first_with_relative_time(self):
        from mind import scene_speech
        ctx={'time':'11-12 18:00','scene':None,'memories':[],'recent':[],'environment':'','profile':{},
             'evidence':[{'kind':'speech','at':1000,'text':'旧话','subject_id':'owner','quality':'usable'},
                         {'kind':'speech','at':55000,'text':'新话','speaker':None,'quality':'uncertain','asr_conflict':True,
                          'alternatives':[{'text':'新话'},{'text':'心化'}]}]}
        p=json.loads(scene_speech.messages({'id':'sad','persona':'x'},ctx,40,60000)[1]['content'])
        self.assertEqual([e['内容'] for e in p['实时输入']],['新话','旧话'])
        self.assertEqual(p['实时输入'][0]['时间'],'5秒前')
        self.assertEqual(p['实时输入'][0]['另一种听法'],['心化'])
        self.assertEqual(p['实时输入'][1]['谁说的'],'主人')
