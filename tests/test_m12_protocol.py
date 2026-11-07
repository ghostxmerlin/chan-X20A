"""Host checks for the actual M12 parser and explicit mode policy."""
import pathlib
import subprocess
import tempfile
import unittest


class M12ProtocolTest(unittest.TestCase):
    def test_parser_and_manual_modes(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-m12-") as directory:
            binary = pathlib.Path(directory) / "test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I" + str(root / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                str(root / "tests/m12_protocol_test.cpp"), "-o", str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)
