import pathlib, subprocess, tempfile, unittest
class BootTrackingTest(unittest.TestCase):
    def test_boot_auto_tracking_runs_once(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-boot-tracking-') as d:
            binary=pathlib.Path(d)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            str(root/'tests/boot_tracking_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
