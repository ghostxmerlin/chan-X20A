import pathlib, subprocess, tempfile, unittest
class AsrMotionTest(unittest.TestCase):
    def test_spoken_yaw_words_and_two_stage_plans(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chan-asr-motion-') as d:
            binary=pathlib.Path(d)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            str(root/'tests/asr_motion_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
