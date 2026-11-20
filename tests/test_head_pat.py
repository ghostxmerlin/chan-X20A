"""Runs tests/head_pat_test.cpp: the head touch's pat gestures (owner 2026-11-20)."""
import pathlib
import subprocess
import tempfile
import unittest


class HeadPatTest(unittest.TestCase):
    def test_pats(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-head-pat-') as d:
            binary = pathlib.Path(d) / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            str(root / 'tests/head_pat_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
