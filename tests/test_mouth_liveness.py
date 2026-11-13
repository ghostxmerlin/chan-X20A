import pathlib
import subprocess
import tempfile
import unittest


class MouthLivenessTest(unittest.TestCase):
    def test_dead_after_45s_without_frames(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-mouth-live-") as directory:
            binary = pathlib.Path(directory) / "mouth-liveness"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            str(root / "tests/mouth_liveness_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
