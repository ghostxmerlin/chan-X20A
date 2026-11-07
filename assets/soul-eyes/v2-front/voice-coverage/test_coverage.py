import json
import unittest
from build_coverage import SOURCE, ROOT, STATES, THEMES, PLACEHOLDERS, sha


class CoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=json.loads(SOURCE.read_text())
        cls.maps=json.loads((ROOT/'candidate-animation-map.json').read_text())['candidates']

    def test_each_source_id_once_and_raw_fields_preserved(self):
        source={c['id']:c for c in self.source['candidates']}
        self.assertEqual(len(self.maps),len(source))
        self.assertEqual({m['id'] for m in self.maps},set(source))
        for m in self.maps:
            for field in ('id','theme','emotion','intent'):
                self.assertEqual(m[field],source[m['id']][field])

    def test_all_themes_and_animations_known(self):
        self.assertEqual({m['theme'] for m in self.maps},set(THEMES))
        self.assertEqual(len(THEMES),20)
        self.assertEqual(len(STATES),30)
        for m in self.maps:
            self.assertIn(m['primary_animation'],STATES)
            self.assertTrue(m['mapping_basis']['strategy'])
            self.assertTrue(all(a in STATES for a in m['alternates']))
            self.assertNotIn(m['primary_animation'],m['alternates'])

    def test_unverified_audio_and_placeholders_not_claimed_verified(self):
        self.assertEqual(sum(m['emotion_unspecified'] for m in self.maps),72)
        for m in self.maps:
            self.assertTrue(m['needs_audio_review'])
            self.assertEqual(m['emotion_unspecified'],m['emotion'] in PLACEHOLDERS)
            if m['emotion_unspecified']:
                self.assertNotEqual(m['mapping_basis']['strategy'],'emotion_keyword')
                self.assertEqual(m['mapping_basis']['emotion_matches'],[])

    def test_snapshot_still_matches_readonly_source(self):
        snapshot=json.loads((ROOT/'source-snapshot.json').read_text())
        self.assertEqual(snapshot['source_sha256'],sha(SOURCE))


if __name__=='__main__':
    unittest.main()
