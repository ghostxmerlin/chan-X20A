import pathlib, subprocess, tempfile, unittest
class TimedYawTurnTest(unittest.TestCase):
    def test_timed_rotation_and_stop_failures(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-web-control-') as d:
            binary=pathlib.Path(d)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(root/'tests/timed_yaw_turn_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
