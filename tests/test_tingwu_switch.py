import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "ear"))
from tingwu_switch import SwitchReader, decide, tingwu_pos   # noqa: E402


class SwitchReaderTest(unittest.TestCase):
    def test_missing_doc_is_off(self):
        r = SwitchReader(lambda: None)
        r.update()
        self.assertFalse(r.on)

    def test_on_doc_turns_on(self):
        r = SwitchReader(lambda: {"_id": "tingwu", "on": True})
        r.update()
        self.assertTrue(r.on)

    def test_never_read_is_off(self):
        self.assertFalse(SwitchReader(lambda: {"on": True}).on)

    def test_read_errors_keep_last_value_for_60_s_then_off(self):
        t = [0.0]
        docs = [{"on": True}]

        def read():
            if not docs:
                raise RuntimeError("db down")
            return docs[0]
        r = SwitchReader(read, now=lambda: t[0])
        r.update()
        docs.clear()
        t[0] = 59
        r.update()
        self.assertTrue(r.on)
        t[0] = 61
        r.update()
        self.assertFalse(r.on)


class DecideTest(unittest.TestCase):
    def test_decide(self):
        self.assertEqual(decide(True, False, True, True, True), "start")
        self.assertIsNone(decide(True, False, True, True, False))      # backing off after an error
        self.assertIsNone(decide(True, False, False, True, True))      # no keys on this server
        self.assertIsNone(decide(True, False, True, False, True))      # audio message not seen yet
        self.assertEqual(decide(False, True, True, True, True), "stop")
        self.assertIsNone(decide(True, True, True, True, True))
        self.assertIsNone(decide(False, False, True, True, True))


class PosTest(unittest.TestCase):
    def test_pos_adds_the_offset(self):
        self.assertEqual(tingwu_pos(1000, 2500, 48000), [64000, 88000])
        self.assertEqual(tingwu_pos(0, 500, 0), [0, 8000])
        self.assertIsNone(tingwu_pos(None, 2500, 0))


if __name__ == "__main__":
    unittest.main()
