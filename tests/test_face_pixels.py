import pathlib
import subprocess
import tempfile
import unittest

class FacePixelsTest(unittest.TestCase):
    def test_formats_and_bounds(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-face-") as directory:
            binary = pathlib.Path(directory) / "face-pixels"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(root / "tests/face_pixels_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
