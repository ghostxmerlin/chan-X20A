import importlib
import json
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, tingwu
from mind import settings
from mind.settings_store import SettingsStore
from mind.store import Store
from test_mind_fusion import FakeChat

W=600_000
ENV={'base_url':'http://x','api_key':'k'}


class ObservationChat(FakeChat):
    def __call__(self, base, key, body, timeout=90):
        if not self.queue:
            payload=json.loads(body['messages'][-1]['content'])
            self.reply_next(json.dumps({'observations':[{'key':'discuss_projects','statement':'办公室有人讨论项目',
                'kind':'habit','subject_id':'room','modality':'hearing','relation':'support','basis':'observed',
                'evidence_ids':[payload['evidence'][0]['id']]}]}))
        return super().__call__(base,key,body,timeout)


class MemoryWorkerTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('mind.memory_worker'), 'learning worker is missing')
        self.worker=importlib.import_module('mind.memory_worker')
        self.db=FakeDB().add('sessions',session('S',T0-60_000))
        self.store=Store(self.db); self.ss=SettingsStore(self.db,'/nonexistent/settings.json')
        self.fake=ObservationChat()

    def tick(self,at):
        return self.worker.tick(self.db,self.store,self.ss,ENV,at,chat=self.fake)

    def data(self,start=T0, text='客户又改项目需求了'):
        for n in range(10):
            m=start+n*60_000
            self.db.add('mind_packages',{'lane':'live','minute':m,'version':1,'package':{},
                                        'strong':{'coverage':{'hearing_s':60,'vision_s':0}}})
            self.db.add('speech',speech('S',str(m),m+5000,text,band='known',name='me'))

    def test_initial_deployment_does_not_learn_old_history(self):
        self.data(T0-W)
        self.tick(T0)
        self.assertEqual(self.fake.calls,[])
        self.assertEqual(self.db.count('mind_memories'),0)
        state=self.db['mind_memory_jobs'].find_one({'_id':'worker:live'})
        self.assertEqual(state['deployed_at'],T0)

    def test_new_window_is_extracted_once_and_keeps_raw_refs(self):
        self.tick(T0); self.data()
        self.tick(T0+W+30_000); self.tick(T0+W+60_000)
        self.assertEqual(len(self.fake.calls),1)
        evidence=self.db['mind_memory_evidence'].docs[-1]
        self.assertEqual(evidence['scope_id'],'office')
        self.assertEqual(evidence['observations'][0]['refs'][0]['c'],'speech')
        self.assertEqual(self.db['mind_memories'].docs[-1]['status'],'candidate')

    def test_master_off_and_no_valid_observations_make_no_paid_calls(self):
        self.tick(T0); self.data(); self.store.set_state(enabled=False)
        self.tick(T0+W+30_000)
        self.assertEqual(self.fake.calls,[])
        self.store.set_state(enabled=True)
        self.tick(T0+2*W+30_000)
        self.assertEqual(self.fake.calls,[])

    def test_disabled_sources_and_learning_do_not_extract(self):
        self.tick(T0); self.data()
        s,v=self.ss.current(); s['sources'].update(speech_text=False,tingwu=False,vision=False)
        self.ss.save(s,expected_version=v)
        self.tick(T0+W+30_000)
        self.assertEqual(self.fake.calls,[])

    def test_fabricated_reference_is_rejected_and_retried_later(self):
        self.tick(T0); self.data()
        self.fake.reply_next(json.dumps({'observations':[{'key':'bad','statement':'主人喜欢胡说', 'kind':'habit',
            'subject_id':'owner','modality':'hearing','relation':'support','basis':'observed','evidence_ids':['not-real']}]}))
        result=self.tick(T0+W+30_000)
        self.assertEqual(result['state'],'error')
        self.assertEqual(self.db.count('mind_memory_evidence'),0)
        self.tick(T0+W+60_000)
        self.assertEqual(len(self.fake.calls),1)
        self.tick(T0+W+360_000)
        self.assertEqual(len(self.fake.calls),2)
        self.assertEqual(self.db.count('mind_memory_evidence'),1)

    def test_crash_after_extraction_reuses_saved_result_without_new_call(self):
        self.tick(T0); self.data()
        self.db['mind_memory_evidence'].fail=RuntimeError('write interrupted')
        self.assertEqual(self.tick(T0+W+30_000)['state'],'error')
        self.db['mind_memory_evidence'].fail=None
        self.tick(T0+W+360_000)
        self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(self.db.count('mind_memory_evidence'),1)

    def test_late_revision_changes_one_window_not_its_support_count(self):
        self.tick(T0); self.data(); self.tick(T0+W+30_000)
        self.db['speech'].docs[-1]['text']='客户说下周才开会'
        self.tick(T0+W+90_000)
        self.assertEqual(len(self.fake.calls),2)
        self.assertEqual(self.db['mind_memories'].docs[-1]['observed_windows'],1)

    def test_mongodb_int64_revision_window_is_supported(self):
        try:
            from bson.int64 import Int64
        except ImportError:  # Host-only tests intentionally have no pymongo dependency.
            class Int64(int):
                pass
        self.tick(T0); self.data(); self.tick(T0+W+30_000)
        job=self.db['mind_memory_jobs'].find_one({'kind':'window'})
        self.db['mind_memory_jobs'].update_one({'_id':job['_id']},{'$set':{'window_ms':Int64(T0)}})
        self.db['speech'].docs[-1]['text']='客户说明天不开会了'
        result=self.tick(T0+W+90_000)
        self.assertEqual(result['state'],'ready')

    def test_failed_revision_retries_after_cursor_advanced_and_backoff(self):
        self.tick(T0); self.data(); self.tick(T0+W+30_000)
        self.db['speech'].docs[-1]['text']='客户改口了'
        self.fake.reply_next(json.dumps({'observations':'invalid'}))
        self.assertEqual(self.tick(T0+W+90_000)['state'],'error')
        self.assertEqual(self.tick(T0+W+120_000)['state'],'backoff')
        self.tick(T0+4*W+30_000)  # also beyond the ordinary two-window revision scan
        job=self.db['mind_memory_jobs'].find_one({'kind':'window','window_ms':T0})
        self.assertEqual(job['state'],'done')
        self.assertEqual(len(self.fake.calls),3)

    def test_extracted_revision_recovers_after_restart_without_new_model_call(self):
        self.tick(T0); self.data(); self.tick(T0+W+30_000)
        job=self.db['mind_memory_jobs'].find_one({'kind':'window'})
        self.db['mind_memory_jobs'].update_one({'_id':job['_id']},{'$set':{'state':'extracted'}})
        self.tick(T0+W+60_000)
        self.assertEqual(self.db['mind_memory_jobs'].find_one({'_id':job['_id']})['state'],'done')
        self.assertEqual(len(self.fake.calls),1)

    def test_other_worker_lease_prevents_duplicate_extraction(self):
        self.tick(T0); self.data()
        self.db['mind_memory_jobs'].update_one({'_id':'lease:live'},
            {'$set':{'owner':'other','expires':T0+3*W}},upsert=True)
        self.assertEqual(self.tick(T0+W+30_000)['state'],'busy')
        self.assertEqual(self.fake.calls,[])

    def test_scope_switch_prevents_old_office_data_entering_home(self):
        self.tick(T0); self.data()
        s,v=self.ss.current(); s['speech'].update(profile='home',scope_since=T0+W)
        self.ss.save(s,expected_version=v)
        self.tick(T0+W+30_000)
        self.assertEqual(self.fake.calls,[])
        self.assertEqual(self.db.count('mind_memory_evidence'),0)

    def test_long_downtime_records_a_gap_and_caps_catchup(self):
        self.tick(T0)
        out=self.tick(T0+72*3600_000)
        state=self.db['mind_memory_jobs'].find_one({'_id':'worker:live'})
        self.assertTrue(state.get('skipped_range'))
        self.assertLessEqual(out['processed'],3)

    def test_invalid_extraction_is_not_cached_as_a_permanent_retry_failure(self):
        self.tick(T0); self.data()
        bad={'observations':[{'key':'', 'statement':'无效候选', 'kind':'habit','subject_id':'room',
                              'modality':'hearing','relation':'support','basis':'observed','evidence_ids':[]}]}
        class BadOnce(ObservationChat):
            def __call__(inner,base,key,body,timeout=90):
                if not inner.calls:
                    bad['observations'][0]['evidence_ids']=[json.loads(body['messages'][-1]['content'])['evidence'][0]['id']]
                    inner.reply_next(json.dumps(bad))
                return super().__call__(base,key,body,timeout)
        self.fake=BadOnce()
        self.assertEqual(self.tick(T0+W+30_000)['state'],'error')
        self.tick(T0+W+360_000)
        self.assertEqual(len(self.fake.calls),2)
        self.assertEqual(self.db.count('mind_memory_evidence'),1)

    def test_link_estimate_without_raw_observation_is_not_coverage(self):
        self.data()
        self.db['speech'].docs.clear()
        inputs=self.worker.window_inputs(self.db,self.ss.current()[0],T0,T0+W)
        self.assertEqual(inputs['coverage'],{'hearing_s':0,'vision_s':0})

    def test_cross_window_asr_is_counted_once_using_capture_time(self):
        boundary=T0+W
        pos=[(boundary-500-(T0-60_000))*16,(boundary+500-(T0-60_000))*16]
        self.db.add('speech',speech('S',1,boundary+500,'客户又改项目需求了',pos=pos))
        self.db.add('tingwu',tingwu('S',1,boundary+1000,'客户又改项目需求了',pos=pos))
        for m in (boundary-60_000,boundary):
            self.db.add('mind_packages',{'lane':'live','minute':m,'version':1,'strong':{'coverage':{'hearing_s':60,'vision_s':0}}})
        s,_=self.ss.current()
        before=self.worker.window_inputs(self.db,s,T0,boundary)
        after=self.worker.window_inputs(self.db,s,boundary,boundary+W)
        self.assertEqual(before['evidence'],[])
        self.assertEqual(before['coverage']['hearing_s'],0)
        self.assertEqual(len(after['evidence']),1)
        self.assertEqual(len(after['evidence'][0]['refs']),2)
        self.assertEqual(after['evidence'][0]['at'],boundary+500)

    def test_long_group_across_midnight_is_assigned_to_one_day(self):
        boundary=int(settings.local_time(T0,self.ss.current()[0]['tz']).replace(hour=0,minute=0,second=0).timestamp()*1000)+86_400_000
        base=T0-60_000
        pos=[(boundary-1000-base)*16,(boundary+1000-base)*16]
        self.db.add('speech',speech('S',1,boundary-500,'客户改需求',pos=[pos[0],pos[0]+16000]))
        self.db.add('speech',speech('S',2,boundary+500,'交期不改',pos=[pos[0]+16000,pos[1]]))
        self.db.add('tingwu',tingwu('S',1,boundary+1500,'客户改需求，交期不改',pos=pos))
        s,_=self.ss.current()
        a=self.worker.window_inputs(self.db,s,boundary-W,boundary)
        b=self.worker.window_inputs(self.db,s,boundary,boundary+W)
        self.assertEqual(len(a['evidence']),1);self.assertEqual(len(a['evidence'][0]['refs']),3)
        self.assertEqual(b['evidence'],[])

    def test_expanded_group_cannot_pull_old_scene_text_across_hard_boundary(self):
        boundary=T0+W;base=T0-60_000
        pos=[(boundary-500-base)*16,(boundary+500-base)*16]
        self.db.add('speech',speech('S',1,boundary+500,'客户改需求',pos=pos))
        self.db.add('tingwu',tingwu('S',1,boundary+1500,'客户改需求',pos=pos))
        s,_=self.ss.current();s['speech'].update(profile='home',scope_since=boundary)
        self.assertEqual(self.worker.window_inputs(self.db,s,boundary,boundary+W)['evidence'],[])
        s['speech']['scope_since']=0
        self.assertEqual(self.worker.window_inputs(self.db,s,boundary,boundary+W,not_before=boundary)['evidence'],[])

    def test_late_local_asr_retracts_the_previous_window_assignment(self):
        self.tick(T0);boundary=T0+W;base=T0-60_000
        pos=[(boundary-500-base)*16,(boundary+500-base)*16]
        self.db.add('tingwu',tingwu('S',1,boundary+1500,'客户改需求',pos=pos))
        for m in (boundary-60_000,boundary):
            self.db.add('mind_packages',{'lane':'live','minute':m,'version':1,'strong':{'coverage':{'hearing_s':60,'vision_s':0}}})
        self.tick(boundary+30_000)
        self.db.add('speech',speech('S',1,boundary+500,'客户改需求',pos=pos))
        self.tick(boundary+W+30_000)
        rows=self.db['mind_memory_evidence'].docs
        old=max((r for r in rows if r['window_ms']==T0),key=lambda r:r['version'])
        new=max((r for r in rows if r['window_ms']==boundary),key=lambda r:r['version'])
        self.assertEqual(old['observations'],[])
        self.assertEqual(len(new['observations']),1)

    def test_budget_failure_can_resume_without_skipping_window(self):
        self.tick(T0); self.data()
        s,v=self.ss.current(); s['budget_usd_per_day']=0; self.ss.save(s,expected_version=v)
        self.assertEqual(self.tick(T0+W+30_000)['error'],'BudgetExceeded')
        self.assertEqual(self.fake.calls,[])
        s,v=self.ss.current(); s['budget_usd_per_day']=20; self.ss.save(s,expected_version=v)
        self.tick(T0+W+360_000)
        self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(self.db.count('mind_memory_evidence'),1)

    def test_owner_alias_is_saved_under_stable_owner_identity(self):
        self.tick(T0); self.data()
        inputs=self.worker.window_inputs(self.db,self.ss.current()[0],T0,T0+W)
        self.fake.reply_next(json.dumps({'observations':[{'key':'talk_projects','statement':'主人在讨论项目',
            'kind':'habit','subject_id':'me','modality':'hearing','relation':'support','basis':'observed',
            'evidence_ids':[inputs['evidence'][0]['id']]}]}))
        self.tick(T0+W+30_000)
        self.assertEqual(self.db['mind_memories'].docs[0]['subject_id'],'owner')

    def test_independent_process_stores_get_unique_model_run_ids(self):
        with mock.patch('mind.store.time.time',return_value=42.0):
            one=Store(self.db).put_run({'step':'memory'})
            two=Store(self.db).put_run({'step':'say'})
        self.assertNotEqual(one,two)


if __name__=='__main__':
    unittest.main()
