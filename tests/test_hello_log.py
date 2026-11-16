"""Run/audit log writer (server/common/hello_log.py): queue, redaction, body dedup, spool-then-ship,
replay. No Mongo, no network: every test runs on log_fixtures.FakeDB."""
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_fixtures import T0, FakeDB, capture_log, temp_dir   # noqa: E402
import hello_log                                             # noqa: E402

DAY = "20261110"            # T0's day in Beijing
MONTH = "202611"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def spool_lines(spool, svc, day=DAY):
    path = Path(spool) / f"{svc}-{day}.jsonl"
    return path.read_bytes().splitlines() if path.exists() else []


def spool_offset(spool, svc, day=DAY):
    path = Path(spool) / f"{svc}-{day}.offset"
    return int(path.read_text()) if path.exists() else 0


class HelloLogTest(unittest.TestCase):
    def tearDown(self):
        if hello_log._current is not None:
            hello_log._current.close(timeout_s=0)
            hello_log._current = None

    def test_emit_writes_flow_and_body(self):
        logger, db = capture_log("eye")
        hello_log.emit("call.eye", body={"system": "S", "output": "O"}, sum="x")
        self.assertTrue(logger.flush())
        flows = db.flows(MONTH)
        self.assertEqual(len(flows), 1)
        f = flows[0]
        self.assertEqual(f["_id"], f"eye:{logger.run}:1")
        self.assertRegex(logger.run, r"^[0-9a-f]{8}$")
        self.assertEqual((f["svc"], f["kind"], f["seq"], f["at"], f["sum"]), ("eye", "call.eye", 1, T0, "x"))
        self.assertEqual(f["body"], {"system": sha("S"), "output": sha("O")})
        self.assertEqual(db.bodies(MONTH), {sha("S"): "S", sha("O"): "O"})

    def test_body_dedup_within_month(self):
        logger, db = capture_log("mind")
        for i in range(3):
            hello_log.emit("call.notes", body={"system": "same prompt", "output": f"out {i}"})
        self.assertTrue(logger.flush())
        bodies = db.bodies(MONTH)
        self.assertEqual(sorted(bodies.values()), ["out 0", "out 1", "out 2", "same prompt"])
        upserts = [c for c in db.ops("update_one", "logbody_" + MONTH) if c[2] == {"_id": sha("same prompt")}]
        self.assertEqual(len(upserts), 1)
        # a later batch with the same prompt only references it
        hello_log.emit("call.notes", body={"system": "same prompt"})
        self.assertTrue(logger.flush())
        upserts = [c for c in db.ops("update_one", "logbody_" + MONTH) if c[2] == {"_id": sha("same prompt")}]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(db.flows(MONTH)[-1]["body"], {"system": sha("same prompt")})

    def test_redact_keys_and_url(self):
        out = hello_log.redact({"api_key": "sk-abc", "nested": {"Authorization": "Bearer x"},
                                "url": "wss://h/p?token=abc&x=1"})
        self.assertEqual(out, {"api_key": "***", "nested": {"Authorization": "***"},
                               "url": "wss://h/p?token=***&x=1"})
        self.assertEqual(hello_log.redact({"u": "https://h/a?x=1&Key=s3cr3t"}), {"u": "https://h/a?x=1&Key=***"})
        self.assertEqual(hello_log.redact({"api_keys": ["sk-a", "sk-b"], "Secret": {"value": "v", "n": 2}}),
                         {"api_keys": ["***", "***"], "Secret": {"value": "***", "n": 2}})
        # provider usage counts stay numbers
        usage = {"prompt_tokens": 12, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 0}}
        self.assertEqual(hello_log.redact({"usage": usage}), {"usage": usage})

    def test_redact_applies_to_flow_and_body(self):
        logger, db = capture_log("mind")
        hello_log.emit("svc.start", args={"password": "p", "db_uri": "mongodb://h/?authSource=a&token=t"},
                       body={"input": {"secret": "s", "messages": ["hi"]}})
        self.assertTrue(logger.flush())
        f = db.flows(MONTH)[0]
        self.assertEqual(f["args"], {"password": "***", "db_uri": "mongodb://h/?authSource=a&token=***"})
        text = db.bodies(MONTH)[f["body"]["input"]]
        self.assertEqual(json.loads(text), {"secret": "***", "messages": ["hi"]})

    def test_key_fp(self):
        self.assertEqual(hello_log.key_fp("sk-xTVl1234567"), "sk-xTVl")
        self.assertIsNone(hello_log.key_fp(None))
        logger, db = capture_log("mind")
        hello_log.emit("call.chat", key="sk-xTVl1234567")
        self.assertTrue(logger.flush())
        self.assertEqual(db.flows(MONTH)[0]["key"], "sk-xTVl")

    def test_data_url_stripped(self):
        logger, db = capture_log("eye")
        url = "data:image/jpeg;base64," + "A" * 5000
        messages = [{"role": "user", "content": [{"type": "text", "text": "看图"},
                                                  {"type": "image_url", "image_url": {"url": url}}]}]
        hello_log.emit("call.eye", body={"input": messages, "note": "inline ![p](" + url + ") end"}, ref={"photo": url})
        self.assertTrue(logger.flush())
        f = db.flows(MONTH)[0]
        stored = json.loads(db.bodies(MONTH)[f["body"]["input"]])
        self.assertEqual(stored[0]["content"][1]["image_url"]["url"], "[image omitted, 5023 bytes]")
        self.assertEqual(db.bodies(MONTH)[f["body"]["note"]], "inline ![p]([image omitted, 5023 bytes]) end")
        self.assertEqual(f["ref"], {"photo": "[image omitted, 5023 bytes]"})
        for text in db.bodies(MONTH).values():
            self.assertNotIn("base64,", text)
        self.assertNotIn("base64,", json.dumps(f))
        for line in spool_lines(logger.spool_dir, "eye"):
            self.assertNotIn(b"base64,", line)

    def test_month_boundary_beijing(self):
        self.assertEqual(hello_log.month_of(1796054399999), "202611")
        self.assertEqual(hello_log.month_of(1796054400000), "202612")
        logger, db = capture_log("mind")
        hello_log.emit("call.notes", at=1796054399999, body={"output": "same"})
        hello_log.emit("call.notes", at=1796054400000, body={"output": "same"})
        self.assertTrue(logger.flush())
        self.assertEqual([f["at"] for f in db.flows("202611")], [1796054399999])
        self.assertEqual([f["at"] for f in db.flows("202612")], [1796054400000])
        self.assertEqual(db.bodies("202611"), {sha("same"): "same"})
        self.assertEqual(db.bodies("202612"), {sha("same"): "same"})

    def test_spool_then_ship(self):
        logger, db = capture_log("ear")
        hello_log.emit("heard.asr", sum="你好")
        self.assertTrue(logger.flush())
        path = Path(logger.spool_dir) / f"ear-{DAY}.jsonl"
        lines = spool_lines(logger.spool_dir, "ear")
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["flow"]["sum"], "你好")
        self.assertEqual(spool_offset(logger.spool_dir, "ear"), path.stat().st_size)
        self.assertEqual(logger.stats()["backlog_lines"], 0)
        self.assertEqual(logger.stats()["shipped"], 1)

    def test_db_down_then_replay(self):
        spool = temp_dir()
        down = FakeDB()
        down.fail = ConnectionError("mongo down")
        logger, _ = capture_log("mind", db=down, spool_dir=spool)
        hello_log.emit("call.chat", sum="a", body={"output": "A"})
        hello_log.emit("call.chat", sum="b")
        self.assertFalse(logger.flush(timeout_s=2))
        self.assertEqual(len(spool_lines(spool, "mind")), 2)
        self.assertEqual(spool_offset(spool, "mind"), 0)
        st = logger.stats()
        self.assertEqual((st["backlog_lines"], st["shipped"]), (2, 0))
        self.assertGreaterEqual(st["db_errors"], 1)
        old = [json.loads(x)["flow"] for x in spool_lines(spool, "mind")]
        old_run = logger.run

        logger2, db = capture_log("mind", spool_dir=spool, clock_ms=lambda: T0 + 60000)
        self.assertNotEqual(logger2.run, old_run)
        self.assertTrue(logger2.flush())
        flows = db.flows(MONTH)
        self.assertEqual([(f["_id"], f["at"]) for f in flows], [(o["_id"], o["at"]) for o in old])
        self.assertEqual([f["_id"] for f in flows], [f"mind:{old_run}:1", f"mind:{old_run}:2"])
        self.assertEqual(db.bodies(MONTH), {sha("A"): "A"})
        self.assertEqual(spool_offset(spool, "mind"), (Path(spool) / f"mind-{DAY}.jsonl").stat().st_size)
        self.assertEqual(logger2.stats()["backlog_lines"], 0)

    def test_replay_idempotent(self):
        spool = temp_dir()
        down = FakeDB()
        down.fail = ConnectionError("mongo down")
        logger, _ = capture_log("mind", db=down, spool_dir=spool)
        hello_log.emit("call.chat", sum="a", body={"output": "A"})
        hello_log.emit("call.chat", sum="b")
        self.assertFalse(logger.flush(timeout_s=2))
        db = FakeDB()
        logger2, _ = capture_log("mind", db=db, spool_dir=spool)
        self.assertTrue(logger2.flush())
        logger2.close()
        (Path(spool) / f"mind-{DAY}.offset").write_text("0")        # killed before the offset was saved
        logger3, _ = capture_log("mind", db=db, spool_dir=spool)
        self.assertTrue(logger3.flush())
        self.assertEqual(len(db.flows(MONTH)), 2)
        self.assertEqual(len(db.ops("insert_many", "log_" + MONTH)), 2)   # the second one hit duplicate keys
        self.assertEqual(logger3.stats()["db_errors"], 0)
        self.assertEqual(logger3.stats()["backlog_lines"], 0)
        self.assertEqual(spool_offset(spool, "mind"), (Path(spool) / f"mind-{DAY}.jsonl").stat().st_size)

    def test_emit_fast_when_db_slow(self):
        db = FakeDB()
        db.delay_s = 2
        logger, _ = capture_log("ear", db=db)
        t = time.perf_counter()
        for i in range(100):
            hello_log.emit("heard.asr", sum=f"句子 {i}", body={"input": "x" * 200})
        elapsed = time.perf_counter() - t
        self.assertLess(elapsed, 0.05)
        self.assertEqual(logger.stats()["queued"], 100)

    def test_spool_unwritable_still_ships(self):
        if os.geteuid() == 0:
            self.skipTest("root can write a read-only folder")
        spool = temp_dir() / "ro"
        spool.mkdir(mode=0o500)
        self.addCleanup(os.chmod, spool, 0o700)
        logger, db = capture_log("tts", spool_dir=spool)
        hello_log.emit("call.tts", sum="合成", body={"input": "你好"})
        self.assertTrue(logger.flush())
        self.assertEqual(len(db.flows(MONTH)), 1)
        self.assertEqual(db.bodies(MONTH), {sha("你好"): "你好"})
        self.assertGreaterEqual(logger.stats()["spool_errors"], 1)
        self.assertEqual(list(spool.iterdir()), [])

    def test_disk_full_leaves_no_half_line(self):
        logger, db = capture_log("ear")
        hello_log.emit("heard.asr", sum="first")
        self.assertTrue(logger.flush())
        path = Path(logger.spool_dir) / f"ear-{DAY}.jsonl"
        size = path.stat().st_size
        with patch.object(hello_log.os, "fsync", side_effect=OSError(28, "No space left on device")):
            hello_log.emit("heard.asr", sum="second")
            self.assertTrue(logger.flush())
        self.assertEqual(path.stat().st_size, size)
        self.assertEqual([f["sum"] for f in db.flows()], ["first", "second"])
        self.assertEqual(logger.stats()["spool_errors"], 1)

    def test_big_body_truncated(self):
        logger, db = capture_log("mind")
        size = 5 * 1024 * 1024
        hello_log.emit("call.notes", body={"input": "a" * size})
        self.assertTrue(logger.flush())
        text = db.bodies(MONTH)[db.flows(MONTH)[0]["body"]["input"]]
        marker = f"…[truncated, {size} bytes total]"
        self.assertEqual(text, "a" * (4 * 1024 * 1024) + marker)

    def test_indexes_created_once_per_month(self):
        logger, db = capture_log("mind")
        hello_log.emit("call.eye", sum="1")
        self.assertTrue(logger.flush())
        hello_log.emit("call.eye", sum="2")
        self.assertTrue(logger.flush())
        calls = db.ops("create_index", "log_" + MONTH)
        self.assertEqual(len(calls), 4)
        self.assertEqual(db.ops("create_index", "logbody_" + MONTH), [])
        first_insert = db.calls.index(db.ops("insert_many", "log_" + MONTH)[0])
        self.assertTrue(all(db.calls.index(c) < first_insert for c in calls))
        keys = [c[2] for c in calls]
        self.assertEqual(keys[:3], [[("at", 1)], [("kind", 1), ("at", 1)], [("trace", 1)]])
        self.assertEqual(calls[3][3]["partialFilterExpression"], {"cost": {"$exists": True}})
        self.assertEqual(calls[3][2][0][0], "at")
        self.assertEqual(len(db.ops("insert_many", "log_" + MONTH)), 2)

    def test_emit_never_raises(self):
        class BadRepr:
            def __repr__(self):
                raise RuntimeError("no repr")

        logger, db = capture_log("mind")
        thing = object()
        hello_log.emit("svc.error", ref={"o": thing}, odd=thing, bad=BadRepr(), body={"input": {"o": thing}},
                       raw=b"\x00\x01", big=2 ** 70)
        self.assertTrue(logger.flush())
        f = db.flows(MONTH)[0]
        self.assertEqual(f["ref"], {"o": repr(thing)})
        self.assertEqual(f["odd"], repr(thing))
        self.assertIsInstance(f["bad"], str)
        self.assertEqual(f["raw"], "[bytes omitted, 2 bytes]")
        self.assertEqual(f["big"], str(2 ** 70))
        self.assertEqual(json.loads(db.bodies(MONTH)[f["body"]["input"]]), {"o": repr(thing)})

    def test_emit_without_init_is_noop(self):
        hello_log._current = None
        self.assertIsNone(hello_log.emit("call.eye", sum="x"))

    def test_line_cut_by_kill_is_skipped(self):
        spool = temp_dir()
        good = {"flow": {"_id": "mind:0000abcd:1", "at": T0, "svc": "mind", "kind": "heard.asr"}, "bodies": {}}
        (spool / f"mind-{DAY}.jsonl").write_text(json.dumps(good) + "\n" + '{"flow": {"_id": "mind:0000abcd:2"')
        logger, db = capture_log("mind", spool_dir=spool)
        hello_log.emit("heard.asr", sum="after restart")
        self.assertTrue(logger.flush())
        self.assertEqual([f["_id"] for f in db.flows()], ["mind:0000abcd:1", f"mind:{logger.run}:1"])
        self.assertEqual(logger.stats()["spool_errors"], 1)
        self.assertEqual(logger.stats()["backlog_lines"], 0)

    def test_old_shipped_spool_pruned(self):
        spool = temp_dir()
        old = spool / "mind-20261101.jsonl"
        old.write_text('{"flow": {}}\n')
        (spool / "mind-20261101.offset").write_text(str(old.stat().st_size))
        unshipped = spool / "mind-20261102.jsonl"         # old but never shipped: replayed, not deleted
        line = {"flow": {"_id": "mind:0000abcd:1", "at": T0 - 8 * 86400000, "svc": "mind", "kind": "svc.beat"},
                "bodies": {}}
        unshipped.write_text(json.dumps(line) + "\n")
        recent = spool / "mind-20261105.jsonl"
        recent.write_text("")
        logger, db = capture_log("mind", spool_dir=spool)
        self.assertTrue(logger.flush())
        self.assertFalse(old.exists())
        self.assertFalse((spool / "mind-20261101.offset").exists())
        self.assertTrue(recent.exists())
        self.assertEqual([f["_id"] for f in db.flows()], ["mind:0000abcd:1"])


if __name__ == "__main__":
    unittest.main()
