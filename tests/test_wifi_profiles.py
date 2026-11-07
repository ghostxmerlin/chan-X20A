import pathlib
import subprocess
import tempfile
import unittest

class WifiProfilesTest(unittest.TestCase):
    def test_priority_and_credentials(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-wifi-profiles-") as directory:
            binary = pathlib.Path(directory) / "wifi-profiles"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(root / "tests/wifi_profiles_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
