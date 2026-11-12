import importlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, tingwu
from mind import settings, selfvoice, speak
from mind.store import Store
from test_mind_fusion import FakeChat
from test_mind_speak import FakeTts

ROOT = Path(__file__).resolve().parents[1]
ENV = {'base_url': 'http://x', 'api_key': 'k'}


class SpeechCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('mind.speech_context'), 'v2 context is missing')
        self.ctxmod = importlib.import_module('mind.speech_context')
        self.db = FakeDB().add('sessions', session('S', T0-300_000))
        self.store, self.s, self.now = Store(self.db), settings.load(None), T0
        selfvoice.set_spoken([])
        self.addCleanup(lambda: selfvoice.set_spoken([]))

    def context(self):
        return self.ctxmod.build(self.db, self.store, self.s, self.now)

    def heard(self, id=1, age=5000, text='客户改成下周交付', **kw):
        row = speech('S', id, self.now-age, text, **kw)
        self.db.add('speech', row)
        return row


class SpeechContextTest(SpeechCase):

    def eye(self, minute_age, frame_age, confidence, version=1):
        self.db.add('eye_minutes', {'minute':self.now-minute_age, 'version':version, 'source':'model',
            'frames':[{'at':self.now-frame_age}],
            'result':{'activity':'有人走到桌边', 'changes':'有人进入', 'confidence':confidence}})

    def test_unclear_latest_visual_keeps_previous_fresh_clear_observation(self):
        self.eye(60_000, 20_000, 'low')
        self.eye(120_000, 95_000, 'mid')
        rows = self.context()['evidence']
        self.assertEqual([r['quality'] for r in rows], ['uncertain', 'usable'])
        self.assertEqual(rows[1]['at'], self.now-95_000)
        self.assertEqual(rows[1]['refs'][0]['minute'], self.now-120_000)

    def test_visual_fallback_does_not_revive_superseded_stale_or_previous_scope(self):
        self.eye(60_000, 20_000, 'high', version=1)
        self.eye(60_000, 20_000, 'low', version=2)
        self.eye(180_000, 121_000, 'high')
        self.assertFalse(any(e['quality']=='usable' for e in self.context()['evidence']))
        self.eye(120_000, 95_000, 'mid')
        self.s['speech']['scope_since'] = self.now-60_000
        self.assertFalse(any(e['quality']=='usable' for e in self.context()['evidence']))

    def test_latest_clear_visual_does_not_add_older_observations(self):
        self.eye(60_000, 20_000, 'high')
        self.eye(120_000, 95_000, 'mid')
        self.assertEqual(len(self.context()['evidence']), 1)

    def test_similar_asr_with_changed_negation_date_or_amount_stays_uncertain(self):
        for left,right in [('客户说不是明天交付','客户说是明天交付'),('客户说周五交付','客户说周四交付'),
                           ('客户说加三千块钱','客户说加五千块钱')]:
            with self.subTest(left=left):
                self.db['speech'].docs.clear();self.db['tingwu'].docs.clear()
                self.heard(text=left,pos=[4_720_000,4_736_000])
                self.db.add('tingwu',tingwu('S',1,self.now,right,pos=[4_720_000,4_736_000]))
                row=self.context()['evidence'][0]
                self.assertTrue(row['asr_conflict'])
                self.assertEqual(row['quality'],'uncertain')

    def test_punctuation_difference_does_not_make_asr_uncertain(self):
        self.heard(text='客户说明天交付',pos=[4_720_000,4_736_000])
        self.db.add('tingwu',tingwu('S',1,self.now,'客户说，明天交付。',pos=[4_720_000,4_736_000]))
        self.assertEqual(self.context()['evidence'][0]['quality'],'usable')

    def test_dual_asr_is_one_event_with_alternatives_and_canonical_owner(self):
        self.heard(pos=[4_720_000,4_736_000], band='known', name='me')
        self.db.add('tingwu', tingwu('S', 1, self.now, '客户改成下周交付', pos=[4_720_000,4_736_000]))
        rows = [x for x in self.context()['evidence'] if x['kind']=='speech']
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['refs']), 2)
        self.assertEqual(rows[0]['subject_id'], 'owner')
        self.assertEqual(rows[0]['priority'], 'highest')

    def test_one_asr_sentence_spanning_two_other_segments_stays_one_group(self):
        self.heard(1, age=6000, text='客户改了需求', pos=[4_704_000,4_720_000])
        self.heard(2, age=5000, text='交期还是明天', pos=[4_720_000,4_736_000])
        self.db.add('tingwu', tingwu('S', 1, self.now, '客户改了需求，交期还是明天', pos=[4_704_000,4_736_000]))
        rows = [x for x in self.context()['evidence'] if x['kind']=='speech']
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['refs']), 3)

    def test_self_and_disabled_speech_sources_are_not_used(self):
        selfvoice.set_spoken(['今天这代码又得加班改了'])
        self.heard(text='今天这代码又得加班改了', self_sounding=True)
        self.assertFalse(self.context()['evidence'])
        self.heard(2)
        self.s['sources']['speech_text'] = False
        self.assertFalse(self.context()['evidence'])

    def test_old_open_notes_and_stale_frame_are_not_presented_as_fresh(self):
        self.db.add('conversations', {'cid':'c1', 'start_ms':self.now-60_000, 'last_ms':self.now-1000,
                                     'updated_at':self.now-600_000, 'end_ms':None, 'notes':'旧结论', 'title':'项目'})
        self.db.add('eye_minutes', {'minute':self.now-60_000, 'version':1, 'source':'model',
                                    'frames':[{'at':self.now-300_000}], 'result':{'activity':'靠椅背', 'changes':'', 'confidence':'high'}})
        self.assertEqual(self.context()['evidence'], [])

    def test_late_note_scan_does_not_refresh_old_source_speech(self):
        self.db.add('conversations',{'cid':'c1','start_ms':self.now-600_000,'last_ms':self.now-300_000,
                                    'updated_at':self.now-10_000,'notes':'旧交期','title':'项目'})
        self.db.add('conversation_versions',{'cid':'c1','version':1,'at':self.now-10_000,
            'until_ms':self.now-30_000,'covered_last_ms':self.now-300_000,'notes':'旧交期','confidence':'high'})
        self.assertEqual(self.context()['evidence'],[])

    def test_legacy_note_version_without_coverage_is_not_highest_evidence(self):
        self.db.add('conversations',{'cid':'c1','start_ms':self.now-600_000,'last_ms':self.now-5000,'notes':'旧交期'})
        self.db.add('conversation_versions',{'cid':'c1','version':1,'at':self.now-10_000,
            'until_ms':self.now-30_000,'notes':'旧交期','confidence':'high'})
        self.assertFalse(any(e['quality']=='usable' for e in self.context()['evidence']))

    def test_scope_boundary_discards_old_speech_and_notes(self):
        self.heard(age=10_000)
        self.s['speech'].update(profile='home', scope_since=self.now-5000)
        self.db.add('conversations', {'cid':'c1', 'start_ms':self.now-60_000, 'last_ms':self.now-1000,
                                     'updated_at':self.now, 'notes':'办公室需求', 'title':'项目'})
        self.assertEqual(self.context()['evidence'], [])
        self.assertEqual(self.context()['environment'], '')

    def test_played_history_excludes_failed_synthesis_and_voice_auditions(self):
        for i, (role, state) in enumerate([('try','done'), ('sad','failed'), ('roast','done'), ('worst','unknown')]):
            self.db.add('utterances', {'uid':str(i), 'at':self.now-10_000, 'role':role, 'state':state,
                                       'text':f'句子{i}', 'scope_id':'office'})
        self.assertEqual([x['text'] for x in self.context()['recent']], ['句子2','句子3'])

    def test_emotion_boost_requires_repetition_and_acoustic_baseline(self):
        for i in range(3):
            self.heard(i, age=100_000+i*2000, emotion='neutral', loud=10, band='known', name='me')
        self.heard(10, age=10_000, emotion='angry', loud=20, band='known', name='me')
        self.assertFalse(any(x.get('emotion_boost') for x in self.context()['evidence']))
        self.heard(11, age=5000, emotion='angry', loud=20, band='known', name='me')
        boosted = [x for x in self.context()['evidence'] if x.get('emotion_boost')]
        self.assertEqual(len(boosted), 2)
        self.assertTrue(all(x['emotion_reliable'] is False for x in boosted))


class SpeechPipelineTest(SpeechCase):
    def modules(self):
        self.assertIsNotNone(importlib.util.find_spec('mind.attention'), 'attention stage is missing')
        self.assertIsNotNone(importlib.util.find_spec('mind.speech_generation'), 'generation stage is missing')
        return importlib.import_module('mind.attention'), importlib.import_module('mind.speech_generation')

    def choose_reply(self, ids, **kw):
        return {'has_topic':True, 'focus':'客户改交期', 'evidence_ids':ids, 'agreement':'unknown',
                'priority':'highest', 'reason_codes':['latest_speech'], 'special_visual':False,
                'routine_match_ids':[], **kw}

    def candidates(self, ids, text='活往上加，交期怎么不往后挪？'):
        return {'candidates':[{'text':text, 'evidence_ids':ids, 'joke_type':'算账'}], 'selected':0}

    def test_conflicting_notes_cannot_override_latest_speech(self):
        attention, _ = self.modules()
        self.heard()
        self.db.add('conversations', {'cid':'c1','version':1,'start_ms':self.now-60_000,'last_ms':self.now-30_000,
                                     'updated_at':self.now-20_000,'notes':'明天交付','title':'项目','confidence':'high'})
        ctx = self.context()
        heard = next(x['id'] for x in ctx['evidence'] if x['kind']=='speech')
        note = next(x['id'] for x in ctx['evidence'] if x['kind']=='notes')
        fake = FakeChat()
        fake.reply_next(json.dumps(self.choose_reply([note], agreement='conflict')))
        focus = attention.choose(ENV,self.store,self.s,ctx,self.now,chat=fake)
        self.assertEqual(focus['evidence_ids'], [heard])
        self.assertEqual(focus['agreement'], 'conflict')

    def test_agreeing_notes_and_speech_are_both_highest(self):
        attention, _ = self.modules()
        self.heard()
        self.db.add('conversations', {'cid':'c1','version':1,'start_ms':self.now-60_000,'last_ms':self.now-30_000,
                                     'updated_at':self.now-20_000,'notes':'改为下周交付','title':'项目','confidence':'high'})
        ctx = self.context()
        ids = [x['id'] for x in ctx['evidence']]
        fake = FakeChat(); fake.reply_next(json.dumps(self.choose_reply(ids[:1], agreement='agree')))
        focus = attention.choose(ENV,self.store,self.s,ctx,self.now,chat=fake)
        self.assertEqual(set(focus['evidence_ids']), set(ids))
        self.assertEqual(focus['priority'], 'highest')

    def test_invalid_model_refs_fall_back_to_real_speech(self):
        attention, _ = self.modules()
        self.heard()
        ctx = self.context(); fake = FakeChat()
        fake.reply_next(json.dumps(self.choose_reply(['invented'])))
        focus = attention.choose(ENV,self.store,self.s,ctx,self.now,chat=fake)
        self.assertEqual(focus['evidence_ids'], [ctx['evidence'][0]['id']])
        self.assertTrue(focus['fallback'])

    def test_malformed_model_metadata_falls_back_instead_of_crashing(self):
        attention, _ = self.modules(); self.heard(); ctx = self.context(); fake = FakeChat()
        fake.reply_next(json.dumps(self.choose_reply([ctx['evidence'][0]['id']], routine_match_ids=None)))
        focus = attention.choose(ENV,self.store,self.s,ctx,self.now,chat=fake)
        self.assertTrue(focus['fallback'])

    def test_no_visual_change_does_not_override_fresh_speech(self):
        attention, _ = self.modules(); self.heard()
        self.db.add('eye_minutes', {'minute':self.now-60_000,'version':1,'source':'model',
                                   'frames':[{'at':self.now-5000}],
                                   'result':{'activity':'主人靠在椅背上','changes':'没有明显变化','confidence':'high'}})
        ctx=self.context(); fake=FakeChat()
        vid=next(e['id'] for e in ctx['evidence'] if e['kind']=='vision')
        sid=next(e['id'] for e in ctx['evidence'] if e['kind']=='speech')
        fake.reply_next(json.dumps(self.choose_reply([vid],special_visual=True)))
        focus=attention.choose(ENV,self.store,self.s,ctx,self.now,chat=fake)
        self.assertEqual(focus['evidence_ids'],[sid])

    def test_cache_invalidates_when_new_evidence_arrives(self):
        attention, _ = self.modules()
        self.heard(); ctx = self.context(); fake = FakeChat()
        fake.reply_next(json.dumps(self.choose_reply([ctx['evidence'][0]['id']])))
        attention.choose(ENV,self.store,self.s,ctx,self.now,chat=fake)
        cached = attention.choose(ENV,self.store,self.s,ctx,self.now+1000,chat=fake)
        self.assertTrue(cached['cached'])
        self.heard(2,age=1000,text='不是下周，还是明天')
        ctx = self.context()
        fake.reply_next(json.dumps(self.choose_reply([ctx['evidence'][-1]['id']])))
        focus = attention.choose(ENV,self.store,self.s,ctx,self.now+2000,chat=fake)
        self.assertFalse(focus['cached'])
        self.assertEqual(len(fake.calls), 2)

    def test_no_fresh_evidence_refuses_before_paid_generation(self):
        attention, _ = self.modules()
        fake = FakeChat(); tts = FakeTts()
        old = speak.CHAT; speak.CHAT = fake; self.addCleanup(lambda:setattr(speak,'CHAT',old))
        with self.assertRaises(attention.NoSituation):
            speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now,wait=True)
        self.assertEqual(tts.said, [])
        self.assertEqual(self.db.count('mind_runs'), 0)

    def test_conflicting_recognition_explains_why_it_cannot_speak(self):
        attention, _ = self.modules()
        self.heard(text='明天交付', pos=[4_720_000,4_736_000])
        self.db.add('tingwu', tingwu('S',1,self.now,'明天不交付',pos=[4_720_000,4_736_000]))
        with self.assertRaisesRegex(attention.NoSituation, '两路语音识别不一致'):
            attention.choose(ENV,self.store,self.s,self.context(),self.now,chat=FakeChat())
        self.assertEqual(self.db.count('mind_runs'),0)

    def test_say_v2_forwards_role_dialect_and_preserves_entire_sentence(self):
        self.modules()
        self.heard(text='客户加需求，钱没加'); ctx=self.context()
        ids=[ctx['evidence'][0]['id']]; fake=FakeChat()
        fake.reply_next(json.dumps(self.choose_reply(ids)))
        fake.reply_next(json.dumps(self.candidates(ids,'活路翻倍，钱没涨，我这个班上得造孽哦。')))
        old=speak.CHAT; speak.CHAT=fake; self.addCleanup(lambda:setattr(speak,'CHAT',old))
        tts=FakeTts()
        rec=speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now,wait=True)
        self.assertEqual(tts.said[0][0],'活路翻倍，钱没涨，我这个班上得造孽哦。')
        self.assertIn('请讲四川话',tts.said[0][1]['instruction'])
        self.assertEqual(rec['scope_id'],'office')
        self.assertEqual(self.db['utterances'].docs[-1]['state'],'done')
        self.assertEqual([r['step'] for r in self.db['mind_runs'].docs],['attention','say'])

    def test_long_or_repeated_lines_get_one_repair_and_are_never_truncated(self):
        attention, gen=self.modules(); self.heard(); ctx=self.context(); ids=[ctx['evidence'][0]['id']]
        focus=self.choose_reply(ids); fake=FakeChat()
        fake.reply_next(json.dumps(self.candidates(ids,'工作又加了一大堆' * 10)))
        fake.reply_next(json.dumps(self.candidates(ids,'需求翻倍，工资倒是一动不动。')))
        out=gen.generate(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles')['worst'],ctx,focus,self.now,chat=fake)
        self.assertEqual(out['text'],'需求翻倍，工资倒是一动不动。')
        self.assertEqual(len(fake.calls),2)
        ctx['recent']=[{'text':'需求翻倍，工资倒是一动不动。','state':'done'}]
        fake=FakeChat()
        for _ in range(3): fake.reply_next(json.dumps(self.candidates(ids,'需求翻倍！工资倒是一动不动。')))
        with self.assertRaises(RuntimeError):
            gen.generate(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles')['worst'],ctx,focus,self.now,chat=fake)
        self.assertEqual(len(fake.calls),2)

    def test_stop_during_attention_prevents_generation_and_playback(self):
        self.heard(); ids=[self.context()['evidence'][0]['id']]
        fake=FakeChat(); fake.reply_next(json.dumps(self.choose_reply(ids)))
        def cancelling(*args,**kwargs):
            response=fake(*args,**kwargs)
            speak.cancel_pending()
            return response
        old=speak.CHAT;speak.CHAT=cancelling;self.addCleanup(lambda:setattr(speak,'CHAT',old))
        tts=FakeTts()
        with self.assertRaises(speak.NotConnected):
            speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now)
        self.assertEqual(tts.said,[])
        self.assertEqual(len(fake.calls),1)

    def test_stop_while_device_health_is_pending_cancels_the_whole_request(self):
        self.heard();fake=FakeChat();old=speak.CHAT;speak.CHAT=fake
        self.addCleanup(lambda:setattr(speak,'CHAT',old))
        class StoppedDuringHealth(FakeTts):
            def health(inner):
                speak.cancel_pending()
                return super().health()
        tts=StoppedDuringHealth()
        with self.assertRaises(speak.NotConnected):
            speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now)
        self.assertEqual(fake.calls,[]);self.assertEqual(tts.said,[])

    def test_changed_settings_prevent_old_generation_playback(self):
        self.heard(); ids=[self.context()['evidence'][0]['id']]
        fake=FakeChat(); fake.reply_next(json.dumps(self.choose_reply(ids)))
        old=speak.CHAT;speak.CHAT=fake;self.addCleanup(lambda:setattr(speak,'CHAT',old))
        tts=FakeTts()
        with self.assertRaises(speak.NotConnected):
            speak.say(ENV,self.store,self.s,speak.load_roles(ROOT/'config/roles'),'sad',tts,self.now,is_current=lambda:False)
        self.assertEqual(tts.said,[])

    def test_budget_zero_calls_no_model_or_tts(self):
        self.modules()
        calls=importlib.import_module('mind.model_calls')
        self.heard(); fake=FakeChat(); self.s['budget_usd_per_day']=0
        attention,_=self.modules()
        with self.assertRaises(calls.BudgetExceeded):
            attention.choose(ENV,self.store,self.s,self.context(),self.now,chat=fake)
        self.assertEqual(fake.calls,[])


if __name__ == '__main__':
    unittest.main()
