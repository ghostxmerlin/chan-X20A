import pathlib, subprocess, tempfile, unittest
class DualKeyLogicTest(unittest.TestCase):
    def test_grouping_direction_queue_backoff(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-dualkey-logic-') as d:
            binary=pathlib.Path(d)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            str(root/'tests/dualkey_logic_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
