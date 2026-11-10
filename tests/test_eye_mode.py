"""Normal eye mode: emotion mapping, request ids, vision controller, player pool/sources."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class EyeModeTest(unittest.TestCase):
    def test_normal_mode(self):
        with tempfile.TemporaryDirectory() as d:
            binary = pathlib.Path(d) / "eye-mode"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                            str(ROOT / "tests/eye_mode_test.cpp"),
                            str(ROOT / "upstream/StackChan/firmware/main/collector/eyes/player.cpp"),
                            "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
