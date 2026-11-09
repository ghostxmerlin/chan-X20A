"""Real Servo spring/update loop with a substituted clock; no hardware access."""
import pathlib
import subprocess
import tempfile
import unittest


class ServoTorqueReleaseTest(unittest.TestCase):
    def test_release_waits_for_measured_arrival_or_bounded_hold(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        firmware = root / "upstream/StackChan/firmware"
        toolkit = firmware / "components/smooth_ui_toolkit/src"
        with tempfile.TemporaryDirectory(prefix="chan-servo-torque-release-") as directory:
            temporary = pathlib.Path(directory)
            (temporary / "hal").mkdir()
            (temporary / "hal/hal.h").write_text(
                "#pragma once\n#include <cstdint>\nextern uint32_t test_tick_ms;\n"
                "struct TestHal { uint32_t millis() { return test_tick_ms; } };\nTestHal& GetHAL();\n"
            )
            binary = temporary / "servo-torque-release-test"
            subprocess.run([
                "c++", "-std=c++17", "-O1", "-Wall", "-Wextra",
                "-DSMOOTH_UI_TOOLKIT_ENABLE_DEFAULT_HAL=0",
                "-I" + str(temporary), "-I" + str(toolkit),
                str(root / "tests/servo_torque_release_test.cpp"),
                str(firmware / "main/stackchan/motion/servo.cpp"),
                *map(str, sorted((toolkit / "core").rglob("*.cpp"))), "-o", str(binary),
            ], check=True, cwd=root)
            subprocess.run([str(binary)], check=True, cwd=root)


if __name__ == "__main__":
    unittest.main()
