import pathlib
import subprocess
import tempfile
import unittest


class TouchGestureTest(unittest.TestCase):
    def test_device_shaped_strokes_classify(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-touch-gesture-") as directory:
            binary = pathlib.Path(directory) / "touch-gesture"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            "-I" + str(root / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                            str(root / "tests/touch_gesture_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
