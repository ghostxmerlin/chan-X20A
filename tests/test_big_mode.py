"""Runs tests/big_mode_test.cpp: the three big modes 关 / 收集 / 智能 (owner 2026-11-20)."""
import pathlib
import subprocess
import tempfile
import unittest


class BigModeTest(unittest.TestCase):
    def test_big_modes(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-big-mode-') as d:
            binary = pathlib.Path(d) / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            str(root / 'tests/big_mode_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
