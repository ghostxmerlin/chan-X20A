"""A second stack (the Mac stand-in, 2026-11-19 night) runs the same code on its own database, ports and log spool:
every place that named the robot's stack reads an environment variable, and without it nothing changes."""
import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "server" / "ear", ROOT / "server", ROOT / "server" / "common"):
    sys.path.insert(0, str(p))

VARS = ("SOUL_DB", "SOUL_MIND_URL", "SOUL_EAR_ASK_URL", "SOUL_TTS_MOUTH_URL", "SOUL_LOG_SPOOL", "SOUL_CODE_VERSION_FILE")


def fresh(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


class SecondStackConfigTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in VARS}
        for k in VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m in ("hello_log", "ear_log", "mouth_link", "signal_link"):
            sys.modules.pop(m, None)

    def test_defaults_are_the_robots_stack(self):
        self.assertEqual(fresh("hello_log").DB_NAME, "hello")
        self.assertEqual(fresh("ear_log").SPOOL_DIR, "~/soul-spike/log-spool")
        ml = fresh("mouth_link")
        self.assertEqual((ml.MIND, ml.UPSTREAM), ("http://127.0.0.1:52740", "ws://127.0.0.1:52751/mouth"))
        self.assertEqual(fresh("signal_link").MIND_URL, "http://127.0.0.1:52740/signal")

    def test_the_mac_stack_reads_its_own(self):
        os.environ.update({"SOUL_DB": "hello_mac", "SOUL_MIND_URL": "http://127.0.0.1:52840",
                           "SOUL_TTS_MOUTH_URL": "ws://127.0.0.1:52851/mouth", "SOUL_LOG_SPOOL": "~/soul-spike-mac/log-spool"})
        self.assertEqual(fresh("hello_log").DB_NAME, "hello_mac")
        self.assertEqual(fresh("ear_log").SPOOL_DIR, "~/soul-spike-mac/log-spool")
        ml = fresh("mouth_link")
        self.assertEqual((ml.MIND, ml.UPSTREAM), ("http://127.0.0.1:52840", "ws://127.0.0.1:52851/mouth"))
        self.assertEqual(fresh("signal_link").MIND_URL, "http://127.0.0.1:52840/signal")

    def test_no_module_names_the_robots_database_any_more(self):
        import re
        bad = []
        for f in list((ROOT / "server" / "mind").glob("*.py")) + list((ROOT / "server" / "ear").glob("*.py")):
            t = f.read_text(encoding="utf-8")
            if re.search(r"""\[["']hello["']\]""", t):
                bad.append(f.name)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
