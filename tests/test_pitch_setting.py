"""Link the actual Servo and spring code, substituting only its HAL clock."""
import pathlib
import subprocess
import tempfile
import unittest


class PitchSettingTest(unittest.TestCase):
    def test_persistence_requests_and_real_servo(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        firmware = root / "upstream/StackChan/firmware"
        toolkit = firmware / "components/smooth_ui_toolkit/src"
        with tempfile.TemporaryDirectory(prefix="chan-pitch-setting-") as directory:
            temporary = pathlib.Path(directory)
            (temporary / "hal").mkdir()
            (temporary / "hal/hal.h").write_text(
                "#pragma once\n#include <cstdint>\nextern uint32_t test_tick_ms;\n"
                "struct TestHal { uint32_t millis() { return test_tick_ms; } };\nTestHal& GetHAL();\n"
            )
            binary = temporary / "pitch-setting-test"
            sources = sorted((toolkit / "core").rglob("*.cpp"))
            subprocess.run([
                "c++", "-std=c++17", "-O1",
                "-DSMOOTH_UI_TOOLKIT_ENABLE_DEFAULT_HAL=0",
                "-I" + str(temporary), "-I" + str(toolkit),
                str(root / "tests/pitch_setting_test.cpp"),
                str(firmware / "main/stackchan/motion/servo.cpp"),
                *map(str, sources), "-o", str(binary),
            ], check=True, cwd=root)
            subprocess.run([str(binary)], check=True, cwd=root)
