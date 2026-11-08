"""Execute actual pinned SPI setup/cleanup functions with DMA fault injection."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / '.tools/esp-idf-v5.5.4/components/esp_driver_spi/src/gpspi/spi_master.c'
spec = importlib.util.spec_from_file_location('spi_patch', ROOT / 'scripts/prepare-spi-driver.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def function(source, name):
    start = source.index('static SPI_MASTER_ISR_ATTR ', source.index(name) - 60)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class SpiAllocationTest(unittest.TestCase):
    def test_setup_failure_preserves_buffers_and_frees_every_temporary(self):
        source = patch.patched(DRIVER.read_bytes()).decode()
        harness = (ROOT / 'tests/spi_allocation_test.cpp').read_text()
        harness = harness.replace('// DRIVER_FUNCTIONS', '\n'.join(
            function(source, name) for name in ['uninstall_priv_desc(', 'setup_priv_desc(']))
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / 'test.cpp'
            binary = Path(temp) / 'test'
            src.write_text(harness)
            subprocess.run(['clang++', '-std=c++17', '-O1', '-g',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(src), '-o', str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_driver_drift_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source changed'):
            patch.patched(DRIVER.read_bytes() + b'\n')
