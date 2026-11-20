"""图库 reads (2026-11-20): server/mind/gallery.py over FakeDB and a temp recordings root."""
import base64
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
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

    def test_live_eye_scene_rows_count_as_read(self):
        """C1: the live eye's 识别 rows (vision_looks kind scene, with the photo's path) are shown like 看一眼."""
        at = ms(2026, 11, 20, 10, 5, 30)
        self.frame("a", at, 1)
        self.db["vision_looks"].insert_one({"kind": "scene", "lid": "live-s-1", "at": at + 1000,
                                            "path": "frames/s/1.jpg", "text": "主人坐在床边看手机"})
        minute = at // 60000 * 60000
        self.assertTrue(gallery.frames(self.db, minute, minute + 3600_000)[0]["look"])
        self.assertEqual(gallery.detail(self.db, self.root, "a")["looks"],
                         [{"at": at + 1000, "kind": "scene", "text": "主人坐在床边看手机"}])

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
        self.assertEqual(d["looks"], [{"at": at, "kind": "look", "text": "一杯茶"}])
        self.assertEqual(d["eye"]["minute"], minute)
        self.assertEqual(d["eye"]["text"], "写字\n书房")
        self.assertEqual(d["scene"], {"minute": minute, "text": "新情景"})
        self.assertIsNone(gallery.detail(self.db, self.root, "nope"))

    def test_texts(self):
        obj = {"key": "abc", "text": "你好", "a": ["x", "吃饭", {"op": "add", "doing": "写字", "confidence": "high"}],
               "n": 3}
        self.assertEqual(gallery.texts(obj), ["你好", "吃饭", "写字"])
        self.assertEqual(gallery.texts(None), [])

    def test_texts_only_descriptive_fields(self):
        """I8: ids, codes and machine values are no description (they would become fragments and over-redact)."""
        obj = {"kind": "look", "lane": "live", "id": "abc", "_id": "x1", "code": "ok_code", "source": "model",
               "model": "qwen-vl-max", "prompt": "eye-v3", "version": "v2", "who_id": "owner", "subject_id": "room",
               "run_id": "r123", "dimension": "room", "people": [{"who": "me", "who_id": "p_1", "doing": "低头写字"}],
               "scene": "书房", "tag": "eye_minutes:1794300000000", "en": "A cup of tea on the desk", "cjk": "写字abc"}
        self.assertEqual(gallery.texts(obj), ["低头写字", "书房", "A cup of tea on the desk", "写字abc"])

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
                                             "at": M2 + 70_000, "summary": {"title": "现在还在进行的段落", "text": "t"}})
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
        self.assertEqual(self.ids(self.log), ["ear:1:11", "ear:1:9", "eye:1:1", "eye:1:2", "eye:1:3", "mind:1:1",
                                              "mind:1:2", "mind:1:3", "mind:1:4", "mind:1:5"])    # call.* kept stripped
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

    def test_only_the_eye_and_scene_runs_of_the_minutes_are_deleted(self):
        """I3: chat / say / notes / model calls of the same minute are not derived from the photo: kept (redacted)."""
        self.db.add("mind_runs",
                    {"_id": "re1", "minute": M1, "lane": "live", "step": "eye", "request": {"c": LOOK}},
                    {"_id": "rc", "minute": M1, "lane": "live", "step": "chat", "at": M1 + 30_000,
                     "request": {"c": "你看：" + LOOK}},
                    {"_id": "rsay", "minute": M1, "lane": "live", "step": "say", "at": M1 + 31_000},
                    {"_id": "rno", "minute": M1, "lane": "live", "step": "notes", "at": M1 + 32_000},
                    {"_id": "rmc", "minute": M1, "lane": "live", "step": "tool_answer", "at": M1 + 33_000})
        out = self.run_delete()
        self.assertEqual(self.ids("mind_runs"), ["r2", "rc", "rf", "rmc", "rn", "rno", "rs1r", "rs2", "rsay"])
        self.assertEqual(out["runs"], 4)                               # r1, r1b (fusion), re1 (eye), rs1 (segment)
        self.assertEqual(self.db["mind_runs"].find_one({"_id": "rc"})["request"]["c"], f"你看：{MARK}。{MARK}。好的")

    def test_call_rows_are_kept_stripped_and_still_cost(self):
        """M3: a deleted photo's call.* rows keep what the daily-cap ledger needs and nothing that describes it."""
        import hello_log_query
        from log_fixtures import FakeDB as LogDB
        cost = {"cur": "CNY", "amount": 0.25}
        row = self.db[self.log].find_one({"_id": "eye:1:1"})
        self.db[self.log].replace_one({"_id": "eye:1:1"}, {**row, "run": "1", "seq": 1, "ok": True, "ms": 812,
                                                          "model": "qwen-vl", "usage": {"in": 9}, "cost": cost,
                                                          "trace": "t1", "extra": {"q": LOOK}, "key": "k"})
        out = self.run_delete()
        self.assertEqual(out["log_rows"], 4)
        kept = self.db[self.log].find_one({"_id": "eye:1:1"})
        self.assertEqual(kept, {"_id": "eye:1:1", "kind": "call.look", "at": M1 + 7_000, "svc": "ear", "run": "1",
                                "seq": 1, "ok": True, "ms": 812, "model": "qwen-vl", "usage": {"in": 9},
                                "cost": cost})
        for i in ("eye:1:2", "mind:1:1", "mind:1:2"):
            self.assertFalse({"ref", "body", "sum"} & set(self.db[self.log].find_one({"_id": i})), i)
        ldb = LogDB()
        for d in self.db[self.log].docs:
            ldb[self.log].docs[d["_id"]] = d
        self.assertEqual(hello_log_query.ledger(ldb, "2026-11-20")["total"], {"CNY": 0.25})
        again = gallery.plan(self.db, ["c"], spool_dir=self.spool)       # a stripped row is not found again
        self.assertEqual(again["log_rows"], 0)

    def test_second_batch_planned_before_the_first_is_not_stale(self):
        """M1: records gone since the plan (deleted by batch 1) do not make batch 2 stale; a new record does."""
        fp1 = gallery.plan(self.db, ["a"], spool_dir=self.spool)["fp"]
        fp2 = gallery.plan(self.db, ["b"], spool_dir=self.spool)["fp"]
        gallery.delete(self.db, self.root, ["a"], fp1, spool_dir=self.spool, emit=self.emit)
        out = gallery.delete(self.db, self.root, ["b"], fp2, spool_dir=self.spool, emit=self.emit)
        self.assertEqual(out["frames"], 1)
        self.assertEqual(self.ids("frames"), ["c", "d"])
        fp3 = gallery.plan(self.db, ["c"], spool_dir=self.spool)["fp"]
        self.db["vision_looks"].insert_one({"_id": "lc", "kind": "look", "at": M1 + 41_000, "path": self.paths["c"],
                                            "text": "新的一眼"})
        with self.assertRaises(gallery.Stale):
            gallery.delete(self.db, self.root, ["c"], fp3, spool_dir=self.spool, emit=self.emit)
        with self.assertRaises(gallery.Stale):                         # never planned
            gallery.delete(self.db, self.root, ["d"], "0" * 16, spool_dir=self.spool, emit=self.emit)

    def test_plan_lists_the_distinct_minutes(self):
        """M2: the page sums minutes over batches without counting a minute twice."""
        self.assertEqual(gallery.plan(self.db, ["a", "b"], spool_dir=self.spool)["minutes"], [M1])
        self.assertEqual(gallery.plan(self.db, ["a", "d"], spool_dir=self.spool)["minutes"], [M1, M2])

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

    def open_segment(self, id, start, **extra):
        self.db["mind_segments"].insert_one({"_id": id, "start": start, "end": None, "lane": "live", "version": 1,
                                             "summary": {"title": f"段落{id}", "text": "t"}, **extra})

    def segment_ids(self, ids):
        return sorted(s["title"][2:] for s in gallery.plan(self.db, ids, spool_dir=self.spool)["segments"])

    def test_old_open_segment_versions_do_not_match(self):
        """Production (2026-11-21 read-only check): 835 of 1009 mind_segments rows are old open versions (end None,
        written 5 min after their start, never closed). An open row covers [start, at) only, not until now."""
        old = M2 - 10 * 86400_000
        self.open_segment("o1", old, at=old + 300_000)                     # (1) days before: not matched
        self.db[self.log].insert_one({"_id": "mind:9:1", "kind": "call.segment", "at": old + 300_000,
                                      "ref": {"minute": old, "lane": "live"}})
        self.db["mind_runs"].insert_one({"_id": "ro1", "minute": old, "lane": "live", "step": "segment"})
        self.open_segment("o2", M2 - 120_000, at=M2 + 30_000)               # (2) [start, at) holds M2: matched
        self.open_segment("o3", M2 - 300_000, at=M2 - 60_000)               # ended before M2: not matched
        self.open_segment("o4", M2)                                         # (3) no at: [start, start + 60 s)
        self.open_segment("o5", M2 - 60_000)                                # no at, the minute before: not matched
        self.open_segment("o6", M2 - 60_000, at=None)
        out = gallery.plan(self.db, ["d"], spool_dir=self.spool)
        self.assertEqual(self.segment_ids(["d"]), ["o2", "o4", "去客厅休息的时间段"])      # + g2 (closed, overlaps)
        self.assertEqual(out["runs"], 2)                                    # r2 + rs2 (g2's run), not ro1
        self.assertEqual(out["log_rows"], 2)                                # eye:1:3 + g2's call.segment, not mind:9:1
        self.run_delete(["d"])
        self.assertEqual([i for i in self.ids("mind_segments") if i.startswith("o")], ["o1", "o3", "o5", "o6"])
        self.assertIn("ro1", self.ids("mind_runs"))
        self.assertIn("ref", self.db[self.log].find_one({"_id": "mind:9:1"}))

    def test_segments_starting_after_the_photo_minute_never_match(self):
        """Guard: photos in one minute cannot match a segment that starts after that minute, open or closed."""
        self.open_segment("a1", M2 + 60_000, at=M2 + 600_000)
        self.db["mind_segments"].insert_one({"_id": "a2", "start": M2 + 120_000, "end": M2 + 600_000, "lane": "live",
                                             "summary": {"title": "段落a2", "text": "t"}})
        self.open_segment("a3", M2 + 60_000)
        self.assertFalse({"a1", "a2", "a3"} & set(self.segment_ids(["d"])))

    def test_live_eye_scene_text_is_redacted_elsewhere(self):
        """C1 (reviewer's scene_leak.py): a vision_looks kind scene row's text quoted in a realtime instructions body."""
        text = "主人穿着红色睡衣坐在床边看手机"
        self.db["vision_looks"].insert_one({"_id": "ls", "kind": "scene", "lid": "live-s-1", "at": M1 + 8_000,
                                            "path": self.paths["a"], "text": text})
        self.db[self.log].insert_one({"_id": "ear:1:20", "kind": "call.realtime", "at": M1 + 9_000,
                                      "body": {"instructions": sha("7")}})
        self.db[self.body].insert_one({"_id": sha("7"), "text": "3 秒前：" + text})
        out = self.run_delete()
        self.assertNotIn("ls", self.ids("vision_looks"))
        self.assertEqual(out["looks"], 2)
        self.assertEqual(self.db[self.body].find_one({"_id": sha("7")})["text"], "3 秒前：" + MARK)

    def test_memory_evidence_excerpts_and_statements_are_redacted(self):
        """I4: mind_memory_evidence refs to a deleted eye minute carry its activity text (memory_worker.py)."""
        self.db.add("mind_memory_evidence",
                    {"_id": "ev1", "lane": "live", "scope_id": "home", "window_ms": M1 - 600_000, "version": 1,
                     "observations": [
                         {"key": "desk", "statement": "下午书房里的书桌前面坐着一个人", "refs": [
                             {"c": "eye_minutes", "minute": M1, "version": 1, "excerpt": "低头在笔记本上写字很认真"},
                             {"c": "speech", "id": "s1", "excerpt": "我在写作业"}]},
                         {"key": "sofa", "statement": "客厅沙发上没有人", "refs": [
                             {"c": "eye_minutes", "minute": M2, "version": 1, "excerpt": "客厅沙发上没有人"}]}]},
                    {"_id": "ev2", "lane": "live", "scope_id": "home", "window_ms": M1, "version": 1,
                     "observations": [{"key": "x", "statement": "书房里的书桌前面坐着一个人", "refs": [
                         {"c": "speech", "id": "s2", "excerpt": "好"}]}]})
        before = self.db["mind_memory_evidence"].find_one({"_id": "ev2"})
        out = self.run_delete()
        ev = self.db["mind_memory_evidence"].find_one({"_id": "ev1"})
        self.assertEqual(ev["observations"][0]["statement"], "下午" + MARK)
        self.assertEqual([r["excerpt"] for r in ev["observations"][0]["refs"]], [MARK, "我在写作业"])
        self.assertEqual(ev["observations"][1], {"key": "sofa", "statement": "客厅沙发上没有人", "refs": [
            {"c": "eye_minutes", "minute": M2, "version": 1, "excerpt": "客厅沙发上没有人"}]})
        self.assertEqual(self.db["mind_memory_evidence"].find_one({"_id": "ev2"}), before)
        self.assertEqual(out["evidence_redacted"], 1)

    def test_neighbour_minutes_quoting_the_photos_are_redacted(self):
        """M7: eye_minutes / mind_packages / mind_scenes within 3 minutes (eye.recent_looks) that are not deleted."""
        self.db.add("eye_minutes", {"_id": "en", "minute": M1 + 120_000, "version": 1, "frames": [],
                                    "result": {"scene": "最近看到：" + LOOK, "people": []}},
                    {"_id": "ef", "minute": M1 + 240_000, "version": 1, "result": {"scene": LOOK}})
        self.db.add("mind_packages", {"_id": "pn", "minute": M1 - 180_000, "lane": "live", "version": 1,
                                      "package": {"eye": {"recent_looks": [{"at": 1, "text": LOOK}]}}})
        self.db.add("mind_scenes", {"_id": "sn", "minute": M1 + 60_000, "lane": "live", "version": 1,
                                    "scene": {"text": "还是" + "主人正在书房里安静地写东西"}})
        out = self.run_delete()
        self.assertEqual(self.db["eye_minutes"].find_one({"_id": "en"})["result"]["scene"],
                         f"最近看到：{MARK}。{MARK}。好的")
        self.assertEqual(self.db["mind_packages"].find_one({"_id": "pn"})["package"]["eye"]["recent_looks"],
                         [{"at": 1, "text": f"{MARK}。{MARK}。好的"}])
        self.assertEqual(self.db["mind_scenes"].find_one({"_id": "sn"})["scene"]["text"], "还是" + MARK)
        self.assertEqual(self.db["eye_minutes"].find_one({"_id": "ef"})["result"]["scene"], LOOK)   # 4 min away
        self.assertEqual(out["neighbours_redacted"], 3)

    def test_spool_lines_of_deleted_rows_are_wiped(self):
        """I7: short values (主人, 书房) of a deleted row's own spool line survive fragment redaction: wipe them all."""
        eye = Path(self.spool) / "eye-20261120.jsonl"
        own = jline({"_id": "eye:1:1", "kind": "call.look", "at": M1 + 7_000, "svc": "ear", "sum": "主人",
                     "ref": {"path": self.paths["a"], "lid": 1}, "body": {"output": sha("b")}, "ms": 812},
                    {sha("b"): "书房\"里"})
        other = jline({"_id": "eye:1:9", "kind": "call.eye", "at": M1 + 8_000, "sum": "主人"}, {})
        eye.write_text(own + other, encoding="utf-8")
        size = eye.stat().st_size
        self.run_delete()
        data = eye.read_bytes()
        self.assertEqual(len(data), size)
        first, second = data.decode().splitlines()
        rec = json.loads(first)
        self.assertEqual(rec["flow"]["_id"], "eye:1:1")                 # replay stays idempotent (duplicate _id)
        self.assertEqual((rec["flow"]["at"], rec["flow"]["ms"], rec["flow"]["ref"]["lid"]), (M1 + 7_000, 812, 1))
        self.assertEqual(rec["flow"]["sum"], "******")
        self.assertEqual(set(rec["flow"]["kind"] + rec["flow"]["svc"] + rec["flow"]["ref"]["path"]), {"*"})
        self.assertEqual(list(rec["bodies"]), [sha("b")])
        self.assertEqual(set(rec["bodies"][sha("b")]), {"*"})
        self.assertNotIn("主人", first)
        self.assertEqual(json.loads(second)["flow"]["sum"], "主人")      # another row's line: fragments only

    def test_spool_scan_stars_exactly_what_plain_replace_would(self):
        """The spool's byte-trie regex (one per batch, finditer over the file) vs. one replace per fragment and line:
        fragments that share prefixes or contain each other, and a trie too deep for the compiler (plain fallback)."""
        import random
        rnd = random.Random(7)
        chars = "主人书房桌上一杯茶看手机红色"
        frags = sorted({"".join(rnd.choice(chars) for _ in range(rnd.randint(8, 14))) for _ in range(300)}
                       | {"主人书房桌上一杯茶", "主人书房桌上一杯茶看手机"}, key=lambda f: (-len(f), f))
        deep = ["甲" * k + "乙" for k in range(8, 1300)]
        for words in (frags, deep):
            with tempfile.TemporaryDirectory() as d:
                lines = [jline({"_id": f"ear:1:{i}", "kind": "call.realtime"},
                               {"x": "".join(rnd.choice(chars + "甲乙") for _ in range(200))}) for i in range(300)]
                (Path(d) / "ear-20261120.jsonl").write_text("".join(lines), encoding="utf-8")
                gallery.redact.redact_spool(d, words, M1, M1)
                got = (Path(d) / "ear-20261120.jsonl").read_text(encoding="utf-8")
            want = []
            for line in lines:
                b = line.encode()
                for w in words:
                    n = w.encode()
                    b = b.replace(n, b"*" * len(n))
                want.append(b.decode())
            self.assertEqual(got, "".join(want))

    def test_bodies_and_sums_are_redacted_only_near_the_photos(self):
        """C2: only bodies referenced by log rows with at in [from - 1 h, to + 48 h] (and those rows' sums) are
        candidates, not the whole month."""
        quote = "看到" + LOOK
        frm, to = M1 + 5_000, M1 + 20_000
        for i, (at, c) in enumerate([(frm - 2 * 3600_000, "2"), (frm - 3000_000, "3"), (to + 47 * 3600_000, "4"),
                                     (to + 49 * 3600_000, "5")]):
            self.db[self.log].insert_one({"_id": f"ear:2:{i}", "kind": "call.realtime", "at": at, "sum": quote,
                                          "body": {"input": sha(c)}})
            self.db[self.body].insert_one({"_id": sha(c), "text": quote})
        self.db[self.body].insert_one({"_id": sha("6"), "text": quote})          # no row refers to it
        out = self.run_delete()
        text = {c: self.db[self.body].find_one({"_id": sha(c)})["text"] for c in "23456"}
        self.assertEqual(text, {"2": quote, "3": f"看到{MARK}。{MARK}。好的", "4": f"看到{MARK}。{MARK}。好的",
                                "5": quote, "6": quote})
        sums = [self.db[self.log].find_one({"_id": f"ear:2:{i}"})["sum"] for i in range(4)]
        self.assertEqual([s == quote for s in sums], [True, False, False, True])
        self.assertEqual((out["bodies_redacted"], out["sums_redacted"]), (3, 3))   # + sha("1"); + ear:1:11

    def test_a_file_that_cannot_be_removed_keeps_its_record(self):
        """I6: file first, then its frames record; a file that cannot go keeps the record (retry later)."""
        real, stuck = os.remove, str(Path(self.root) / self.paths["a"])

        def remove(path, *a, **k):
            if os.path.realpath(path) == os.path.realpath(stuck):
                raise PermissionError(13, "denied")
            return real(path, *a, **k)
        with mock.patch("mind.gallery.os.remove", side_effect=remove):
            out = self.run_delete()
        self.assertEqual(self.ids("frames"), ["a", "c", "d"])
        self.assertTrue(Path(stuck).exists())
        self.assertFalse((Path(self.root) / self.paths["b"]).exists())
        self.assertEqual((out["frames"], out["files"], out["files_failed"]), (1, 1, 1))
        self.assertEqual(self.events[0][1]["extra"]["files_failed"], 1)

    def test_a_failure_midway_is_still_logged_with_what_was_done(self):
        """I6: gallery.delete is written even when the delete fails: error type and the counts so far."""
        steps = []
        with mock.patch.object(self.db["mind_scenes"], "delete_many", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                fp = gallery.plan(self.db, ["a", "b"], spool_dir=self.spool)["fp"]
                gallery.delete(self.db, self.root, ["a", "b"], fp, spool_dir=self.spool, emit=self.emit,
                               step=lambda name, counts: steps.append(name))
        self.assertEqual(len(self.events), 1)
        kind, f = self.events[0]
        self.assertEqual((kind, f["extra"]["error"], f["extra"]["log_rows"], f["extra"]["segments"]),
                         ("gallery.delete", "RuntimeError", 4, 2))
        self.assertNotIn("frames", f["extra"])
        self.assertIn("没删完", f["sum"])
        self.assertNotIn("db down", json.dumps(f, ensure_ascii=False))
        self.assertEqual(steps, ["bodies", "redact", "spool", "logs", "records"])
        self.assertEqual(self.ids("frames"), ["a", "b", "c", "d"])
        self.assertTrue((Path(self.root) / self.paths["a"]).exists())

    def test_steps_are_reported_in_order(self):
        steps = []
        fp = gallery.plan(self.db, ["a", "b"], spool_dir=self.spool)["fp"]
        out = gallery.delete(self.db, self.root, ["a", "b"], fp, spool_dir=self.spool, emit=self.emit,
                             step=lambda name, counts: steps.append((name, dict(counts))))
        self.assertEqual([n for n, _ in steps], ["bodies", "redact", "spool", "logs", "records", "files"])
        self.assertEqual(steps[-1][1]["segments"], 2)
        self.assertEqual(out["frames"], 2)

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
