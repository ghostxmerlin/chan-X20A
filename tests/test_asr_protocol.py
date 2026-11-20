"""Runs tests/asr_protocol_test.cpp: the StickS3 voice module's frames and words (owner 2026-11-20)."""
import pathlib
import subprocess
import tempfile
import unittest


class AsrProtocolTest(unittest.TestCase):
    def test_stick_words_and_frames(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-asr-protocol-') as d:
            binary = pathlib.Path(d) / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            str(root / 'tests/asr_protocol_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
