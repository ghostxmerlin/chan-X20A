"""Bounded per-axis motion trace used as servo arrival evidence; no hardware access."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class MotionTraceTest(unittest.TestCase):
    def test_bounded_trace_lifecycle(self):
        toolkit = ROOT / 'upstream/StackChan/firmware/components/smooth_ui_toolkit/src'
        with tempfile.TemporaryDirectory(prefix='chan-motion-trace-') as directory:
            binary = pathlib.Path(directory) / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                            '-Wno-mismatched-tags', '-I' + str(toolkit),
                            str(ROOT / 'tests/motion_trace_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
