"""图库 reads (2026-11-20): server/mind/gallery.py over FakeDB and a temp recordings root."""
import base64
import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB  # noqa: E402
from mind import gallery  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def ms(y, mo, d, h, mi, s=0):
    return int(datetime.datetime(y, mo, d, h, mi, s, tzinfo=TZ).timestamp() * 1000)


class GalleryTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        (Path(self.root) / "frames" / "s").mkdir(parents=True)
        (Path(self.root) / "frames" / "s" / "1.jpg").write_bytes(b"\xff\xd8one")
        (Path(self.root) / "frames" / "s" / "2.jpg").write_bytes(b"\xff\xd8two")
        out = Path(self.root).parent / "outside.jpg"
        out.write_bytes(b"secret")
        self.addCleanup(lambda: out.unlink(missing_ok=True))

    def frame(self, id, at, n=1, **extra):
        self.db["frames"].insert_one({"_id": id, "session": "s", "seq": n, "t_us": n, "at": at,
                                      "path": f"frames/s/{n}.jpg", **extra})

    def test_days_and_hours_in_beijing_time(self):
        self.frame("a", ms(2026, 11, 19, 23, 59))
        self.frame("b", ms(2026, 11, 20, 0, 1))
        self.assertEqual(gallery.days(self.db, TZ), [{"day": "2026-11-20", "n": 1}, {"day": "2026-11-19", "n": 1}])
        self.assertEqual(gallery.hours(self.db, "2026-11-20", TZ), [{"hour": 0, "n": 1}])
        self.assertEqual(gallery.hours(self.db, "2026-11-19", TZ), [{"hour": 23, "n": 1}])
        with self.assertRaises(ValueError):
            gallery.hours(self.db, "nope", TZ)

    def test_frames_flags(self):
        at = ms(2026, 11, 20, 10, 5, 30)
        self.frame("a", at, 1, snap=3)
        self.frame("b", at + 90_000, 2)
        self.db["vision_looks"].insert_one({"kind": "look", "at": at, "path": "frames/s/1.jpg", "text": "x"})
        self.db["vision_looks"].insert_one({"kind": "burst", "at": at})
        minute = at // 60000 * 60000
        self.db["eye_minutes"].insert_one({"minute": minute, "result": {"activity": "写字"}})
        self.db["mind_scenes"].insert_one({"minute": minute, "lane": "live", "version": 1, "scene": {"text": "桌前"}})
        out = gallery.frames(self.db, minute, minute + 3600_000)
        self.assertEqual([f["id"] for f in out], ["a", "b"])
        self.assertEqual({k: out[0][k] for k in ("snap", "look", "eye", "scene")},
                         {"snap": True, "look": True, "eye": True, "scene": True})
        self.assertEqual({k: out[1][k] for k in ("snap", "look", "eye", "scene")},
                         {"snap": False, "look": False, "eye": False, "scene": False})
        with self.assertRaises(ValueError):
            gallery.frames(self.db, minute, minute + 3600_001)

    def test_images_reads_only_under_root(self):
        self.frame("a", 1000, 1)
        self.db["frames"].insert_one({"_id": "e", "at": 2000, "path": "../outside.jpg"})
        out = gallery.images(self.db, self.root, ["a", "e", "missing"])
        self.assertEqual(base64.b64decode(out["a"]), b"\xff\xd8one")
        self.assertIsNone(out["e"])
        self.assertIsNone(out["missing"])
        with self.assertRaises(ValueError):
            gallery.images(self.db, self.root, [str(i) for i in range(61)])

    def test_read_file(self):
        self.assertEqual(gallery.read_file(self.root, "frames/s/2.jpg"), b"\xff\xd8two")
        self.assertIsNone(gallery.read_file(self.root, "../outside.jpg"))
        self.assertIsNone(gallery.read_file(self.root, ""))
        self.assertIsNone(gallery.read_file(None, "frames/s/1.jpg"))

    def test_detail_has_look_text_eye_and_scene(self):
        at = ms(2026, 11, 20, 10, 5, 30)
        self.frame("a", at, 1)
        minute = at // 60000 * 60000
        self.db["vision_looks"].insert_one({"kind": "look", "at": at, "path": "frames/s/1.jpg", "text": "一杯茶"})
        self.db["eye_minutes"].insert_one({"minute": minute, "version": 1, "result": {"activity": "旧"}})
        self.db["eye_minutes"].insert_one({"minute": minute, "version": 2, "result": {
            "people_count": 1, "people": [{"who": "me", "doing": "写字"}], "scene": "书房"}})
        self.db["mind_scenes"].insert_one({"minute": minute, "lane": "live", "version": 1, "scene": {"text": "旧情景"}})
        self.db["mind_scenes"].insert_one({"minute": minute, "lane": "live", "version": 2,
                                          "scene": {"key": "k1", "text": "新情景"}})
        self.db["mind_scenes"].insert_one({"minute": minute, "lane": "rerun", "version": 9, "scene": {"text": "重跑"}})
        d = gallery.detail(self.db, self.root, "a")
        self.assertEqual((d["id"], d["at"], d["session"], d["snap"]), ("a", at, "s", False))
        self.assertEqual(base64.b64decode(d["jpeg_b64"]), b"\xff\xd8one")
        self.assertEqual(d["looks"], [{"at": at, "text": "一杯茶"}])
        self.assertEqual(d["eye"]["minute"], minute)
        self.assertEqual(d["eye"]["text"], "me\n写字\n书房")
        self.assertEqual(d["scene"], {"minute": minute, "text": "新情景"})
        self.assertIsNone(gallery.detail(self.db, self.root, "nope"))

    def test_texts(self):
        obj = {"key": "abc", "text": "你好", "a": ["x", "吃饭", {"op": "add", "doing": "写字", "confidence": "high"}],
               "n": 3}
        self.assertEqual(gallery.texts(obj), ["你好", "吃饭", "写字"])
        self.assertEqual(gallery.texts(None), [])

    def test_oid(self):
        from bson import ObjectId
        h = "0123456789abcdef01234567"
        self.assertEqual(gallery.oid(h), ObjectId(h))
        self.assertEqual(gallery.oid("a"), "a")


if __name__ == "__main__":
    unittest.main()
