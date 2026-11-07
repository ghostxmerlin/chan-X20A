"""Host regression for discovery nonce and pitch request boundaries."""
import pathlib
import subprocess
import tempfile
import unittest


class ConnectionProtocolTest(unittest.TestCase):
    def test_request_boundaries(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-connection-") as directory:
            binary = pathlib.Path(directory) / "connection-test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                str(root / "tests/connection_protocol_test.cpp"), "-o", str(binary),
            ], check=True, cwd=root)
            subprocess.run([str(binary)], check=True, cwd=root)
