"""Safety policy and real Servo host regression, with no hardware access."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / 'upstream/StackChan/firmware/main/collector'

class FaceTrackingTest(unittest.TestCase):
    def test_physical_inputs_never_reach_axes_or_block_web(self):
        source = (COLLECTOR / 'remote.cpp').read_text()
        self.assertNotIn('apply_remote_yaw(', source)
        self.assertNotIn('apply_remote_pitch(', source)
        self.assertNotIn('sequence != applied_seq', source)
        receive = source.split('esp_err_t received(')[1].split('void motor_task(')[0]
        self.assertNotIn('manual_yaw.cancel()', receive)
        self.assertIn('mode_input.accept(', receive)

    def test_policy_and_real_servo(self):
        self.assertTrue((COLLECTOR / 'face_tracking.h').exists(), 'bounded tracking policy is missing')
        firmware = ROOT / 'upstream/StackChan/firmware'
        toolkit = firmware / 'components/smooth_ui_toolkit/src'
        with tempfile.TemporaryDirectory(prefix='chan-face-tracking-') as directory:
            temporary = pathlib.Path(directory)
            (temporary / 'hal').mkdir()
            (temporary / 'hal/hal.h').write_text(
                '#pragma once\n#include <cstdint>\nextern uint32_t test_tick_ms;\n'
                'struct TestHal { uint32_t millis() { return test_tick_ms; } };\nTestHal& GetHAL();\n')
            binary = temporary / 'tracking'
            subprocess.run(['c++', '-std=c++17', '-O1',
                '-DSMOOTH_UI_TOOLKIT_ENABLE_DEFAULT_HAL=0',
                '-I'+str(temporary), '-I'+str(toolkit),
                str(ROOT / 'tests/face_tracking_test.cpp'),
                str(firmware / 'main/stackchan/motion/servo.cpp'),
                *map(str, sorted((toolkit/'core').rglob('*.cpp'))), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_remote_mode_edges(self):
        self.assertTrue((COLLECTOR / 'remote_modes.h').exists(), 'remote mode edge guard is missing')
        with tempfile.TemporaryDirectory(prefix='chan-remote-modes-') as directory:
            binary = pathlib.Path(directory) / 'modes'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                str(ROOT/'tests/remote_modes_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
