"""realtime_link keeps its own log / _LOG_FAILED after copying rt_base's names (2026-11-20: rt_base's shims look
realtime_link's up at call time, so copying them over made log call itself; every Docking task died in a
RecursionError on the Mac stack, and the other tests never saw it because they patch log)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "ear"))


class LogShim(unittest.TestCase):
    def test_log_is_not_the_shim(self):
        import realtime_link
        import rt_base
        self.assertIsNot(realtime_link.log, rt_base.log)
        self.assertIsNot(realtime_link._LOG_FAILED, getattr(rt_base, "_LOG_FAILED", None))

    def test_the_shim_reaches_print_without_recursion(self):
        import realtime_link
        import rt_base
        said = []
        old = realtime_link.log
        try:
            realtime_link.log = lambda *a, **k: said.append(a)
            rt_base.log("hello")
            self.assertEqual(said, [("hello",)])
        finally:
            realtime_link.log = old


if __name__ == "__main__":
    unittest.main()
