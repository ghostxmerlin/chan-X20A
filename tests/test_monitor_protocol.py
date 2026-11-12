import pathlib, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class MonitorProtocolTest(unittest.TestCase):
    def test_sender_allows_progress_but_bounds_stalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=pathlib.Path(tmp)/'sender'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'upstream/StackChan/firmware/main/collector'),str(ROOT/'tests/monitor_send_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
    def test_delta_preserves_unchanged_fields_and_nulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=pathlib.Path(tmp)/'delta'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'upstream/StackChan/firmware/main/collector'),'-I',str(ROOT/'upstream/StackChan/firmware/components/ArduinoJson/src'),str(ROOT/'tests/monitor_delta_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
    def test_wire_crc_bounds_and_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=pathlib.Path(tmp)/'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'upstream/StackChan/firmware/main/collector'),str(ROOT/'tests/monitor_protocol_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
