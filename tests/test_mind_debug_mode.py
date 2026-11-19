"""调试模式, mind's half (server/mind/debug_mode.py, 2026-11-19): the session follows the ear's mode, new speech becomes
notes through the model, a reminder goes to the ear at most every ALERT_S, the end closes the session; debug_log /
debug_summary read it."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from mind_fixtures import FakeDB  # noqa: E402
from mind import debug_mode  # noqa: E402
from mind.tools import ToolError  # noqa: E402

T0 = int(time.time() * 1000) - 3600_000


class DebugModeTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.now = [T0 + 600_000]
        self.calls, self.asked, self.replies = [], [], []
        self.w = debug_mode.Worker(self.db, {"api_key": "k"}, store=None, settings_fn=dict, ask=self.ask,
                                   clock_ms=lambda: self.now[0], run_json=self.run_json)

    def ask(self, name, args):
        self.asked.append((name, args))
        return {"result": "queued"}

    def run_json(self, env, store, s, step, prompt, schema, messages, now):
        self.calls.append((step, messages[1]["content"]))
        out = self.replies.pop(0) if self.replies else {"topic": "I2C", "notes": [], "alert": {"say": "", "why": "none", "refs": []}}
        return out, "run1", None

    def on(self):
        self.db["switches"].replace_one({"_id": "rt_modes_status"}, {"_id": "rt_modes_status",
                                        "debug": {"since_ms": T0, "until_ms": None, "open": True}}, upsert=True)

    def say(self, at, text, own=False):
        self.db["speech"].insert_one({"t": at, "text": text, "speaker": {"name": "me"}, "self": own})

    def test_notes_alert_and_end(self):
        self.assertEqual(self.w.tick(), "off")
        self.on()
        self.assertEqual(self.w.tick(), "quiet")
        self.say(T0 + 1000, "I2C 又报 NACK 了")
        self.say(T0 + 2000, "我说的", own=True)
        self.assertEqual(self.w.tick(), "notes +0")                    # the first scan goes at once
        self.assertIn("I2C 又报 NACK 了", self.calls[-1][1])
        self.assertNotIn("我说的", self.calls[-1][1])                    # hello's own lines are not notes
        hhmmss = time.strftime("%H:%M:%S", time.localtime((T0 + 5000) / 1000))
        self.replies = [{"topic": "Tab5 触摸", "notes": [
            {"kind": "error", "text": "又报 NACK", "key": "I2C NACK", "value": "", "unit": "", "at": hhmmss},
            {"kind": "reading", "text": "电流 120 mA", "key": "电流", "value": "120", "unit": "mA", "at": hhmmss}],
            "alert": {"say": "NACK 已经第三次了，要不要先拔掉 Port2 试试", "why": "repeat", "refs": ["HW-001"]}}]
        self.say(T0 + 5000, "电流 120 毫安，又 NACK")
        self.assertEqual(self.w.tick(), "wait")                         # < NEW_MIN and < SCAN_S
        self.now[0] += debug_mode.SCAN_S * 1000
        self.assertEqual(self.w.tick(), "notes +2")
        self.assertEqual(self.asked[-1][0], "debug_alert")
        doc = self.db[debug_mode.COLL].find_one({})
        self.assertEqual((doc["topic"], len(doc["notes"]), doc["notes"][1]["value"], len(doc["alerts"])), ("Tab5 触摸", 2, 120.0, 1))
        # a second reminder inside ALERT_S is not sent
        self.replies = [{"topic": "", "notes": [], "alert": {"say": "又一件", "why": "other", "refs": []}}]
        self.say(self.now[0] + 1, "还是不行")
        self.now[0] += debug_mode.SCAN_S * 1000
        self.w.tick()
        self.assertEqual(len(self.asked), 1)
        # the tools
        t = debug_mode.tools(self.db, clock_ms=lambda: self.now[0])
        log = t["debug_log"]({})
        self.assertEqual((log["topic"], log["on"], log["counts"]), ("Tab5 触摸", True, {"I2C NACK": 1, "电流": 1}))
        self.assertEqual(t["debug_log"]({"query": "电流"})["notes"][0]["unit"], "mA")
        # the mode ends: the session closes
        self.db["switches"].replace_one({"_id": "rt_modes_status"}, {"_id": "rt_modes_status", "debug": None})
        self.assertEqual(self.w.tick(), "off")
        self.assertIsNotNone(self.db[debug_mode.COLL].find_one({})["end_ms"])
        self.assertFalse(t["debug_log"]({})["on"])

    def test_summary_and_errors(self):
        t = debug_mode.tools(self.db, env={"api_key": "k"}, store=object(), clock_ms=lambda: self.now[0],
                             run_json=lambda *a, **k: ({"spoken": "调了触摸，NACK 还在。", "tried": ["拔 Port2：好了"], "open": []},
                                                       "r", None))
        with self.assertRaises(ToolError):
            t["debug_log"]({})
        self.db[debug_mode.COLL].insert_one({"_id": "dbg-1", "start_ms": T0, "end_ms": T0 + 1, "notes": [
            {"kind": "error", "text": "NACK", "key": "I2C", "at_ms": T0 + 1}], "alerts": [], "topic": "触摸"})
        out = t["debug_summary"]({})
        self.assertEqual(out["spoken"], "调了触摸，NACK 还在。")
        self.assertEqual(self.db[debug_mode.COLL].find_one({"_id": "dbg-1"})["summary"]["tried"], ["拔 Port2：好了"])


if __name__ == "__main__":
    unittest.main()
