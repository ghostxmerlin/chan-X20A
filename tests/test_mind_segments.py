import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0   # noqa: E402
from mind import fusion, segments, settings   # noqa: E402
from mind.store import Store   # noqa: E402
from test_mind_fusion import FakeChat   # noqa: E402

MIN = 60_000
T = T0
S = settings.load(None)
ENV = {"base_url": "http://x", "api_key": "k"}


def rows(states, start=T):
    return [{"minute": start + i * MIN, "owner_state": st, "hearing_s": 60, "vision_s": 0} for i, st in enumerate(states)]


class CutTest(unittest.TestCase):
    def test_flicker_shorter_than_3_minutes_does_not_cut(self):
        segs = segments.cut(rows(["seen"] * 5 + ["likely_absent"] * 2 + ["seen"] * 5))
        self.assertEqual([s["cls"] for s in segs], ["present"])
        self.assertEqual((segs[0]["start"], segs[0]["end"], segs[0]["reason"]), (T, None, "start"))

    def test_lasting_change_cuts_at_its_first_minute(self):
        segs = segments.cut(rows(["seen"] * 5 + ["likely_absent"] * 4))
        self.assertEqual([(s["cls"], s["start"]) for s in segs], [("present", T), ("absent", T + 5 * MIN)])
        self.assertEqual(segs[0]["end"], T + 5 * MIN)
        self.assertIsNone(segs[1]["end"])
        self.assertEqual(segs[1]["reason"], "owner:present→absent")

    def test_missing_minutes_make_a_gap(self):
        segs = segments.cut(rows(["seen"] * 3) + rows(["seen"] * 3, start=T + 10 * MIN))
        self.assertEqual([(s["cls"], s["start"], s["end"]) for s in segs],
                         [("present", T, T + 3 * MIN), ("gap", T + 3 * MIN, T + 10 * MIN), ("present", T + 10 * MIN, None)])

    def test_owner_class(self):
        self.assertEqual([segments.owner_class(x) for x in ("seen", "just_left", "likely_present", "likely_absent", "unknown", "sensors_off")],
                         ["present", "present", "present", "absent", "unknown", "off"])


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.fake = FakeChat()
        fusion.CHAT = self.fake
        self.fake_reply = json.dumps({"title": "在桌前工作", "text": "主人一直在桌前。", "confidence": "mid"}, ensure_ascii=False)

    def add(self, start, states):
        for i, st in enumerate(states):
            m = start + i * MIN
            pkg = {"speech": [], "tingwu_only": [], "self_speech": [], "sounds": [], "faces": []}
            strong = {"owner": {"state": st}, "coverage": {"hearing_s": 60, "vision_s": 0, "m12_s": 0, "estimated": False}}
            self.db.add("mind_packages", {"lane": "live", "minute": m, "version": 1, "package": pkg, "strong": strong, "summary": "s", "fingerprint": "f"})
            self.db.add("mind_scenes", {"lane": "live", "minute": m, "version": 1, "source": "model",
                                        "scene": {"summary": f"第{i}分钟", "entries": [{"key": "people.owner", "dimension": "people", "text": "在"}]}})

    def update(self, now):
        self.fake.queue = [(self.fake_reply, None)] * 10
        return segments.update(ENV, self.store, S, T, now)

    def test_closed_segment_gets_summarized_once(self):
        self.add(T, ["seen"] * 5 + ["likely_absent"] * 4)
        segs = self.update(T + 20 * MIN)
        self.assertEqual(len(self.fake.calls), 2)
        self.assertEqual(segs[0]["summary"]["title"], "在桌前工作")
        self.assertEqual(self.db["mind_runs"].docs[0]["step"], "segment")
        self.update(T + 21 * MIN)
        self.assertEqual(len(self.fake.calls), 2)

    def test_open_segment_refreshed_every_5_minutes_with_new_scenes(self):
        self.add(T, ["seen"] * 6)
        self.update(T + 6 * MIN)
        n = len(self.fake.calls)
        self.add(T + 6 * MIN, ["seen"] * 2)
        self.update(T + 8 * MIN)
        self.assertEqual(len(self.fake.calls), n)
        self.add(T + 8 * MIN, ["seen"] * 3)
        self.update(T + 11 * MIN)
        self.assertEqual(len(self.fake.calls), n + 1)

    def test_no_env_means_no_summaries(self):
        self.add(T, ["seen"] * 3)
        segs = segments.update(None, self.store, S, T, T + 5 * MIN)
        self.assertEqual((len(self.fake.calls), segs[0]["summary"]), (0, None))


if __name__ == "__main__":
    unittest.main()
