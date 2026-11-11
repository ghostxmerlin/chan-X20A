import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, vision   # noqa: E402
from mind import fusion                                  # noqa: E402
from mind.settings_store import SettingsStore           # noqa: E402
from test_mind_fusion import FakeChat                   # noqa: E402
import copy                                             # noqa: E402
from mind import api, runner, settings                  # noqa: E402
from mind.records import Records                        # noqa: E402
from mind.store import Store                            # noqa: E402

MIN = 60_000


class Reply:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def json(self):
        return json.loads(self.body)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = FakeDB()
        db.add("sessions", session("H", T0 - 3600_000, source="stackchan-dev"))
        db.add("speech", speech("H", "s-1", T0 + 1000))
        cls.store = Store(db)
        rec = Records(db)
        s = settings.load(None)
        runner.process_minute(rec, cls.store, T0, s, T0 + 10 * MIN)
        db.add("speech", speech("H", "s-2", T0 + 2000))
        runner.process_minute(rec, cls.store, T0, s, T0 + 10 * MIN)
        cls.store.set_state(cursor_minute=T0)
        cls.server = api.make_server(cls.store, "k", "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, path, token="k", method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return Reply(r.status, r.read())
        except urllib.error.HTTPError as e:
            with e:
                return Reply(e.code, e.read())

    def test_eye_latest_404(self):
        self.assertEqual(self.call("/eye/latest").status, 404)

    def test_latest_frame_404_without_frames(self):
        self.assertEqual(self.call("/frames/latest").status, 404)

    def test_requires_token(self):
        self.assertEqual(self.call("/state", token=None).status, 401)
        self.assertEqual(self.call("/state", token="wrong").status, 401)

    def test_state(self):
        r = self.call("/state").json()
        self.assertEqual((r["lane"], r["cursor_minute"]), ("live", T0))
        self.assertIn("now_ms", r)

    def test_timeline(self):
        rows = self.call(f"/timeline?from={T0}&to={T0 + MIN}").json()
        self.assertEqual([(x["minute"], x["version"]) for x in rows], [(T0, 2)])

    def test_timeline_range_limit(self):
        self.assertEqual(self.call(f"/timeline?from=0&to={25 * 3600_000}").status, 400)
        self.assertEqual(self.call("/timeline?from=x&to=1").status, 400)

    def test_minute_versions(self):
        r = self.call(f"/minute?t={T0}").json()
        self.assertEqual((r["versions"], r["version"]), ([1, 2], 2))
        self.assertEqual(len(r["package"]["speech"]), 2)
        self.assertEqual(self.call(f"/minute?t={T0}&version=1").json()["version"], 1)

    def test_minute_missing(self):
        self.assertEqual(self.call(f"/minute?t={T0 + 600_000}").status, 404)

    def test_unknown_path(self):
        self.assertEqual(self.call("/nope").status, 404)

    def test_post_rejected(self):
        self.assertEqual(self.call("/state", method="POST").status, 405)



class ApiV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = db = FakeDB()
        db.add("sessions", session("H", T0 - 3600_000, source="stackchan-dev"), session("V", T0 - 3600_000, source="stackchan-dev", kind="vision"))
        db.add("speech", speech("H", "s-1", T0 + 1000, band="known", name="me"))
        db.add("vision", vision("V", "face_found", T0 + 500, face=4), vision("V", "face_named", T0 + 900, face=4, name="me"))
        cls.store = Store(db)
        cls.fake = FakeChat()
        fusion.CHAT = cls.fake
        s = settings.load(None)
        runner.process_minute(Records(db), cls.store, T0, s, T0 + 10 * MIN)
        doc = cls.store.latest("live", T0)
        fusion.run_minute({"base_url": "x", "api_key": "k"}, cls.store, s, 0, T0, doc["package"], doc["strong"], {}, now_ms=T0 + 2 * MIN)
        cls.settings_store = SettingsStore(db, "/nonexistent.json")
        cls.recordings = __import__("tempfile").TemporaryDirectory()
        from test_mind_speak import FakeTts
        cls.tts = FakeTts(connected=False)
        from mind import speak
        speak.CHAT = cls.fake
        ctx = {"env": {"base_url": "x", "api_key": "k"}, "settings_store": cls.settings_store, "recordings_root": cls.recordings.name,
               "tts": cls.tts, "roles_dir": str(Path(__file__).resolve().parents[1] / "config" / "roles")}
        cls.server = api.make_server(cls.store, "k", "127.0.0.1", 0, ctx)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, path, body=None, ctype="application/json", method=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, method=method or ("POST" if body is not None else "GET"))
        req.add_header("Authorization", "Bearer k")
        if body is not None:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return Reply(r.status, r.read())
        except urllib.error.HTTPError as e:
            with e:
                return Reply(e.code, e.read())

    def post_json(self, path, obj):
        return self.call(path, json.dumps(obj).encode())

    def test_post_requires_json_and_size_limit(self):
        self.assertEqual(self.call("/mark", b"x", ctype="text/plain").status, 415)
        self.assertEqual(self.call("/mark", b"{" + b" " * 17000 + b"}").status, 413)
        self.assertEqual(self.call("/mark", b"[1]").status, 400)
        self.assertEqual(self.post_json("/nope", {}).status, 404)

    def test_invalid_settings_rejected_previous_stays(self):
        s = self.call("/settings").json()
        bad = copy.deepcopy(s["settings"]); bad["owner"]["voice_names"] = []
        r = self.post_json("/settings", {"settings": bad, "note": ""})
        self.assertEqual(r.status, 400)
        self.assertIn("voice_names", r.body.decode())
        self.assertEqual(self.call("/settings").json()["version"], s["version"])

    def test_settings_save(self):
        s = self.call("/settings").json()
        new = copy.deepcopy(s["settings"]); new["place"]["default"] = "家里"
        r = self.post_json("/settings", {"settings": new, "note": "试"}).json()
        self.assertEqual(r["version"], s["version"] + 1)
        self.assertEqual(self.call("/settings").json()["settings"]["place"]["default"], "家里")

    def test_mark_and_label(self):
        self.assertEqual(self.post_json("/mark", {"kind": "arrive"}).status, 200)
        self.assertEqual(self.post_json("/mark", {"kind": "dance"}).status, 400)
        self.assertEqual(self.post_json("/label", {"minute": T0 + 9 * MIN, "verdict": "wrong", "field": "owner", "correct": "absent"}).status, 404)
        self.assertEqual(self.post_json("/label", {"minute": T0, "verdict": "wrong", "field": "owner", "correct": "absent", "note": ""}).status, 200)
        now = int(__import__("time").time() * 1000)
        labels = self.call(f"/labels?from={now - 12 * 3600_000}&to={now + 3600_000}").json()
        self.assertTrue({"mark", "label"} <= {x["kind"] for x in labels})

    def test_rerun_never_touches_live(self):
        before = [d for d in copy.deepcopy(self.db["mind_scenes"].docs) if d["lane"] == "live"]
        r = self.post_json("/rerun", {"minute": T0, "prompt": "v3", "model": "gpt-6.1-terra"})
        self.assertEqual(r.status, 200)
        self.assertEqual((r.json()["lane"], r.json()["model"]), ("rerun", "gpt-6.1-terra"))
        self.assertEqual([d for d in self.db["mind_scenes"].docs if d["lane"] == "live"], before)
        self.assertEqual(self.post_json("/rerun", {"minute": T0, "prompt": "v9", "model": "gpt-6-luna"}).status, 400)
        self.assertEqual(self.call(f"/scene?t={T0}&lane=rerun").json()["versions"][0]["lane"], "rerun")

    def test_rerun_uses_the_live_scenes_settings_version(self):
        live = self.store.latest_scene("live", T0)
        cur = self.call("/settings").json()
        new = copy.deepcopy(cur["settings"]); new["place"]["default"] = "别处"
        self.post_json("/settings", {"settings": new, "note": ""})
        r = self.post_json("/rerun", {"minute": T0, "prompt": "v3", "model": "gpt-6-luna"}).json()
        self.assertEqual(r["settings_version"], live["settings_version"])
        self.assertNotIn("别处", self.fake.calls[-1]["messages"][1]["content"])

    def test_switch_turns_mind_off_and_rerun_is_refused(self):
        try:
            self.assertEqual(self.call("/state").json().get("enabled"), True)
            self.assertEqual(self.post_json("/switch", {"enabled": "no"}).status, 400)
            r = self.post_json("/switch", {"enabled": False})
            self.assertEqual((r.status, r.json()["enabled"]), (200, False))
            self.assertIs(self.call("/state").json()["enabled"], False)
            n = len(self.fake.calls)
            r = self.post_json("/rerun", {"minute": T0, "prompt": "v3", "model": "gpt-6-luna"})
            self.assertEqual(r.status, 409)
            self.assertEqual(len(self.fake.calls), n)
        finally:
            self.post_json("/switch", {"enabled": True})
        self.assertIs(self.call("/state").json()["enabled"], True)

    def test_only_v3_is_offered(self):
        self.assertEqual(self.call("/prompts").json()["versions"], ["v3"])
        for old_version in ("v1", "v2"):
            self.assertEqual(self.post_json("/rerun", {"minute": T0, "prompt": old_version, "model": "gpt-6-luna"}).status, 400)
            s = self.call("/settings").json()
            old = copy.deepcopy(s["settings"]); old["fusion"]["prompt"] = old_version
            self.assertEqual(self.post_json("/settings", {"settings": old, "note": ""}).status, 400)
        self.assertEqual(settings.load(None)["fusion"]["prompt"], "v3")

    def test_voices_and_voice_try(self):
        self.assertEqual([v["id"] for v in self.call("/voices").json()], ["tang", "xiaohei"])
        self.tts.connected = True
        try:
            self.post_json("/switches", {"name": "mouth", "mode": "clips"})
            self.assertEqual(self.post_json("/voice/try", {"voice": "xiaohei"}).status, 409)   # not in TTS mode
            self.post_json("/switches", {"name": "mouth", "mode": "tts"})
            self.assertEqual(self.post_json("/voice/try", {"voice": "nobody"}).status, 400)
            r = self.post_json("/voice/try", {"voice": "xiaohei"})
            self.assertEqual(r.status, 200)
            self.assertIn("小黑", r.json()["text"])
        finally:
            self.tts.connected = False
            self.post_json("/switches", {"name": "mouth", "mode": "clips"})

    def test_mouth_mode_switch(self):
        self.assertEqual(self.call("/switches").json()["mouth"]["mode"], "clips")        # no document: today's clips
        try:
            self.assertEqual(self.post_json("/switches", {"name": "mouth", "mode": "tts"}).json()["mouth"]["mode"], "tts")
            self.assertEqual(self.post_json("/switches", {"name": "mouth", "mode": "shout"}).status, 400)
            self.assertEqual(self.post_json("/switches", {"name": "mouth", "on": True}).status, 400)
            stopped = len(self.tts.stops)
            self.post_json("/switches", {"name": "mouth", "mode": "clips"})
            self.assertEqual(len(self.tts.stops), stopped + 1)                   # leaving TTS stops what is playing
        finally:
            self.post_json("/switches", {"name": "mouth", "mode": "clips"})

    def test_notes_switch_and_list_api(self):
        try:
            self.assertIs(self.post_json("/switches", {"name": "notes", "on": True}).json()["notes"]["on"], True)
        finally:
            self.post_json("/switches", {"name": "notes", "on": False})
        coll = self.db["conversations"]
        try:
            coll.insert_one({"cid": "c1", "start_ms": T0, "last_ms": T0 + 1000, "end_ms": None, "title": "地线", "notes": "n", "version": 1})
            coll.insert_one({"cid": "c0", "start_ms": T0 - 3600_000, "last_ms": T0 - 3500_000, "end_ms": T0 - 3500_000, "title": "早", "notes": "n", "version": 1})
            rows = self.call(f"/notes?from={T0 - 60_000}&to={T0 + 60_000}").json()
            self.assertEqual([r["cid"] for r in rows], ["c1"])
            self.assertNotIn("_id", rows[0])
        finally:
            coll.docs[:] = []

    def test_roles_say_and_utterances(self):
        roles = self.call("/roles").json()
        self.assertEqual(sorted(r["id"] for r in roles), ["comfort", "deadpan", "roast", "sad", "worst"])
        self.assertEqual(self.post_json("/say", {"role": "roast"}).status, 409)      # mouth not connected
        self.assertEqual(self.post_json("/say", {"role": "nope"}).status, 400)
        self.tts.connected = True
        self.post_json("/switches", {"name": "mouth", "mode": "clips"})
        r = self.post_json("/say", {"role": "roast"})
        self.assertEqual(r.status, 409)                                          # TTS mode is not on
        self.assertIn("TTS", r.json()["error"])
        self.post_json("/switches", {"name": "mouth", "mode": "tts"})
        try:
            self.fake.reply_next(json.dumps({"text": "这板子焊得，比我的发型还有个性。"}, ensure_ascii=False))
            r = self.post_json("/say", {"role": "roast"})
            self.assertEqual((r.status, r.json()["text"]), (200, "这板子焊得，比我的发型还有个性。"))
        finally:
            self.tts.connected = False
            self.post_json("/switches", {"name": "mouth", "mode": "clips"})
        now = int(__import__("time").time() * 1000)
        rows = self.call(f"/utterances?from={now - 60_000}&to={now + 60_000}").json()
        self.assertEqual(rows[-1]["role"], "roast")
        self.assertEqual(self.post_json("/say/stop", {}).status, 200)

    def test_place_report(self):
        self.assertEqual(self.post_json("/place", {"label": "办公室"}).status, 200)
        self.assertEqual(self.post_json("/place", {"label": ""}).status, 400)
        self.assertEqual(self.post_json("/place", {"label": "x" * 41}).status, 400)
        self.assertEqual(self.store.place_at(int(__import__("time").time() * 1000) + 1000)["label"], "办公室")

    def test_now_and_names(self):
        now = self.call("/now").json()
        self.assertEqual(now["scene"]["source"], "model")
        self.assertIn("state", now)
        self.assertEqual(now["strong"]["owner"]["state"], "seen")
        self.assertEqual(self.call("/names").json(), {"voice": ["me"], "face": ["me"]})
        self.assertEqual(self.call("/prompts").json()["models"], ["gpt-6-luna", "gpt-6.1-terra"])

    def test_scene_includes_its_run(self):
        r = self.call(f"/scene?t={T0}").json()
        self.assertEqual(r["versions"][0]["source"], "model")
        self.assertEqual(r["runs"][0]["step"], "fusion")
        self.assertIn("messages", r["runs"][0]["request"])

    def test_segments_and_stats(self):
        self.assertIsInstance(self.call(f"/segments?from={T0 - 3600_000}&to={T0 + 5 * MIN}").json(), list)
        st = self.call("/stats?days=3650").json()
        self.assertIn("days", st)
        row = next(r for r in st["rows"] if r["step"] == "fusion")
        self.assertGreaterEqual(row["calls"], 1)
        self.assertIn("cost_usd", row)


if __name__ == "__main__":
    unittest.main()
