"""图库 reads (2026-11-20): server/mind/gallery.py over FakeDB and a temp recordings root."""
import base64
import datetime
import json
import os
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


M1 = ms(2026, 11, 20, 15, 30)                 # the minute of the two photos to delete (and one kept)
M2 = M1 + 10 * 60_000                         # another minute: its photo, scene and segment stay
LOOK = "桌上放着一杯冒着热气的绿茶。旁边有一本打开的书。好的"
LOOK_FRAGS = ["桌上放着一杯冒着热气的绿茶", "旁边有一本打开的书"]
MARK = "［图库已删除］"


def sha(c):
    return c * 64


def jline(flow, bodies):
    return json.dumps({"flow": flow, "bodies": bodies}, ensure_ascii=False, separators=(",", ":")) + "\n"


class DeleteTest(unittest.TestCase):
    """2 photos to delete (a, b in M1), 1 kept in the same minute (c), 1 kept in another minute (d)."""

    def setUp(self):
        self.db = FakeDB()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = str(Path(self.tmp.name) / "rec")
        self.spool = str(Path(self.tmp.name) / "spool")
        os.makedirs(self.spool)
        self.paths = {}
        for i, (id, at, day_dir) in enumerate([("a", M1 + 5_000, "x"), ("b", M1 + 20_000, "x"),
                                               ("c", M1 + 40_000, "y"), ("d", M2 + 5_000, "y")]):
            rel = f"frames/2026-11-20/{day_dir}/{at * 1000}-{i}.jpg"
            (Path(self.root) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(self.root) / rel).write_bytes(b"\xff\xd8" + id.encode())
            self.paths[id] = rel
            self.db["frames"].insert_one({"_id": id, "session": day_dir, "seq": i, "t_us": at * 1000, "at": at,
                                          "path": rel})
        p = self.paths
        self.db.add("vision_looks",
                    {"_id": "la", "kind": "look", "lid": 1, "at": M1 + 6_000, "path": p["a"], "text": LOOK},
                    {"_id": "ld", "kind": "look", "lid": 2, "at": M2 + 6_000, "path": p["d"], "text": "客厅里亮着一盏落地灯"})
        self.db.add("eye_minutes",
                    {"_id": "e1", "minute": M1, "version": 1, "source": "s",
                     "frames": [{"seq": 0, "path": p["a"]}, {"seq": 2, "path": p["c"]}],
                     "result": {"scene": "书房里的书桌前面坐着一个人", "people": [{"doing": "低头在笔记本上写字很认真"}]}},
                    {"_id": "e0", "minute": M1 - 60_000, "version": 1, "frames": [{"seq": 1, "path": p["b"]}],
                     "result": None},
                    {"_id": "e2", "minute": M2, "version": 1, "frames": [{"seq": 3, "path": p["d"]}],
                     "result": {"scene": "客厅沙发上没有人在"}})
        self.db.add("mind_packages",
                    {"_id": "p1", "minute": M1, "lane": "live", "version": 1, "package": {"eye": "x"}},
                    {"_id": "p1r", "minute": M1, "lane": "rerun", "version": 1, "package": {"eye": "x"}},
                    {"_id": "p2", "minute": M2, "lane": "live", "version": 1, "package": {"eye": "y"}})
        self.db.add("mind_scenes",
                    {"_id": "s1", "minute": M1, "lane": "live", "version": 1, "scene": {"text": "主人正在书房里安静地写东西"}},
                    {"_id": "s1r", "minute": M1, "lane": "rerun", "version": 1, "scene": {"text": "重跑"}},
                    {"_id": "s2", "minute": M2, "lane": "live", "version": 1, "scene": {"text": "主人离开了书桌去了客厅那边"}})
        self.db.add("mind_runs",
                    {"_id": "r1", "minute": M1, "lane": "live", "step": "fusion"},
                    {"_id": "r1b", "minute": M1, "lane": "rerun", "step": "fusion"},
                    {"_id": "r2", "minute": M2, "lane": "live", "step": "fusion"},
                    {"_id": "rs1", "minute": M1 - 600_000, "lane": "live", "step": "segment", "at": M1 + 130_000,
                     "request": {"m": "主人一直在书桌前写字"}, "parsed": {"title": "下午在书桌前写作业的时间"}},
                    {"_id": "rs1r", "minute": M1 - 600_000, "lane": "rerun", "step": "segment"},
                    {"_id": "rs2", "minute": M1 + 300_000, "lane": "live", "step": "segment"},
                    {"_id": "rn", "minute": M1 + 120_000, "lane": "live", "step": "eye", "at": M1 + 125_000,
                     "request": {"messages": [{"role": "user", "content": "最近看到：" + LOOK}], "n": 3},
                     "response": {"x": "无关的回答内容"}},
                    {"_id": "rf", "minute": M1 + 5 * 3600_000, "lane": "live", "step": "eye",
                     "at": M1 + 5 * 3600_000, "request": {"c": LOOK}})
        self.db.add("mind_segments",
                    {"_id": "g1", "start": M1 - 600_000, "end": M1 + 120_000, "lane": "live", "version": 1,
                     "summary": {"title": "下午在书桌前写作业的时间", "text": "主人一直在书桌前写字。期间喝了几口茶水休息", "confidence": 0.8}},
                    {"_id": "g3", "start": M1 - 3600_000, "end": M1 + 30_000, "lane": "live", "version": 1,
                     "summary": {"title": "还没结束的一个长段落", "text": "短", "confidence": 0.5}},
                    {"_id": "g2", "start": M1 + 300_000, "end": M1 + 1200_000, "lane": "live", "version": 1,
                     "summary": {"title": "后来去客厅休息的时间段", "text": "客厅里放着音乐很安静", "confidence": 0.7}})
        self.log, self.body = "log_202611", "logbody_202611"
        self.db.add(self.log,
                    {"_id": "eye:1:1", "kind": "call.look", "at": M1 + 7_000, "svc": "ear", "sum": LOOK,
                     "ref": {"path": p["a"], "lid": 1}, "body": {"system": sha("a"), "output": sha("b")}},
                    {"_id": "eye:1:2", "kind": "call.eye", "at": M1 + 61_000, "svc": "eye", "sum": "ok",
                     "ref": {"minute": M1}, "body": {"input": sha("c"), "output": sha("d")}},
                    {"_id": "mind:1:1", "kind": "call.fusion", "at": M1 + 62_000, "svc": "mind",
                     "ref": {"minute": M1, "lane": "live", "prompt": "v2"}, "body": {"params": sha("e")}},
                    {"_id": "mind:1:2", "kind": "call.segment", "at": M1 + 130_000, "svc": "mind",
                     "ref": {"minute": M1 - 600_000, "lane": "live"}},
                    {"_id": "mind:1:3", "kind": "call.segment", "at": M1 + 1300_000, "svc": "mind",
                     "ref": {"minute": M1 + 300_000, "lane": "live"}},
                    {"_id": "mind:1:4", "kind": "call.segment", "at": M1 - 200_000, "svc": "mind",
                     "ref": {"minute": M1 - 300_000, "lane": "live"}},
                    {"_id": "mind:1:5", "kind": "call.segment", "at": M1 + 140_000, "svc": "mind",
                     "ref": {"minute": M1 - 600_000, "lane": "rerun"}},
                    {"_id": "ear:1:11", "kind": "call.realtime", "at": M1 + 95_000, "svc": "ear",
                     "sum": "我看到桌上放着一杯冒着热气的绿茶"},
                    {"_id": "eye:1:3", "kind": "call.eye", "at": M2 + 61_000, "svc": "eye", "sum": "ok",
                     "ref": {"minute": M2}, "body": {"system": sha("a"), "output": sha("f")}},
                    {"_id": "ear:1:9", "kind": "call.realtime", "at": M1 + 90_000, "svc": "ear", "sum": "回答",
                     "body": {"input": sha("1")}})
        self.context = "你看到的：桌上放着一杯冒着热气的绿茶。主人说：好"
        self.db.add(self.body, {"_id": sha("a"), "text": "系统提示"}, {"_id": sha("b"), "text": LOOK},
                    {"_id": sha("c"), "text": "看图"}, {"_id": sha("d"), "text": "{}"}, {"_id": sha("e"), "text": "p"},
                    {"_id": sha("f"), "text": "{}"}, {"_id": sha("1"), "text": self.context})
        self.today = Path(self.spool) / "ear-20261120.jsonl"
        self.today.write_text(jline({"_id": "ear:1:9", "kind": "call.realtime"}, {sha("1"): self.context})
                              + jline({"_id": "ear:1:10", "kind": "svc.beat"}, {}), encoding="utf-8")
        (Path(self.spool) / "ear-20261120.offset").write_text("12")
        self.old = Path(self.spool) / "ear-20261101.jsonl"
        self.old.write_text(jline({"_id": "x", "kind": "call.realtime"}, {sha("2"): self.context}), encoding="utf-8")
        self.events = []

    def emit(self, kind, **fields):
        self.events.append((kind, fields))

    def counts(self):
        return {k: len(v.docs) for k, v in self.db.collections.items()}

    def run_delete(self, ids=("a", "b")):
        fp = gallery.plan(self.db, list(ids), spool_dir=self.spool)["fp"]
        return gallery.delete(self.db, self.root, list(ids), fp, spool_dir=self.spool, emit=self.emit)

    def ids(self, coll):
        return sorted(d["_id"] for d in self.db[coll].docs)

    def test_plan_counts_everything_and_is_read_only(self):
        before = self.counts()
        spool_before = (self.today.read_bytes(), self.old.read_bytes())
        out = gallery.plan(self.db, ["a", "b"], spool_dir=self.spool)
        self.assertEqual(len(out["fp"]), 16)
        self.assertEqual({k: out[k] for k in ("frames", "files", "looks", "eye_minutes", "packages", "scenes",
                                               "runs", "log_rows", "from", "to")},
                         {"frames": 2, "files": 2, "looks": 1, "eye_minutes": 2, "packages": 2, "scenes": 2,
                          "runs": 3, "log_rows": 4, "from": M1 + 5_000, "to": M1 + 20_000})
        self.assertEqual(sorted(out["segments"], key=lambda s: s["start"]),
                         [{"start": M1 - 3600_000, "end": M1 + 30_000, "title": "还没结束的一个长段落"},
                          {"start": M1 - 600_000, "end": M1 + 120_000, "title": "下午在书桌前写作业的时间"}])
        after = self.counts()
        self.assertEqual({k: after[k] for k in before}, before)
        self.assertTrue(all(n == 0 for k, n in after.items() if k not in before))
        self.assertEqual((self.today.read_bytes(), self.old.read_bytes()), spool_before)
        self.assertEqual(gallery.plan(self.db, ["a", "b"], spool_dir=self.spool)["fp"], out["fp"])
        self.assertNotEqual(gallery.plan(self.db, ["a"], spool_dir=self.spool)["fp"], out["fp"])

    def test_open_segment_overlaps_and_owns_later_segment_calls(self):
        self.db["mind_segments"].insert_one({"_id": "g4", "start": M2 - 60_000, "end": None, "lane": "live",
                                             "summary": {"title": "现在还在进行的段落", "text": "t"}})
        out = gallery.plan(self.db, ["d"], spool_dir=self.spool)
        self.assertEqual(sorted(s["title"] for s in out["segments"]), ["后来去客厅休息的时间段", "现在还在进行的段落"])
        self.db[self.log].insert_one({"_id": "mind:1:6", "kind": "call.segment", "at": M2 + 70_000,
                                      "ref": {"minute": M2 - 60_000, "lane": "live"}})
        out = gallery.plan(self.db, ["d"], spool_dir=self.spool)
        self.assertEqual(out["log_rows"], 3)                          # eye:1:3 + segment calls of g2 and g4

    def test_delete_removes_every_copy(self):
        out = self.run_delete()
        self.assertEqual(self.ids("frames"), ["c", "d"])
        self.assertEqual(self.ids("vision_looks"), ["ld"])
        self.assertEqual(self.ids("eye_minutes"), ["e2"])
        self.assertEqual(self.ids("mind_packages"), ["p2"])
        self.assertEqual(self.ids("mind_scenes"), ["s2"])
        self.assertEqual(self.ids("mind_runs"), ["r2", "rf", "rn", "rs1r", "rs2"])
        self.assertEqual(self.ids("mind_segments"), ["g2"])
        self.assertEqual(self.ids(self.log), ["ear:1:11", "ear:1:9", "eye:1:3", "mind:1:3", "mind:1:4", "mind:1:5"])
        p = Path(self.root)
        self.assertFalse((p / self.paths["a"]).exists())
        self.assertFalse((p / self.paths["b"]).exists())
        self.assertFalse((p / self.paths["a"]).parent.exists())       # folder x emptied -> removed
        self.assertTrue((p / self.paths["c"]).exists())
        self.assertTrue((p / self.paths["d"]).exists())
        self.assertTrue((p / "frames").is_dir())
        self.assertEqual({k: out[k] for k in ("frames", "files", "files_skipped", "looks", "eye_minutes", "packages",
                                               "scenes", "segments", "runs", "log_rows")},
                         {"frames": 2, "files": 2, "files_skipped": 0, "looks": 1, "eye_minutes": 2, "packages": 2,
                          "scenes": 2, "segments": 2, "runs": 3, "log_rows": 4})

    def test_shared_body_is_kept_unique_body_is_deleted(self):
        out = self.run_delete()
        self.assertEqual(self.ids(self.body), sorted([sha("a"), sha("f"), sha("1")]))
        self.assertEqual(out["bodies"], 4)                             # b, c, d, e

    def test_body_shared_under_another_name_is_kept(self):
        self.db[self.log].insert_one({"_id": "x:1:1", "kind": "call.other", "at": M2, "body": {"result": sha("c")}})
        out = self.run_delete()
        self.assertIn(sha("c"), self.ids(self.body))
        self.assertEqual(out["bodies"], 3)                             # b, d, e

    def test_many_long_fragments_are_batched_under_the_regex_cap(self):
        sentences = [f"第{i:03d}句" + "很长的描述文字" * 9 for i in range(300)]
        self.db["vision_looks"].docs[0]["text"] = "。".join(sentences)
        self.db[self.body].docs.append({"_id": sha("9"), "text": "上下文：" + "；".join(sentences)})
        self.db[self.log].insert_one({"_id": "ear:1:12", "kind": "call.realtime", "at": M1 + 99_000,
                                      "body": {"input": sha("9")}})
        coll, seen = self.db[self.body], []
        real = coll.find

        def spy(flt=None, projection=None):
            if flt and "$regex" in str(flt):
                seen.append(flt["text"]["$regex"])
            return real(flt, projection)
        coll.find = spy
        self.run_delete()
        self.assertGreater(len(seen), 1)
        self.assertTrue(all(len(p.encode()) <= gallery.REGEX_BYTES for p in seen))
        text = self.db[self.body].find_one({"_id": sha("9")})["text"]
        self.assertTrue(all(x not in text for x in sentences))
        self.assertEqual(text.count(gallery.MARK), 300)

    def test_nearby_runs_and_log_sums_are_redacted(self):
        out = self.run_delete()
        rn = self.db["mind_runs"].find_one({"_id": "rn"})
        self.assertEqual(rn["request"]["messages"][0]["content"], f"最近看到：{MARK}。{MARK}。好的")
        self.assertEqual((rn["request"]["n"], rn["response"]), (3, {"x": "无关的回答内容"}))
        self.assertEqual(self.db["mind_runs"].find_one({"_id": "rf"})["request"], {"c": LOOK})   # > 3 h away
        self.assertEqual(out["runs_redacted"], 1)
        self.assertEqual(self.db[self.log].find_one({"_id": "ear:1:11"})["sum"], f"我看到{MARK}")
        self.assertEqual(out["sums_redacted"], 1)

    def test_fragments_are_redacted_in_other_bodies_and_spool(self):
        size = self.today.stat().st_size
        old = self.old.read_bytes()
        out = self.run_delete()
        ctx = self.db[self.body].find_one({"_id": sha("1")})["text"]
        self.assertEqual(ctx, f"你看到的：{MARK}。主人说：好")
        self.assertEqual(out["bodies_redacted"], 1)
        data = self.today.read_bytes()
        self.assertEqual(len(data), size)
        text = data.decode("utf-8", errors="replace")
        for f in LOOK_FRAGS:
            self.assertNotIn(f, text)
        self.assertIn(b"*" * len(LOOK_FRAGS[0].encode()), data)
        for line in data.splitlines():
            json.loads(line)
        self.assertEqual(out["spool_redacted"], 1)
        self.assertEqual(self.old.read_bytes(), old)                   # outside the photos' days
        self.assertEqual((Path(self.spool) / "ear-20261120.offset").read_text(), "12")

    def test_stale_plan_is_refused(self):
        fp = gallery.plan(self.db, ["a", "b"], spool_dir=self.spool)["fp"]
        self.db["vision_looks"].insert_one({"_id": "la2", "kind": "look", "at": M1 + 9_000, "path": self.paths["a"],
                                            "text": "又看了一眼"})
        before, spool = self.counts(), self.today.read_bytes()
        with self.assertRaises(gallery.Stale):
            gallery.delete(self.db, self.root, ["a", "b"], fp, spool_dir=self.spool, emit=self.emit)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.today.read_bytes(), spool)
        self.assertTrue((Path(self.root) / self.paths["a"]).exists())
        self.assertEqual(self.events, [])

    def test_outside_path_is_skipped(self):
        out_file = Path(self.root).parent / "outside.jpg"
        out_file.write_bytes(b"secret")
        self.db["frames"].insert_one({"_id": "e", "at": M2 + 30_000, "path": "../outside.jpg"})
        out = self.run_delete(["e"])
        self.assertEqual(self.ids("frames"), ["a", "b", "c", "d"])
        self.assertTrue(out_file.exists())
        self.assertEqual((out["frames"], out["files"], out["files_skipped"]), (1, 0, 1))

    def test_run_log_line_has_no_paths_or_text(self):
        self.run_delete()
        self.assertEqual(len(self.events), 1)
        kind, fields = self.events[0]
        self.assertEqual(kind, "gallery.delete")
        self.assertEqual(fields["by"], "page")
        self.assertEqual(fields["sum"], "删除 2 张照片（15:30–15:30）")
        self.assertEqual(fields["extra"]["frames"], 2)
        self.assertEqual((fields["extra"]["from"], fields["extra"]["to"]), (M1 + 5_000, M1 + 20_000))
        blob = json.dumps(fields, ensure_ascii=False)
        self.assertNotIn("frames/", blob)
        for f in LOOK_FRAGS + ["书房里的书桌前面坐着一个人", "主人正在书房里安静地写东西", "下午在书桌前写作业的时间"]:
            self.assertNotIn(f, blob)

    def test_more_than_500_ids_is_refused(self):
        with self.assertRaises(ValueError):
            gallery.plan(self.db, [str(i) for i in range(501)], spool_dir=self.spool)
        with self.assertRaises(ValueError):
            gallery.delete(self.db, self.root, [str(i) for i in range(501)], "x", spool_dir=self.spool,
                           emit=self.emit)


if __name__ == "__main__":
    unittest.main()
