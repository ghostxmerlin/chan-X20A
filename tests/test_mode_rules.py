import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTORS = ROOT / "tests/data/mode_vectors.json"


class ModeRulesTest(unittest.TestCase):
    def test_firmware_rules_replay_every_vector(self):
        with tempfile.TemporaryDirectory(prefix="chan-mode-rules-") as directory:
            binary = pathlib.Path(directory) / "mode-rules"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(ROOT / "upstream/StackChan/firmware/main/collector"),
                            "-I" + str(ROOT / "upstream/StackChan/firmware/components/ArduinoJson/src"),
                            str(ROOT / "tests/mode_rules_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary), str(VECTORS)], check=True)

    def test_vectors_match_the_rules_in_modes_py(self):
        # The committed vectors are what tests/gen_mode_vectors.py makes from today's modes.py / mode_table.py.
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import gen_mode_vectors as gen
        finally:
            sys.path.pop(0)
        body = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(body["table"], gen.table())
        fresh = []
        for st in gen.states():
            for inp, value in gen.inputs():
                for speaking in (False, True):
                    new, res, effects = gen.run(st, inp, value, speaking)
                    fresh.append([[st[k] for k in gen.FIELDS], inp, value, speaking,
                                  [new[k] for k in gen.FIELDS], res["code"], res["toast"], effects])
        self.assertEqual(body["vectors"], fresh, "regenerate: python3 -B tests/gen_mode_vectors.py")


if __name__ == "__main__":
    unittest.main()
