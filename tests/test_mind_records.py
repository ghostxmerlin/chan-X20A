import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mind_fixtures import FakeDB, T0, session, speech, vision
from mind import records, settings
from mind.records import Records


def path_with(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write(text); f.close()
    return f.name


class RecordsTest(unittest.TestCase):
    def test_speech_is_filed_by_the_ear_clock_and_pos_gives_the_drift(self):
        doc = {"pos": [16000 * 30, 16000 * 31], "t": 1_037_000, "at": 1_037_000}
        self.assertEqual(records.wall_ms(doc, 1_000_000, "speech"), (1_037_000, 7_000))
        self.assertEqual(records.wall_ms({"t": 5}, None, "sounds"), (5, None))

    def test_tingwu_is_filed_by_pos_since_its_at_is_arrival(self):
        doc = {"pos": [16000 * 30, 16000 * 33], "at": 1_000_000 + 45_000}
        self.assertEqual(records.wall_ms(doc, 1_000_000, "tingwu"), (1_030_000, None))
        self.assertEqual(records.wall_ms({"at": 9}, None, "tingwu"), (9, None))

    def test_minute_boundary_belongs_to_new_minute(self):
        self.assertEqual(settings.minute_floor(120_000), 120_000)
        self.assertEqual(settings.minute_floor(179_999), 120_000)

    def test_settings_deep_merge_keeps_defaults(self):
        s = settings.load(path_with('{"owner": {"display": "X"}}'))
        self.assertEqual(s["owner"]["display"], "X")
        self.assertEqual(s["owner"]["voice_names"], ["me"])
        self.assertEqual(s["thresholds"]["face_tail_s"], 120)
        self.assertEqual(settings.load(None)["tz"], "Asia/Shanghai")
        self.assertEqual(settings.load("/nonexistent/x.json")["work"]["start"], "09:00")

    def test_load_env(self):
        env = settings.load_env(path_with('# c\nA=1\nB="two"\n'))
        self.assertEqual(env, {"A": "1", "B": "two"})
        self.assertEqual(settings.load_env("/nonexistent"), {})

    def test_local_label_and_work_hours(self):
        s = settings.load(None)
        self.assertEqual(settings.local_label(T0, "Asia/Shanghai"), "11-10 16:40")
        self.assertTrue(settings.in_work_hours(T0, s))                       # Tuesday 16:40
        self.assertFalse(settings.in_work_hours(T0 + 3 * 3600_000, s))       # 19:40

    def test_sounds_are_found_by_t_since_they_have_no_at(self):
        db = FakeDB()
        db.add("sessions", session("A", T0 - 600_000))
        db.add("sounds", {"session": "A", "id": "a-1", "t": T0 + 1_000, "kind": "typing", "pos": [16000 * 601, 16000 * 602]})
        self.assertEqual([d["id"] for d in Records(db).window(T0, T0 + 60_000)["sounds"]], ["a-1"])

    def test_window_returns_overlapping_sessions_and_marks_wall(self):
        db = FakeDB()
        db.add("sessions", session("A", T0 - 600_000), session("B", T0 - 900_000, T0 - 300_000),
               session("C", T0 - 120_000, T0 + 600_000))
        db.add("speech", speech("A", "s-1", T0 + 10_000, pos=[16000 * 610, 16000 * 611]),     # T0+10s
               speech("A", "s-2", T0 + 70_000, pos=[16000 * 600, 16000 * 601]),               # t: next minute
               speech("C", "s-1", T0 + 59_000))
        db.add("vision", vision("C", "face_found", T0 + 5_000, face=3), vision("C", "face_lost", T0 + 61_000, face=3))
        w = Records(db).window(T0, T0 + 60_000)
        self.assertEqual({s["session"] for s in w["sessions"]}, {"A", "C"})
        self.assertEqual(sorted((d["session"], d["id"]) for d in w["speech"]), [("A", "s-1"), ("C", "s-1")])
        self.assertTrue(all("_wall" in d for d in w["speech"]))
        self.assertEqual([v["event"] for v in w["vision"]], ["face_found"])


if __name__ == "__main__":
    unittest.main()
