import pathlib
import subprocess
import tempfile
import unittest


class MouthStreamTest(unittest.TestCase):
    def test_ring_player_and_respond(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-mouth-") as directory:
            binary = pathlib.Path(directory) / "mouth-stream"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            str(root / "tests/mouth_stream_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
