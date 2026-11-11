import pathlib
import subprocess
import tempfile
import unittest


class FrameChangeTest(unittest.TestCase):
    def test_thumb_diff_and_policy(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-frame-") as directory:
            binary = pathlib.Path(directory) / "frame-change"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(root / "tests/frame_change_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
