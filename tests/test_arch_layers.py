"""The server's layers (2026-11-19 refactor, tools/arch/layers.json): every module is assigned a layer and no module
imports one of a higher layer number than its own... i.e. nobody calls upwards, except the imports listed in
layers.json "allowed" (today's state, to be emptied by the refactor)."""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "arch"))
import code_map  # noqa: E402


class LayersTest(unittest.TestCase):
    def test_no_new_upward_import_and_every_module_assigned(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = code_map.main(["--check", "--no-write"])
        self.assertEqual(rc, 0, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
