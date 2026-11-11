import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, sound, speech, vision   # noqa: E402
from mind import runner, settings                                     # noqa: E402
from mind.records import Records                                      # noqa: E402
from mind.store import Store                                          # noqa: E402
from mind import fusion                                               # noqa: E402
from test_mind_fusion import FakeChat                                 # noqa: E402

ENV = {"base_url": "http://x", "api_key": "k"}

S = settings.load(None)
MIN = 60_000


class BrokenRecords:
    def window(self, *a):
        raise RuntimeError("db down")

    def sessions(self, *a):
        raise RuntimeError("db down")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.db.add("sessions", session("H", T0 - 3600_000, source="stackchan-dev"),
                    session("V", T0 - 3600_000, source="stackchan-dev", kind="vision"))
        self.db.add("speech", speech("H", "s-1", T0 + 1000, band="known", name="me"), speech("H", "s-2", T0 + 2000))
        for i in range(3):
            self.db.add("sounds", sound("H", f"a-{i}", T0 + 3000 + i * 1000))
        self.db.add("vision", vision("V", "face_found", T0 + 500, face=4), vision("V", "face_named", T0 + 900, face=4, name="me"))
        self.rec, self.store = Records(self.db), Store(self.db)
        self.store.ensure_indexes()

    def process(self, minute):
        return runner.process_minute(self.rec, self.store, minute, S, minute + 10 * MIN)

    def test_owner_reported_place_reaches_strong_elements(self):
        self.db.add("mind_labels", {"lane": "live", "kind": "place", "label": "办公室", "at": T0 - 3600_000})
        self.db.add("mind_labels", {"lane": "live", "kind": "place", "label": "家里", "at": T0 + 5 * MIN})
        self.process(T0)
        place = self.store.latest("live", T0)["strong"]["place"]
        self.assertEqual((place["label"], place["source"], place["at"]), ("办公室", "owner_report", T0 - 3600_000))
        self.process(T0 + 6 * MIN)
        self.assertEqual(self.store.latest("live", T0 + 6 * MIN)["strong"]["place"]["label"], "家里")

    def test_indexes_for_every_mind_table(self):
        made = {name: c.indexes for name, c in self.db.collections.items() if getattr(c, "indexes", None)}
        for name in ("mind_packages", "mind_scenes", "mind_runs", "mind_segments", "mind_labels", "mind_settings"):
            self.assertIn(name, made)
        self.assertIn(([("run_id", 1)], True), made["mind_runs"])

    def test_unchanged_minute_is_not_rewritten(self):
        self.assertEqual(self.process(T0), "written")
        self.assertEqual(self.process(T0), "same")
        self.assertEqual(self.db.count("mind_packages"), 1)

    def test_late_record_creates_version_2(self):
        self.process(T0)
        self.db.add("speech", speech("H", "s-9", T0 + 30_000))
        self.assertEqual(self.process(T0), "written")
        self.assertEqual(self.store.latest("live", T0)["version"], 2)
        self.assertEqual(self.store.latest("live", T0)["package"]["speech"][-1]["ref"]["id"], "s-9")

    def test_changed_settings_create_a_new_version(self):
        import copy
        self.process(T0)
        s2 = copy.deepcopy(S); s2["tz"] = "UTC"          # same package, different strong elements (time label)
        self.assertEqual(runner.process_minute(self.rec, self.store, T0 + 30 * MIN, S, T0 + 40 * MIN), "written")
        self.assertEqual(runner.process_minute(self.rec, self.store, T0 + 30 * MIN, s2, T0 + 40 * MIN), "written")
        self.assertEqual(self.store.latest("live", T0 + 30 * MIN)["version"], 2)
        self.assertEqual(runner.process_minute(self.rec, self.store, T0 + 30 * MIN, s2, T0 + 40 * MIN), "same")

    def test_tick_catches_up_after_downtime(self):
        self.store.set_state(cursor_minute=T0)
        self.assertEqual(runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 80_000), 9)
        self.assertEqual(self.store.state()["cursor_minute"], T0 + 9 * MIN)
        self.assertEqual(self.store.state()["last_error"], "")

    def test_tick_without_cursor_starts_an_hour_back(self):
        self.assertEqual(runner.tick(self.rec, self.store, S, T0 + 140_000), 60)
        self.assertEqual(self.store.state()["cursor_minute"], T0)

    def test_tick_without_a_new_minute_reprocesses_nothing(self):
        calls = []
        real = runner.process_minute
        runner.process_minute = lambda *a: calls.append(a[2]) or real(*a)
        try:
            runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 80_000)
            n = len(calls)
            runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 85_000)
            self.assertEqual(len(calls), n)                      # same last ready minute: no work
        finally:
            runner.process_minute = real

    def test_minute_is_not_processed_before_delay(self):
        self.store.set_state(cursor_minute=T0)
        self.assertEqual(runner.tick(self.rec, self.store, S, T0 + MIN + 79_000), 0)

    def test_db_error_keeps_cursor(self):
        self.store.set_state(cursor_minute=T0)
        with self.assertRaises(RuntimeError):
            runner.tick(BrokenRecords(), self.store, S, T0 + 5 * MIN)
        self.assertEqual(self.store.state()["cursor_minute"], T0)
        self.assertIn("RuntimeError", self.store.state()["last_error"])

    def test_timeline_returns_latest_version_only(self):
        self.process(T0)
        self.db.add("speech", speech("H", "s-9", T0 + 30_000))
        self.process(T0)
        rows = self.store.timeline("live", T0, T0 + MIN)
        self.assertEqual([r["version"] for r in rows], [2])
        self.assertEqual(rows[0]["owner_state"], "seen")
        self.assertEqual(rows[0]["counts"]["speech"], 3)

    def test_summary_text(self):
        self.process(T0)
        self.assertEqual(self.store.timeline("live", T0, T0 + MIN)[0]["summary"], "说话 2 句（me 1）· 打字 3 · 脸：me")

    def test_face_found_long_before_lookback_is_still_seen(self):
        # the face appeared 50 min ago (beyond the 30 min lookback) in the open vision session, never left
        self.db.add("vision", vision("V", "face_found", T0 - 50 * MIN, face=9, name="me"))
        self.process(T0 + 5 * MIN)
        names = self.store.latest("live", T0 + 5 * MIN)["package"]["faces"]
        self.assertIn(9, [f["face"] for f in names])


class RunnerFusionTest(RunnerTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeChat()
        fusion.CHAT = self.fake

    def test_tick_fuses_new_recent_minutes_only(self):
        self.store.set_state(cursor_minute=T0)
        now = T0 + 3 * 3600_000
        runner.tick(self.rec, self.store, S, now, 48 * 3600_000, env=ENV)
        fused = sorted({d["minute"] for d in self.db["mind_scenes"].docs})
        self.assertGreater(self.db.count("mind_packages"), 170)
        self.assertTrue(fused and fused[0] >= now - 60 * MIN - 140_000)
        self.assertEqual(len(fused), 60)

    def test_late_speech_revises_that_minute(self):
        now = T0 + 10 * MIN + 80_000
        self.store.set_state(cursor_minute=T0)
        runner.tick(self.rec, self.store, S, now, 48 * 3600_000, env=ENV)
        n = self.db.count("mind_scenes")
        self.db.add("speech", speech("H", "s-late", T0 + 5 * MIN + 1000))
        runner.tick(self.rec, self.store, S, now + MIN, 48 * 3600_000, env=ENV)
        self.assertEqual(self.db.count("mind_scenes"), n + 2)          # the new minute + one revision
        self.assertEqual(self.scene_versions(T0 + 5 * MIN)[-1][1], "revised")

    def test_fusion_disabled_writes_no_scenes(self):
        import copy
        s = copy.deepcopy(S); s["fusion"]["enabled"] = False
        self.store.set_state(cursor_minute=T0)
        runner.tick(self.rec, self.store, s, T0 + 10 * MIN + 80_000, 48 * 3600_000, env=ENV)
        self.assertEqual((self.db.count("mind_scenes"), self.fake.calls), (0, []))

    def test_no_env_writes_no_scenes(self):
        self.store.set_state(cursor_minute=T0)
        runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 80_000, 48 * 3600_000)
        self.assertEqual(self.db.count("mind_scenes"), 0)

    def test_segment_summaries_run_after_fusion(self):
        self.store.set_state(cursor_minute=T0)
        runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 80_000, 48 * 3600_000, env=ENV)
        self.assertIn("segment", {d["step"] for d in self.db["mind_runs"].docs})


    def test_mind_switched_off_builds_packages_but_calls_nothing(self):
        self.store.set_state(cursor_minute=T0, enabled=False)
        runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 80_000, 48 * 3600_000, env=ENV)
        self.assertGreater(self.db.count("mind_packages"), 5)
        self.assertEqual((self.db.count("mind_scenes"), self.db.count("mind_runs"), self.fake.calls), (0, 0, []))

    def test_mind_switched_back_on_calls_again(self):
        self.store.set_state(cursor_minute=T0, enabled=False)
        runner.tick(self.rec, self.store, S, T0 + 10 * MIN + 80_000, 48 * 3600_000, env=ENV)
        self.store.set_state(enabled=True)
        runner.tick(self.rec, self.store, S, T0 + 11 * MIN + 80_000, 48 * 3600_000, env=ENV)
        self.assertTrue(self.fake.calls)

    def test_both_senses_off_calls_nothing_not_even_segment_summaries(self):
        db = FakeDB()
        db.add("sessions", session("H", T0 - 3600_000, end_ms=T0 - 1800_000, source="stackchan-dev"))
        db.add("speech", speech("H", "s-old", T0 - 3000_000, band="known", name="me"))
        store = Store(db)
        store.ensure_indexes()
        store.set_state(cursor_minute=T0)
        runner.tick(Records(db), store, S, T0 + 10 * MIN + 80_000, 48 * 3600_000, env=ENV)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual({d["reason"] for d in db["mind_scenes"].docs}, {"sensors_off"})


    # ---- mind v3: first version 30 s after the minute; late records revise it ----
    def scene_versions(self, minute):
        return sorted((d["version"], d["reason"]) for d in self.db["mind_scenes"].docs if d["minute"] == minute and d["lane"] == "live")

    def test_minute_is_processed_30_s_after_it_ends(self):
        self.store.set_state(cursor_minute=T0 - MIN)
        runner.tick(self.rec, self.store, S, T0 + MIN + 29_000, 48 * 3600_000, env=ENV)
        self.assertEqual(self.store.state()["cursor_minute"], T0 - MIN)
        runner.tick(self.rec, self.store, S, T0 + MIN + 30_000, 48 * 3600_000, env=ENV)
        self.assertEqual(self.store.state()["cursor_minute"], T0)

    def eye_result(self, minute):
        self.db.add("eye_minutes", {"minute": minute, "version": 1, "source": "model", "frames": [{"seq": 1}],
                                    "result": {"people_count": 1, "people": [{"who": "主人", "where": "桌前", "doing": "打字"}],
                                               "activity": "主人在打字", "scene": "办公室", "changes": "", "confidence": "high"}})

    def test_at_most_3_revisions_per_tick(self):
        import copy
        s3 = copy.deepcopy(S); s3["fusion"]["prompt"] = "v3"
        self.store.set_state(cursor_minute=T0 - MIN)
        runner.tick(self.rec, self.store, s3, T0 + 8 * MIN + 30_000, 48 * 3600_000, env=ENV)
        for k in range(8):
            self.eye_result(T0 + k * MIN)
        runner.tick(self.rec, self.store, s3, T0 + 9 * MIN + 30_000, 48 * 3600_000, env=ENV)
        revised = [d for d in self.db["mind_scenes"].docs if d.get("reason") == "revised"]
        self.assertEqual(len(revised), 3)

    def test_late_eye_result_revises_the_minute_once(self):
        import copy
        S = copy.deepcopy(globals()["S"]); S["fusion"]["prompt"] = "v3"
        self.store.set_state(cursor_minute=T0 - MIN)
        runner.tick(self.rec, self.store, S, T0 + MIN + 30_000, 48 * 3600_000, env=ENV)
        self.assertEqual(len(self.scene_versions(T0)), 1)
        self.eye_result(T0)
        runner.tick(self.rec, self.store, S, T0 + 2 * MIN + 30_000, 48 * 3600_000, env=ENV)
        self.assertEqual(self.scene_versions(T0)[-1], (2, "revised"))
        runner.tick(self.rec, self.store, S, T0 + 3 * MIN + 30_000, 48 * 3600_000, env=ENV)
        self.assertEqual(len(self.scene_versions(T0)), 2)

    def test_rewritten_package_with_same_content_is_not_revised(self):
        self.store.set_state(cursor_minute=T0 - MIN)
        runner.tick(self.rec, self.store, S, T0 + MIN + 30_000, 48 * 3600_000, env=ENV)
        n = len(self.fake.calls)
        self.db.add("sounds", sound("H", "a-late", T0 + 50_000))           # one more sound of the same kind
        runner.tick(self.rec, self.store, S, T0 + 2 * MIN + 30_000, 48 * 3600_000, env=ENV)
        self.assertEqual(len(self.scene_versions(T0)), 1)
        self.assertEqual(len(self.store.versions("live", T0)), 2)          # the package did get a new version

    def test_no_revision_when_mind_is_off(self):
        self.store.set_state(cursor_minute=T0 - MIN)
        runner.tick(self.rec, self.store, S, T0 + MIN + 30_000, 48 * 3600_000, env=ENV)
        self.store.set_state(enabled=False)
        self.eye_result(T0)
        runner.tick(self.rec, self.store, S, T0 + 2 * MIN + 30_000, 48 * 3600_000, env=ENV)
        self.assertEqual(len(self.scene_versions(T0)), 1)

    def test_revision_runs_are_logged(self):
        import copy
        S = copy.deepcopy(globals()["S"]); S["fusion"]["prompt"] = "v3"
        self.store.set_state(cursor_minute=T0 - MIN)
        runner.tick(self.rec, self.store, S, T0 + MIN + 30_000, 48 * 3600_000, env=ENV)
        before = sum(1 for d in self.db["mind_runs"].docs if d.get("minute") == T0 and d["step"] == "fusion")
        self.eye_result(T0)
        runner.tick(self.rec, self.store, S, T0 + 2 * MIN + 30_000, 48 * 3600_000, env=ENV)
        after = sum(1 for d in self.db["mind_runs"].docs if d.get("minute") == T0 and d["step"] == "fusion")
        self.assertEqual(after, before + 1)

if __name__ == "__main__":
    unittest.main()
