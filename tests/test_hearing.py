import pathlib
import subprocess
import tempfile
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
class HearingTest(unittest.TestCase):
    def test_coalesced_frame_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=str(pathlib.Path(tmp)/'frame_write')
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            '-I'+str(ROOT/'upstream/StackChan/firmware/main/collector'),
                            str(ROOT/'tests/hearing_frame_write_test.cpp'),'-o',out],check=True)
            subprocess.run([out],check=True)
    def test_listening_intent_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=str(pathlib.Path(tmp)/'intent')
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            '-I'+str(ROOT/'upstream/StackChan/firmware/main/collector'),
                            str(ROOT/'tests/hearing_intent_test.cpp'),'-o',out],check=True)
            subprocess.run([out],check=True)
    def test_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=str(pathlib.Path(tmp)/'transport')
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'upstream/StackChan/firmware/main/collector'),str(ROOT/'tests/hearing_transport_test.cpp'),'-o',out],check=True)
            subprocess.run([out],check=True)
    def test_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(pathlib.Path(tmp)/'hearing')
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'upstream/StackChan/firmware/main/collector'),str(ROOT/'tests/hearing_policy_test.cpp'),'-o',out],check=True)
            subprocess.run([out],check=True)
