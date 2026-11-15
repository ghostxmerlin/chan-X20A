"""视觉爆发 on the page (2026-11-15): mind GET /looks and /looks/frame read the ear's hello.vision_looks rows and the
photo files under recordings_root."""
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB  # noqa: E402
from mind import api  # noqa: E402
from mind.store import Store  # noqa: E402


class LooksApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "frames" / "s").mkdir(parents=True)
        (root / "frames" / "s" / "1.jpg").write_bytes(b"\xff\xd8jpeg")
        (Path(self.tmp.name).parent / "outside.jpg").write_bytes(b"secret")
        self.addCleanup(lambda: (Path(self.tmp.name).parent / "outside.jpg").unlink(missing_ok=True))
        self.server = api.make_server(Store(self.db), "k", port=0, ctx={"recordings_root": self.tmp.name})
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}" + path,
                                     headers={"Authorization": "Bearer k"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def look(self, n, at, path="frames/s/1.jpg", burst="b1"):
        self.db["vision_looks"].insert_one({"kind": "look", "lid": f"{burst}-{n}", "burst_id": burst, "at": at,
                                            "text": f"第{n}张", "path": path, "latency_ms": 1100, "usage": {"x": 1}})

    def test_running_then_finished(self):
        now = int(time.time() * 1000)
        self.look(1, now - 4000)
        self.look(2, now - 2000)
        st, d = self.call("/looks?limit=10")
        self.assertEqual(st, 200)
        self.assertEqual([l["lid"] for l in d["looks"]], ["b1-2", "b1-1"])            # newest first
        self.assertEqual((d["running"]["burst_id"], d["running"]["looks"]), ("b1", 2))
        self.assertNotIn("usage", d["looks"][0])
        self.assertNotIn("path", d["looks"][0])
        self.assertTrue(d["looks"][0]["has_frame"])
        self.db["vision_looks"].insert_one({"kind": "burst", "lid": "b1-end", "burst_id": "b1", "at": now - 4000,
                                            "looks": 2, "reason": "heard_stop"})
        st, d = self.call("/looks")
        self.assertIsNone(d["running"])
        self.assertEqual(d["bursts"][0]["reason"], "heard_stop")

    def test_old_looks_are_not_running(self):
        self.look(1, int(time.time() * 1000) - 60_000)
        self.assertIsNone(self.call("/looks")[1]["running"])

    def test_frame_is_read_only_under_the_recordings_root(self):
        now = int(time.time() * 1000)
        self.look(1, now)
        self.look(2, now, path="../outside.jpg")
        st, f = self.call("/looks/frame?lid=b1-1")
        self.assertEqual((st, base64.b64decode(f["jpeg_b64"]), f["missing"]), (200, b"\xff\xd8jpeg", False))
        st, f = self.call("/looks/frame?lid=b1-2")
        self.assertEqual((st, f["jpeg_b64"], f["missing"]), (200, None, True))
        self.assertEqual(self.call("/looks/frame?lid=nope")[0], 404)
        self.assertEqual(self.call("/looks/frame")[0], 400)


if __name__ == "__main__":
    unittest.main()
