import hashlib
import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class EyesCatalogTest(unittest.TestCase):
    def test_all_assets_and_current_40_voices_have_a_checked_catalog(self):
        script = ROOT / 'scripts/prepare-eyes-catalog.py'
        self.assertTrue(script.exists(), 'The device eyes catalog generator is missing')
        subprocess.run(['python3', str(script), '--check'], check=True)
        mapping = json.loads((ROOT/'assets/eyes-voice-map.json').read_text())
        self.assertEqual([c['clip_id'] for c in mapping['clips']], list(range(1, 41)))
        states = json.loads((ROOT/'assets/soul-eyes/v2-front/states.json').read_text())
        for clip in mapping['clips']:
            self.assertIn(clip['state'], states)
            self.assertTrue(clip['reason'])
        manifest = json.loads((ROOT/'assets/soul-eyes/v2-front/manifest.json').read_text())
        count = 0
        for style in manifest['styles']:
            for asset in style['animations']:
                data = (ROOT/'assets/soul-eyes/v2-front'/asset['path']).read_bytes()
                self.assertEqual(len(data), asset['bytes'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), asset['sha256'])
                count += 1
        self.assertEqual(count, 90)
