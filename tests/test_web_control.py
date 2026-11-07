import pathlib, subprocess, tempfile, unittest
class WebControlTest(unittest.TestCase):
    def test_discrete_yaw_and_shared_m12_request(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-web-control-') as d:
            binary=pathlib.Path(d)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(root/'tests/web_control_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
