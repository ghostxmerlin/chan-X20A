import pathlib
import subprocess
import tempfile
import unittest


class HomeTouchTest(unittest.TestCase):
    def test_lost_reads_rejoin_the_stroke(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-home-touch-") as directory:
            binary = pathlib.Path(directory) / "home-touch"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            "-I" + str(root / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                            str(root / "tests/home_touch_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
