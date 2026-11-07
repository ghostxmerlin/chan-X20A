import pathlib
import subprocess
import tempfile
import unittest

class TemperatureTest(unittest.TestCase):
    def test_read_failure_range_and_stale_data(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-thermal-') as directory:
            binary = pathlib.Path(directory) / 'thermal-test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined',
                            str(root / 'tests/temperature_sample_test.cpp'), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=10)
