"""Host regression for the optional CoreS3 magnetic/light/proximity acquisition."""
import pathlib
import subprocess
import tempfile
import unittest


class EnvironmentSensorsTest(unittest.TestCase):
    def test_sensor_conversion_and_register_failures(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        driver = root / "upstream/StackChan/firmware/main/hal/drivers/environment"
        with tempfile.TemporaryDirectory(prefix="chan-environment-") as directory:
            binary = pathlib.Path(directory) / "sensors-test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=undefined", "-fno-omit-frame-pointer",
                str(root / "tests/environment_sensors_test.cpp"),
                str(driver / "bmm150_data.cpp"), str(driver / "ltr553.cpp"),
                "-o", str(binary),
            ], check=True, cwd=root, timeout=30)
            subprocess.run([str(binary)], check=True, cwd=root, timeout=20)
