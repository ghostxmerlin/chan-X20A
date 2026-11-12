import copy
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind import settings, speak
from mind.settings_store import SettingsStore, validate

ROOT = Path(__file__).resolve().parents[1]


class SpeechSettingsTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('mind.speech_settings'), 'speech configuration is missing')
        return importlib.import_module('mind.speech_settings')

    def test_legacy_environment_only_follows_office(self):
        mod = self.module()
        s = settings._merge(settings.DEFAULTS, {'environment': '旧办公室', 'owner': {'display': '保留'}})
        self.assertEqual(mod.effective(s)['environment'], '旧办公室')
        s['speech']['profile'] = 'home'
        self.assertEqual(mod.effective(s)['environment'], '')
        self.assertEqual(s['environment'], '旧办公室')
        self.assertEqual(mod.effective(s)['owner']['display'], '保留')

    def test_invalid_nested_settings_never_reach_model(self):
        self.module()
        for patch in ({'profile': 'bad'}, {'max_chars': 1000}, {'max_chars': True},
                      {'roles': {'sad': {'rate': float('nan')}}}, {'roles': {'bad': {}}},
                      {'roles': {'sad': {'dialect': []}}}, {'roles': {'sad': {'instruction': None}}},
                      {'roles': {'sad': {'voice': 'replace-private-id'}}}, {'profiles': []}):
            s = settings.load(None)
            s['speech'].update(patch)
            self.assertTrue(validate(s), patch)
        s = settings.load(None)
        s['memory'] = {'learning': 'yes', 'apply': True}
        self.assertTrue(validate(s))

    def test_dialect_resolves_actual_tts_instruction_without_changing_voice(self):
        mod = self.module()
        roles = speak.load_roles(ROOT / 'config/roles')
        s = settings.load(None)
        sad = mod.role_config(roles['sad'], s)
        worst = mod.role_config(roles['worst'], s)
        self.assertEqual((sad['voice'], worst['voice']), ('xiaohei', 'me01'))
        self.assertIn('请讲四川话', sad['instruction'])
        self.assertIn('请讲湖北武汉话', worst['instruction'])
        s['speech']['roles']['sad'] = {'dialect': '普通话', 'rate': 1.1}
        sad = mod.role_config(roles['sad'], s)
        self.assertIn('请讲普通话', sad['instruction'])
        self.assertNotIn('四川话', sad['instruction'])
        self.assertEqual(sad['rate'], 1.1)

    def test_stale_settings_save_is_rejected_without_new_revision(self):
        mod = self.module()
        db = FakeDB()
        st = SettingsStore(db, '/nonexistent/settings.json')
        s, v = st.current()
        changed = copy.deepcopy(s)
        changed['speech']['profile'] = 'home'
        self.assertEqual(st.save(changed, '切到家里', expected_version=v), 1)
        self.assertGreater(mod.scope(st.current()[0])['since'], 0)
        with self.assertRaises(Exception) as ex:
            st.save(s, '旧页面', expected_version=v)
        self.assertEqual(type(ex.exception).__name__, 'SettingsConflict')
        self.assertEqual(st.current()[1], 1)
        self.assertEqual(st.current()[0]['speech']['profile'], 'home')
        self.assertEqual(st.get(0)['speech']['profile'], 'office')

    def test_saving_same_profile_keeps_scope_boundary(self):
        mod = self.module()
        st = SettingsStore(FakeDB(), '/nonexistent/settings.json')
        s, v = st.current()
        s['speech']['profile'] = 'friends'
        st.save(s, expected_version=v)
        s, v = st.current()
        boundary = mod.scope(s)['since']
        s['speech']['profiles']['friends']['environment'] = '朋友家'
        st.save(s, expected_version=v)
        self.assertEqual(mod.scope(st.current()[0])['since'], boundary)


if __name__ == '__main__':
    unittest.main()
