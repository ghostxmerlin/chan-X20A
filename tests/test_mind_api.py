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
        ctx = {"env": {"base_url": "x", "api_key": "k"}, "settings_store": cls.settings_store, "recordings_root": cls.recordings.name}
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
        r = self.post_json("/rerun", {"minute": T0, "prompt": "v2", "model": "gpt-6.1-terra"})
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
        r = self.post_json("/rerun", {"minute": T0, "prompt": "v2", "model": "gpt-6-luna"}).json()
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
            r = self.post_json("/rerun", {"minute": T0, "prompt": "v2", "model": "gpt-6-luna"})
            self.assertEqual(r.status, 409)
            self.assertEqual(len(self.fake.calls), n)
        finally:
            self.post_json("/switch", {"enabled": True})
        self.assertIs(self.call("/state").json()["enabled"], True)

    def test_v1_is_retired_but_old_records_stay_readable(self):
        self.assertEqual(self.call("/prompts").json()["versions"], ["v2"])
        self.assertEqual(self.post_json("/rerun", {"minute": T0, "prompt": "v1", "model": "gpt-6-luna"}).status, 400)
        s = self.call("/settings").json()
        old = copy.deepcopy(s["settings"]); old["fusion"]["prompt"] = "v1"
        self.assertEqual(self.post_json("/settings", {"settings": old, "note": ""}).status, 400)
        self.assertEqual(settings.load(None)["fusion"]["prompt"], "v2")

    def test_switches_default_off_and_stale(self):
        r = self.call("/switches").json()
        self.assertEqual(r["tingwu"]["on"], False)
        self.assertIs(r["tingwu_status"]["stale"], True)

    def test_post_switch_tingwu(self):
        try:
            r = self.post_json("/switches", {"name": "tingwu", "on": True})
            self.assertEqual((r.status, r.json()["tingwu"]["on"]), (200, True))
            self.assertIs(self.call("/switches").json()["tingwu"]["on"], True)
            now = int(__import__("time").time() * 1000)
            self.db["switches"].update_one({"_id": "tingwu_status"}, {"$set": {"running": ["s1"], "available": True, "at": now}}, upsert=True)
            st = self.call("/switches").json()["tingwu_status"]
            self.assertEqual((st["running"], st["available"], st["stale"]), (["s1"], True, False))
            self.db["switches"].update_one({"_id": "tingwu_status"}, {"$set": {"at": now - 91_000}})
            self.assertIs(self.call("/switches").json()["tingwu_status"]["stale"], True)
        finally:
            self.post_json("/switches", {"name": "tingwu", "on": False})

    def test_switch_rejects_unknown_name_and_non_bool(self):
        self.assertEqual(self.post_json("/switches", {"name": "mouth", "on": True}).status, 400)
        self.assertEqual(self.post_json("/switches", {"name": "tingwu", "on": "yes"}).status, 400)

    def put_frame(self, at, seq, data=b"\xff\xd8x\xff\xd9", path=None):
        import os
        rel = path or f"frames/20261111/x-v1/{at}-{seq}.jpg"
        full = os.path.join(self.recordings.name, rel)
        if path is None:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
        self.db["frames"].insert_one({"session": "20261111/x-v1", "seq": seq, "t_us": seq, "at": at, "received": at,
                                      "path": rel, "bytes": len(data), "w": 320, "h": 240, "diff": 9})

    def test_latest_frame_returns_the_newest_image(self):
        import base64
        now = int(__import__("time").time() * 1000)
        self.put_frame(now - 20_000, 101, b"\xff\xd8old\xff\xd9")
        self.put_frame(now - 5_000, 102, b"\xff\xd8new\xff\xd9")
        r = self.call("/frames/latest").json()
        self.assertEqual((r["seq"], r["diff"], r["w"], r["h"]), (102, 9, 320, 240))
        self.assertEqual(base64.b64decode(r["jpeg_b64"]), b"\xff\xd8new\xff\xd9")
        self.assertGreaterEqual(r["today"], 2)

    def test_frame_path_outside_root_is_refused(self):
        now = int(__import__("time").time() * 1000)
        self.put_frame(now + 60_000, 103, path="../outside.jpg")
        try:
            r = self.call("/frames/latest").json()
            self.assertEqual((r["seq"], r["missing"], r["jpeg_b64"]), (103, True, None))
        finally:
            coll = self.db["frames"]
            coll.docs[:] = [d for d in coll.docs if d.get("seq") != 103]

    def test_eye_switch_roundtrip(self):
        try:
            r = self.post_json("/switches", {"name": "eye", "on": True}).json()
            self.assertIs(r["eye"]["on"], True)
            self.assertIs(r["eye_status"]["stale"], True)
        finally:
            self.post_json("/switches", {"name": "eye", "on": False})
        self.assertIs(self.call("/switches").json()["eye"]["on"], False)

    def test_eye_latest_and_range(self):
        coll = self.db["eye_minutes"]
        try:
            coll.insert_one({"minute": T0, "version": 1, "source": "model", "result": {"activity": "a"}})
            coll.insert_one({"minute": T0 + 60_000, "version": 1, "source": "model", "result": {"activity": "b"}})
            self.assertEqual(self.call("/eye/latest").json()["result"]["activity"], "b")
            rows = self.call(f"/eye?from={T0}&to={T0 + 60_000}").json()
            self.assertEqual([r["minute"] for r in rows], [T0])
            self.assertNotIn("_id", rows[0])
        finally:
            coll.docs[:] = []

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
