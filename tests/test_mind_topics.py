"""Owner 2026-11-15: hello gets the last half hour of conversation topics (one notes version per 5 minutes)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from mind import notes  # noqa: E402
from mind_fixtures import FakeDB  # noqa: E402

MIN = 60_000
NOW = 1_794_700_000_000


class TopicTimelineTest(unittest.TestCase):
    def test_one_per_five_minutes_oldest_first_repeats_dropped(self):
        db = FakeDB()
        rows = [(-40, "旧话题", "太早了"), (-28, "养猫", "外甥买了猫"), (-26, "养猫", "外甥买了猫，交给家里养"),
                (-21, "养猫", "外甥买了猫，交给家里养"), (-12, "疤痕", "小时候摔的疤"), (-3, "小狗", "吃狗粮拌米饭"),
                (-2, "", "")]
        for i, (m, t, n) in enumerate(rows):
            db["conversation_versions"].insert_one({"cid": 1, "version": i, "at": NOW + m * MIN, "title": t, "notes": n})
        out = notes.topic_timeline(db, NOW, "Asia/Shanghai")
        self.assertEqual([x.split(" ", 1)[1] for x in out],
                         ["养猫：外甥买了猫，交给家里养", "疤痕：小时候摔的疤", "小狗：吃狗粮拌米饭"])
        self.assertEqual(notes.topic_timeline(FakeDB(), NOW, "Asia/Shanghai"), [])


if __name__ == "__main__":
    unittest.main()
