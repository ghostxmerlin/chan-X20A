import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB                      # noqa: E402
from mind import settings                             # noqa: E402
from mind.settings_store import SettingsStore, validate   # noqa: E402


def path_with(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write(text); f.close()
    return f.name


class SettingsStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.st = SettingsStore(self.db, path_with('{"owner": {"display": "X", "voice_names": ["me"], "face_names": ["me"]}}'))

    def test_first_current_bootstraps_version_0_from_file(self):
        s, v = self.st.current()
        self.assertEqual((v, s["owner"]["display"], s["fusion"]["model"]), (0, "X", "gpt-6-luna"))
        self.assertEqual(self.db.count("mind_settings"), 1)
        self.st.current()
        self.assertEqual(self.db.count("mind_settings"), 1)

    def test_save_creates_next_version_and_current_follows(self):
        s, _ = self.st.current()
        s["place"]["default"] = "家里"
        self.assertEqual(self.st.save(s, "改地点"), 1)
        self.assertEqual(self.st.current(), (s, 1))
        self.assertEqual([h["version"] for h in self.st.history()], [1, 0])
        self.assertNotIn("settings", self.st.history()[0])

    def test_invalid_settings_rejected_and_previous_stays(self):
        s, _ = self.st.current()
        bad = copy.deepcopy(s); bad["owner"]["voice_names"] = []
        with self.assertRaises(ValueError) as e:
            self.st.save(bad, "")
        self.assertIn("voice_names", str(e.exception))
        self.assertEqual(self.st.current()[1], 0)

    def test_validate_rules(self):
        s = settings.load(None)
        self.assertEqual(validate(s), [])
        self.assertEqual(validate({**s, "environment": "办公室布局……"}), [])
        self.assertTrue(any("environment" in e for e in validate({**s, "environment": "x" * 3001})))
        self.assertTrue(any("self_facts" in e for e in validate({**s, "self_facts": "x" * 2001})))
        self.assertTrue(any("start" in e for e in validate({**s, "work": {"days": [1], "start": "19:00", "end": "09:00"}})))
        self.assertTrue(any("unknown" in e for e in validate({**s, "colour": 1})))
        self.assertTrue(any("model" in e for e in validate({**s, "fusion": {**s["fusion"], "model": "gpt-x"}})))
        self.assertTrue(any("display" in e for e in validate({**s, "people": [{"display": "", "voice_names": [], "face_names": []}]})))
        self.assertTrue(any("budget" in e for e in validate({**s, "budget_usd_per_day": "a"})))

    def test_validate_types_tz_and_bools(self):
        s = settings.load(None)
        self.assertTrue(any("tz" in e for e in validate({**s, "tz": "Mars/X"})))
        self.assertTrue(any("tz" in e for e in validate({**s, "tz": 5})))
        for k in ("owner", "place", "sources", "work", "fusion", "thresholds"):
            self.assertTrue(any(k in e for e in validate({**s, k: "x"})), k)
        self.assertTrue(any("max_age_min" in e for e in validate({**s, "fusion": {**s["fusion"], "max_age_min": True}})))
        self.assertTrue(any("review_after_min" in e for e in validate({**s, "fusion": {**s["fusion"], "review_after_min": [True, 1, 2, 3]}})))

    def test_get_returns_an_old_version(self):
        s, _ = self.st.current()
        s2 = copy.deepcopy(s); s2["place"]["default"] = "家里"; self.st.save(s2, "")
        self.assertEqual(self.st.get(0)["place"]["default"], s["place"]["default"])

    def test_owner_alias_is_one_per_day_and_stable(self):
        s = settings.load(None)
        s["owner"]["aliases"] = ["Kira", "Dave", "无限", "劳埃德"]
        day = 1794300000000
        a = settings.owner_name(s, day)
        self.assertIn(a, s["owner"]["aliases"])
        self.assertEqual(settings.owner_name(s, day + 3 * 3600_000), a)                        # same day
        names = {settings.owner_name(s, day + k * 86400_000) for k in range(30)}
        self.assertGreater(len(names), 1)                                                     # rotates over days
        s["owner"]["aliases"] = []
        self.assertEqual(settings.owner_name(s, day), s["owner"]["display"])
        self.assertTrue(any("aliases" in e for e in validate({**s, "owner": {**s["owner"], "aliases": ["x" * 30]}})))

    def test_defaults_have_phase1_fields(self):
        s = settings.load(None)
        self.assertEqual(s["sources"]["speech_text"], True)
        self.assertEqual(s["fusion"]["review_after_min"], [5, 10, 20, 30])
        self.assertEqual(s["budget_usd_per_day"], 20.0)
        self.assertIn("hello", s["self_facts"])


if __name__ == "__main__":
    unittest.main()
