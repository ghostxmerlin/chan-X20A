"""Host regression for the exact playback upload gate and output runner."""
import pathlib
import subprocess
import tempfile
import unittest


class PlaybackSessionTest(unittest.TestCase):
    def test_playback_boundaries_and_cleanup(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-playback-test-") as directory:
            binary = pathlib.Path(directory) / "playback-test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
                str(root / "tests/playback_session_test.cpp"), "-o", str(binary),
            ], check=True, cwd=root)
            subprocess.run([str(binary)], check=True, cwd=root)
