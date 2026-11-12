import copy
import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind import api, settings
from mind.store import Store
from mind.settings_store import SettingsStore
from test_mind_speak import FakeTts, ROOT


class SpeechApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        self.tts = FakeTts()
        self.server = api.make_server(Store(self.db), 'k', port=0, ctx={
            'settings_store':self.ss, 'tts':self.tts, 'roles_dir':str(ROOT/'config/roles')})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer k','Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e: return e.code, json.load(e)

    def test_settings_cas_and_profile_preservation(self):
        status, cfg = self.call('/speech/settings')
        self.assertEqual(status, 200)
        self.assertNotIn('secret-id', json.dumps(cfg))
        cfg['speech']['profile'] = 'home'
        cfg['speech']['profiles']['office']['environment'] = '办公环境'
        body = {k:cfg[k] for k in ('speech','memory')}
        self.assertEqual(self.call('/speech/settings', body)[0], 400)
        body['expected_version'] = cfg['version']
        status, saved = self.call('/speech/settings', body)
        self.assertEqual(status, 200)
        self.assertEqual(self.ss.current()[0]['environment'], '办公环境')
        self.assertEqual(saved['speech']['profiles']['home']['topics'], cfg['speech']['profiles']['home']['topics'])
        self.assertGreater(saved['speech']['scope_since'], 0)
        self.assertEqual(self.call('/speech/settings', body)[0], 409)
        self.assertEqual(self.call('/settings', {'settings':self.ss.current()[0], 'expected_version':cfg['version']})[0], 409)

    def test_memory_page_edit_restore_conflict_and_scope(self):
        mid = 'm'+'a'*24
        self.db.add('mind_memories', {'lane':'live','scope_id':'office','memory_id':mid,'version':1,
            'statement':'主人常靠椅背','status':'candidate','changed_at':1})
        self.assertEqual(self.call('/memories?limit=101')[0], 400)
        self.assertEqual(self.call('/memories?status=garbage')[0], 400)
        self.assertEqual(self.call('/memory/history?id=bad')[0], 400)
        self.assertEqual(self.call('/memories?offset=0&limit=1')[1]['total'], 1)
        body={'id':mid, 'expected_version':1, 'action':'correct', 'statement':'靠的是办公椅'}
        status, row=self.call('/memory/edit', body)
        self.assertEqual(status,200)
        self.assertEqual(row['version'],2)
        self.assertEqual(self.call('/memory/edit',body)[0],409)
        body.update(expected_version=2,action='restore',restore_version=1)
        self.assertEqual(self.call('/memory/edit',body)[1]['version'],3)
        self.assertEqual(self.call('/memory/history?id='+mid)[1]['total'],3)
        s,v=self.ss.current();s['speech']['profile']='home';self.ss.save(s,expected_version=v)
        self.assertEqual(self.call('/memory/history?id='+mid)[0],404)

    def test_role_audition_uses_saved_config_and_gate(self):
        self.assertEqual(self.call('/role/try', {'role':'sad'})[0],409)
        self.call('/switches', {'name':'mouth','mode':'tts'})
        s,v=self.ss.current();s['speech']['roles']['sad']={'dialect':'四川话','rate':1.1,'instruction':'自然地抱怨'}
        self.ss.save(s,expected_version=v)
        self.assertEqual(self.call('/role/try', {'role':'sad'})[0],200)
        self.assertEqual(self.tts.said[-1][1]['voice'],'xiaohei')
        self.assertEqual(self.tts.said[-1][1]['instruction'],'请讲四川话。自然地抱怨')
        self.assertEqual(self.tts.said[-1][1]['rate'],1.1)
        self.assertEqual(self.db['utterances'].docs[-1]['role'],'try')
        self.tts.connected=False
        self.assertEqual(self.call('/role/try',{'role':'sad'})[0],409)

    def test_valid_long_scene_settings_fit_request_limit(self):
        _,cfg=self.call('/speech/settings')
        for p in cfg['speech']['profiles'].values():p['environment']='办'*3000
        body={k:cfg[k] for k in ('speech','memory')};body['expected_version']=cfg['version']
        self.assertEqual(self.call('/speech/settings',body)[0],200)

    def test_explanation_is_lazy_and_omits_provider_payloads(self):
        self.db.add('utterances', {'uid':'uabc','at':100,'role':'roast','text':'一句话','state':'done',
            'attention':{'focus':'需求'},'context':{'evidence':[{'id':'s1','text':'改需求'}],'scope':{'id':'office'}},
            'generation':{'private':'provider'},'instruction':'说话方式'})
        status, rows=self.call('/utterances?from=1&to=1000')
        self.assertEqual(status,200)
        self.assertNotIn('context',rows[0]);self.assertNotIn('attention',rows[0]);self.assertNotIn('generation',rows[0])
        status, row=self.call('/say/explain?uid=uabc')
        self.assertEqual(status,200);self.assertEqual(row['attention']['focus'],'需求')
        self.assertNotIn('generation',row)

