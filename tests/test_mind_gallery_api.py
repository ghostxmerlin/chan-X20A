"""图库 routes (2026-11-20): mind /gallery/* over FakeDB, a temp recordings root and a pause Controller."""
import base64
import datetime
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB  # noqa: E402
from mind import api, pause  # noqa: E402
from mind.store import Store  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def ms(y, mo, d, h, mi, s=0):
    return int(datetime.datetime(y, mo, d, h, mi, s, tzinfo=TZ).timestamp() * 1000)


class GalleryApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "frames" / "s").mkdir(parents=True)
        (root / "frames" / "s" / "1.jpg").write_bytes(b"\xff\xd8one")
        (root / "spool").mkdir()
        self.at = ms(2026, 11, 20, 10, 5, 30)
        self.db["frames"].insert_one({"_id": "f1", "session": "s", "seq": 1, "t_us": 1, "at": self.at,
                                      "path": "frames/s/1.jpg"})
        self.events = []
        self.pc = pause.Controller(self.db, settings_fn=lambda: {"pause": {}}, ledger_fn=lambda d: {"total": {}},
                                   emit=lambda kind, **f: self.events.append(kind))
        ctx = {"recordings_root": self.tmp.name, "pause": self.pc, "spool_dir": str(root / "spool")}
        self.server = api.make_server(Store(self.db), "k", port=0, ctx=ctx)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": "Bearer k"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}" + path, data=data,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def robot(self, online):
        self.db["bodies"].insert_one({"_id": "stackchan-x", "kind": "stackchan", "last_seen_ms": 1,
                                      "links": {"hearing": 1 if online else 0, "vision": 0}})

    def test_reads(self):
        st, d = self.call("/gallery/days")
        self.assertEqual((st, d["days"]), (200, [{"day": "2026-11-20", "n": 1}]))
        st, d = self.call("/gallery/hours?day=2026-11-20")
        self.assertEqual((st, d["hours"]), (200, [{"hour": 10, "n": 1}]))
        lo = ms(2026, 11, 20, 10, 0)
        st, d = self.call(f"/gallery/frames?from={lo}&to={lo + 3600_000}")
        self.assertEqual((st, [f["id"] for f in d["frames"]]), (200, ["f1"]))
        st, d = self.call("/gallery/images?ids=f1,zz")
        self.assertEqual(st, 200)
        self.assertEqual(base64.b64decode(d["images"]["f1"]), b"\xff\xd8one")
        self.assertIsNone(d["images"]["zz"])
        st, d = self.call("/gallery/frame?id=f1")
        self.assertEqual((st, d["id"]), (200, "f1"))
        self.assertEqual(base64.b64decode(d["jpeg_b64"]), b"\xff\xd8one")

    def test_bad_params_and_unknown(self):
        self.assertEqual(self.call("/gallery/hours?day=nope")[0], 400)
        self.assertEqual(self.call("/gallery/hours")[0], 400)
        self.assertEqual(self.call("/gallery/frames?from=1&to=x")[0], 400)
        self.assertEqual(self.call("/gallery/images?ids=" + ",".join(str(i) for i in range(61)))[0], 400)
        self.assertEqual(self.call("/gallery/images")[0], 400)
        self.assertEqual(self.call("/gallery/frame")[0], 400)
        self.assertEqual(self.call("/gallery/frame?id=nope")[0], 404)
        self.assertEqual(self.call("/gallery/nope")[0], 404)

    def test_maintenance_state(self):
        st, d = self.call("/gallery/maintenance")
        self.assertEqual((st, d["on"], d["since"], d["can_delete"], d["why_not"]),
                         (200, False, None, False, "还没开始维护"))
        self.robot(True)
        st, d = self.call("/gallery/maintenance", {"on": True})
        self.assertEqual((st, d["on"], d["robot_connected"], d["can_delete"], d["why_not"]),
                         (200, True, True, False, "机器人还连着，请先关机"))
        self.assertIsInstance(d["since"], int)
        self.assertEqual(self.call("/gallery/maintenance", {"on": "x"})[0], 400)
        self.db["bodies"].docs[0]["links"] = {"hearing": 0, "vision": 0}
        st, d = self.call("/gallery/maintenance")
        self.assertEqual((d["robot_connected"], d["can_delete"], d["why_not"]), (False, True, None))
        st, d = self.call("/gallery/maintenance", {"on": False})
        self.assertEqual((d["on"], d["since"], d["why_not"]), (False, None, "还没开始维护"))

    def test_delete_needs_maintenance(self):
        st, p = self.call("/gallery/plan", {"ids": ["f1"]})
        self.assertEqual((st, p["frames"]), (200, 1))
        st, d = self.call("/gallery/delete", {"ids": ["f1"], "fp": p["fp"]})
        self.assertEqual((st, d["code"]), (409, "not_maintenance"))
        self.robot(True)
        self.call("/gallery/maintenance", {"on": True})
        st, d = self.call("/gallery/delete", {"ids": ["f1"], "fp": p["fp"]})
        self.assertEqual((st, d["code"]), (409, "not_maintenance"))
        self.db["bodies"].docs[0]["links"] = {"hearing": 0, "vision": 0}
        st, d = self.call("/gallery/delete", {"ids": ["f1"], "fp": p["fp"]})
        self.assertEqual(st, 200, d)
        self.assertFalse((Path(self.tmp.name) / "frames" / "s" / "1.jpg").exists())
        self.assertEqual(self.db["frames"].count_documents({}), 0)

    def test_roster_unreadable_counts_as_connected(self):
        self.call("/gallery/maintenance", {"on": True})
        orig = self.db.__class__.__getitem__
        self.addCleanup(setattr, self.db.__class__, "__getitem__", orig)

        def boom(s, name):
            if name == "bodies":
                raise RuntimeError("down")
            return orig(s, name)
        self.db.__class__.__getitem__ = boom
        st, d = self.call("/gallery/maintenance")
        self.assertEqual((d["robot_connected"], d["can_delete"]), (True, False))

    def test_bad_delete_bodies(self):
        self.call("/gallery/maintenance", {"on": True})
        self.assertEqual(self.call("/gallery/plan", {"ids": "f1"})[0], 400)
        self.assertEqual(self.call("/gallery/delete", {"ids": ["f1"]})[0], 400)

    def test_stale_is_409(self):
        self.call("/gallery/maintenance", {"on": True})
        st, d = self.call("/gallery/delete", {"ids": ["f1"], "fp": "old"})
        self.assertEqual((st, d["code"]), (409, "stale"))
        self.assertEqual(self.db["frames"].count_documents({}), 1)

    def test_gallery_posts_do_not_count_as_owner_input(self):
        self.assertFalse([p for p in api.OWNER_POSTS if p.startswith("/gallery")])
        calls = []
        self.pc.note_owner = lambda *a, **k: calls.append(a)
        self.call("/gallery/plan", {"ids": ["f1"]})
        self.call("/gallery/maintenance", {"on": True})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
