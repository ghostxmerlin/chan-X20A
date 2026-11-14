import pathlib
import subprocess
import tempfile
import unittest


class MouthAssetsTest(unittest.TestCase):
    def test_transfer_window_rejections_abort_unchanged(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-mouth-assets-") as directory:
            binary = pathlib.Path(directory) / "mouth-assets"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            "-I" + str(root / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                            str(root / "tests/mouth_assets_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
