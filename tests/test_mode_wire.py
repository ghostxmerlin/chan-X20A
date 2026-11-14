import pathlib
import subprocess
import tempfile
import unittest


class ModeWireTest(unittest.TestCase):
    def test_snapshot_inputs_acks_and_queue(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="chan-mode-wire-") as directory:
            binary = pathlib.Path(directory) / "mode-wire"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root / "upstream/StackChan/firmware/main/collector"),
                            "-I" + str(root / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                            str(root / "tests/mode_wire_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
