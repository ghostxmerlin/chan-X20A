from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'upstream/StackChan/firmware/main'


class RuntimeLogTest(unittest.TestCase):
    def compile_and_run(self, name, sources):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / name
            subprocess.run([
                'c++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror',
                '-DCOLLECTOR_RUNTIME_LOG_TEST', '-fsanitize=address,undefined',
                '-fno-omit-frame-pointer', '-I' + str(SOURCE),
                *map(str, sources), '-o', str(binary),
            ], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)

    def test_redaction_wrapping_and_concurrent_snapshots(self):
        self.compile_and_run('runtime-log', [ROOT/'tests/runtime_log_test.cpp', SOURCE/'collector/runtime_log.cpp'])

    def test_ring_fits_internal_ram_budget(self):
        self.compile_and_run('runtime-log-storage', [ROOT/'tests/runtime_log_storage_test.cpp'])
