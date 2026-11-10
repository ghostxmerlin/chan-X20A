"""Mic digital gain + peak limiter (collector mic_gain.h) on the host; no hardware."""
import pathlib
import subprocess
import tempfile
import unittest


class MicGainTest(unittest.TestCase):
    def test_gain_raises_quiet_speech_and_limiter_never_clips(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-mic-gain-") as directory:
            binary = pathlib.Path(directory) / "mic-gain-test"
            subprocess.run(["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                            str(root / "tests/mic_gain_test.cpp"), "-o", str(binary)], check=True, cwd=root)
            subprocess.run([str(binary)], check=True, cwd=root)


if __name__ == "__main__":
    unittest.main()
