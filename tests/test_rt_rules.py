"""rt_rules (2026-11-19 refactor 4.2.9): the reply gate's situation decisions as pure functions, table-tested."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import rt_rules  # noqa: E402


class ModeMuteTest(unittest.TestCase):
    def mm(self, kind="speech", order=None, called=False, debug=False, quiet=False, low=False, addressed=False):
        return rt_rules.mode_mute(kind, order, 4.0, called, debug, quiet, low, addressed)

    def test_table(self):
        self.assertIsNone(self.mm())
        self.assertIsNone(self.mm(kind="greet", quiet=True))            # only speech replies
        self.assertEqual(self.mm(order=1.0), "order")
        self.assertEqual(self.mm(order=1.0, called=True), "order")      # an order wins over a call
        self.assertIsNone(self.mm(order=5.0))
        self.assertIsNone(self.mm(called=True, debug=True, quiet=True, low=True))
        self.assertEqual(self.mm(debug=True, quiet=True), "debug_mode")
        self.assertEqual(self.mm(quiet=True, low=True), "quiet_mode")
        self.assertEqual(self.mm(low=True), "low_mode")
        self.assertIsNone(self.mm(low=True, addressed=True))              # plainly for hello passes 少插话

    def test_waits(self):
        w = {"sent": False, "at": 10.0, "done": False}
        self.assertTrue(rt_rules.look_wait_holds(w, 13.0))
        self.assertFalse(rt_rules.look_wait_holds(w, 15.0))
        self.assertFalse(rt_rules.look_wait_holds(None, 0))
        self.assertTrue(rt_rules.search_wait_holds(w, 30.0, False))      # not done yet: still waits
        self.assertFalse(rt_rules.search_wait_holds({**w, "done": True}, 30.0, False))
        self.assertTrue(rt_rules.search_wait_holds(None, 0, True))       # a search answer being redone


if __name__ == "__main__":
    unittest.main()
