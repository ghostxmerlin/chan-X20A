import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB,T0,session,speech
from mind import eye,notes,notes_runner,settings,fusion
from mind.records import Records
from mind.store import Store
from test_mind_fusion import FakeChat,minute_input


class SceneScopeTest(unittest.TestCase):
    def test_new_scene_closes_old_conversation_and_excludes_old_notes(self):
        db=FakeDB().add('sessions',session('S',T0-60_000))
        db.add('switches',{'_id':'notes','on':True})
        db.add('conversations',{'cid':'old','start_ms':T0-60_000,'last_ms':T0+20_000,'end_ms':None,
                               'refs':[], 'notes':'办公室客户明天来','title':'旧办公室话题','version':1})
        db.add('speech',speech('S',1,T0+40_000,'今天家里吃什么'))
        s=settings.load(None); s['environment']='原办公室环境'; s['speech'].update(profile='home',scope_since=T0+30_000)
        fake=FakeChat(); fake.reply_next(json.dumps({'continues':'','title':'晚饭','notes':'在家聊晚饭','confidence':'high'}))
        old=notes.CHAT; notes.CHAT=fake; self.addCleanup(lambda:setattr(notes,'CHAT',old))
        notes_runner.tick(db,Store(db),Records(db),s,{'base_url':'x','api_key':'k'},T0+120_000)
        self.assertIsNotNone(db['conversations'].find_one({'cid':'old'})['end_ms'])
        latest=db['conversations'].find_one({},sort=[('start_ms',-1)])
        self.assertGreaterEqual(latest['start_ms'],T0+30_000)
        prompt=json.dumps(fake.calls[0]['messages'],ensure_ascii=False)
        self.assertNotIn('原办公室环境',prompt)
        self.assertNotIn('旧办公室话题',prompt)

    def test_eye_does_not_carry_old_scene_into_new_environment(self):
        s=settings.load(None); s['environment']='原办公室环境'; s['speech'].update(profile='home',scope_since=T0)
        previous={'minute':T0-60_000,'result':{'activity':'办公室客户来访','people_count':3}}
        msg=eye.messages_for(s,T0,{'events':[],'frames':[]},previous)
        text=json.dumps(msg,ensure_ascii=False)
        self.assertNotIn('原办公室环境',text)
        self.assertNotIn('办公室客户来访',text)

    def test_fusion_excludes_pre_switch_scene_from_model_input(self):
        db=FakeDB(); store=Store(db); s=settings.load(None)
        s['speech'].update(profile='home',scope_since=T0+60_000)
        db.add('mind_scenes',{'lane':'live','minute':T0,'version':1,'source':'model',
                              'scene':{'summary':'办公室旧场景','entries':[{'key':'old','text':'办公室旧场景','confidence':'high','basis':'observed'}]}})
        fake=FakeChat(); old=fusion.CHAT; fusion.CHAT=fake; self.addCleanup(lambda:setattr(fusion,'CHAT',old))
        pkg,strong=minute_input(T0+60_000)
        fusion.run_minute({'base_url':'x','api_key':'k'},store,s,1,T0+60_000,pkg,strong,{},force_model='gpt-6-luna')
        self.assertNotIn('办公室旧场景',json.dumps(fake.calls[0]['messages'],ensure_ascii=False))


if __name__=='__main__':
    unittest.main()
