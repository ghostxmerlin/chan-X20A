"""Read-only SCS register 40 diagnostics; never accesses hardware."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class ServoTorqueSnapshotTest(unittest.TestCase):
    def test_raw_status_timeout_and_read_only_boundary(self):
        toolkit=ROOT/'upstream/StackChan/firmware/components/smooth_ui_toolkit/src'
        with tempfile.TemporaryDirectory(prefix='servo-torque-snapshot-') as directory:
            binary=pathlib.Path(directory)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                '-Wno-unused-parameter','-Wno-mismatched-tags','-I'+str(toolkit),
                str(ROOT/'tests/servo_torque_snapshot_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
