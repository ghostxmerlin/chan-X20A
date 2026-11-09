"""Operator servo tuning request validation; no hardware access."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ServoTuningTest(unittest.TestCase):
    def test_request_validation(self):
        toolkit = ROOT / 'upstream/StackChan/firmware/components/smooth_ui_toolkit/src'
        with tempfile.TemporaryDirectory(prefix='chan-servo-tuning-') as directory:
            binary = pathlib.Path(directory) / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                            '-Wno-mismatched-tags', '-I' + str(toolkit),
                            str(ROOT / 'tests/servo_tuning_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
