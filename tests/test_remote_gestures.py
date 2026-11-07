"""Host regression for center/display events and independent remote controls."""
import pathlib
import subprocess
import tempfile
import unittest


class RemoteGestureTest(unittest.TestCase):
    def test_center_events_and_motion_isolation(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-remote-gestures-") as directory:
            binary = pathlib.Path(directory) / "remote-gestures-test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                str(root / "tests/remote_gestures_test.cpp"), "-o", str(binary),
            ], check=True, cwd=root)
            subprocess.run([str(binary)], check=True, cwd=root)
