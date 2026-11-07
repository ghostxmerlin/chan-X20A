import json
import pathlib
import subprocess
import tempfile
import unittest


class VoiceLibraryTest(unittest.TestCase):
    def test_decoder_policy_and_local_library(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        assets = root / '.private/voice-library'
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory) / 'voice-test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(root/'tests/voice_codec_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            if not (assets/'manifest.json').exists():
                return  # Pure malformed-input/policy regressions always run on clean checkouts.
            manifest = json.loads((assets/'manifest.json').read_text())
            self.assertEqual(len(manifest['clips']),20)
            for clip in manifest['clips']:
                decoded = pathlib.Path(directory) / 'decoded.pcm'
                subprocess.run([str(binary), str(assets/(clip['source_id']+'.adpcm')),
                                str(clip['frames']), str(decoded)], check=True)
                self.assertEqual(decoded.read_bytes(),
                                 (assets/(clip['source_id']+'.reference.pcm')).read_bytes()[:clip['frames']*2])
