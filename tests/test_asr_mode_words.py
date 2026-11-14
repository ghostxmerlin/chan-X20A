import pathlib
import subprocess
import tempfile
import unittest


class AsrModeWordsTest(unittest.TestCase):
    def test_words_map_to_mode_inputs_and_lose_old_uses(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-asr-mode-words-") as directory:
            binary = pathlib.Path(directory) / "asr-mode-words"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            "-I" + str(root / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                            str(root / "tests/asr_mode_words_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
