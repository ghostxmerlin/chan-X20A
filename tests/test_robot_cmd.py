"""Host checks for the robot command line (collector-0.5.10): robot_cmd.h (parse, rate limit, glyphs, the word that
stays) and head_gesture.h (nod / shake waypoints and legs)."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FW = ROOT / "upstream/StackChan/firmware"


def build_and_run(test, name):
    with tempfile.TemporaryDirectory(prefix=f"chan-{name}-") as directory:
        binary = pathlib.Path(directory) / name
        subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(FW / "main/collector"), "-I" + str(FW / "components/ArduinoJson/src"),
                        str(ROOT / "tests" / test), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


class RobotCmdTest(unittest.TestCase):
    def test_parse_rate_glyphs_sticky(self):
        build_and_run("robot_cmd_test.cpp", "robot-cmd")

    def test_head_gesture(self):
        build_and_run("head_gesture_test.cpp", "head-gesture")

    def test_font_has_every_glyph(self):
        """collector_words_20.c (scripts/generate-words-font.sh) was regenerated after kGlyphs changed."""
        import re
        src = (FW / "main/collector/collector_words_20.c").read_text(encoding="utf-8")
        have = {chr(int(h, 16)) for h in re.findall(r"/\* U\+([0-9A-F]+)", src)}
        glyphs = re.search(r'kGlyphs = "([^"]*)"', (FW / "main/collector/m12_words.h").read_text(encoding="utf-8")).group(1)
        self.assertEqual(set(glyphs) - have, set())


if __name__ == "__main__":
    unittest.main()
