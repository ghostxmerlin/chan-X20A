"""Run/audit log writer (server/common/hello_log.py): queue, redaction, body dedup, spool-then-ship,
replay. No Mongo, no network: every test runs on log_fixtures.FakeDB."""
import hashlib
import json
import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_fixtures import T0, FakeDB, capture_log, temp_dir   # noqa: E402
import hello_log                                             # noqa: E402

SHIP_TRIES_CALLS = 3 + 5 * 3   # whole-chunk tries + REFUSE_ALL records x tries (each fails at its 1st op)
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
        upserts = [c for c in db.ops("bulk_write", "logbody_" + MONTH) if sha("same prompt") in c[2]]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0][3], False)                 # ordered=False
        # a later batch with the same prompt only references it
        hello_log.emit("call.notes", body={"system": "same prompt"})
        self.assertTrue(logger.flush())
        upserts = [c for c in db.ops("bulk_write", "logbody_" + MONTH) if sha("same prompt") in c[2]]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(db.flows(MONTH)[-1]["body"], {"system": sha("same prompt")})

    def test_redact_keys_and_url(self):
        out = hello_log.redact({"api_key": "sk-abc", "nested": {"Authorization": "Bearer x"},
                                "url": "wss://h/p?token=abc&x=1"})
        self.assertEqual(out, {"api_key": "***", "nested": {"Authorization": "***"},
                               "url": "wss://h/p?token=***&x=1"})
        self.assertEqual(hello_log.redact({"u": "https://h/a?x=1&Key=s3cr3t"}), {"u": "https://h/a?x=1&Key=***"})
        src = {"keyword": "看看", "prompt_tokens": 12, "usage": {"input_tokens_details": {"cached": 1}},
               "x-api-key": "abc", "note": "sk-zzzz1234", "h": "Bearer q"}
        self.assertEqual(hello_log.redact(src),
                         {"keyword": "看看", "prompt_tokens": 12, "usage": {"input_tokens_details": {"cached": 1}},
                          "x-api-key": "***", "note": "***", "h": "***"})
        self.assertEqual(hello_log.redact({"Access_Token": "t", "auth": "a", "passwd": "p", "tokens": "many",
                                           "keys": ["k1", "sk-k2"], "Secret": {"value": "v", "n": 2},
                                           "h2": "bearer Q", "token": 7}),
                         {"Access_Token": "***", "auth": "***", "passwd": "***", "tokens": "many",
                          "keys": ["k1", "***"], "Secret": {"value": "***", "n": 2}, "h2": "***", "token": 7})
        # provider usage counts stay numbers
        usage = {"prompt_tokens": 12, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 0}}
        self.assertEqual(hello_log.redact({"usage": usage}), {"usage": usage})

    def test_redact_name_segments(self):
        kept = {"keyword": "看看", "keys": ["a"], "tokens": "many", "prompt_tokens": 1, "completion_tokens": 2,
                "input_tokens_details": {"cached": 1}, "usage": {"total_tokens": 3}, "session": "20261115/x-c1",
                "ref": {"session": "20261115/x-c1"}, "monkey": "m", "max_tokens": 9}
        self.assertEqual(hello_log.redact(kept), kept)
        masked = {"tokenValue": "t", "apiKey": "k", "MONGO_URI": "m", "db_uri": "d", "X-Api-Key": "x",
                  "sessionToken": "s", "auth": {"user": "u", "pass": "p", "port": 1},
                  "credentials": ["c1", "c2"], "client.secret": "c", "Cookie": "c"}
        self.assertEqual(hello_log.redact(masked),
                         {"tokenValue": "***", "apiKey": "***", "MONGO_URI": "***", "db_uri": "***",
                          "X-Api-Key": "***", "sessionToken": "***", "auth": {"user": "***", "pass": "***", "port": 1},
                          "credentials": ["***", "***"], "client.secret": "***", "Cookie": "***"})

    def test_url_params_use_the_name_classifier(self):
        self.assertEqual(hello_log.redact("wss://h/p?access-token=a&apiKey=b&auth_token=c&session_token=d"
                                          "&max_tokens=5&keyword=k&githubtoken=g&x=1"),
                         "wss://h/p?access-token=***&apiKey=***&auth_token=***&session_token=***"
                         "&max_tokens=5&keyword=k&githubtoken=***&x=1")

    def test_url_params_split_on_semicolon_and_question_mark(self):
        self.assertEqual(hello_log.redact("https://h/p?a=1;token=x"), "https://h/p?a=1;token=***")
        self.assertEqual(hello_log.redact("https://h/login?next=https://x/?token=y&keyword=看看"),
                         "https://h/login?next=https://x/?token=***&keyword=看看")
        self.assertEqual(hello_log.redact("https://h/s?keyword=看看"), "https://h/s?keyword=看看")
        self.assertEqual(hello_log.redact("https://h/p?token=abc;def&x=1"), "https://h/p?token=***&x=1")
        self.assertEqual(hello_log.redact("https://h/p?b=1&token=a?b#frag"), "https://h/p?b=1&token=***#frag")

    def test_auth_failure_keeps_records(self):
        class OperationFailure(Exception):
            def __init__(self, msg, code):
                super().__init__(msg)
                self.code = code

        for exc in (OperationFailure("bad auth", 18), OperationFailure("command insert requires auth", 13)):
            with self.subTest(exc=repr(exc)):
                self.assertTrue(hello_log._unreachable(exc))
        # by code only: document text echoed in a message must never stall the spool
        for exc in (RuntimeError("Authentication failed."), ValueError("user is not authorized on hello"),
                    OperationFailure("Document failed validation: not authorized", 121)):
            with self.subTest(exc=repr(exc)):
                self.assertFalse(hello_log._unreachable(exc))
        logger, db = capture_log("mind")
        db.fail = OperationFailure("Authentication failed.", 18)
        hello_log.emit("call.chat", sum="a")
        hello_log.emit("call.chat", sum="b")
        for _ in range(5):
            self.assertFalse(logger.flush())
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (0, 0, 2))
        db.fail = None
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["a", "b"])

    def test_name_segment_suffixes(self):
        masked = {"accesstoken": "a", "githubtoken": "g", "dbpassword": "p", "awsaccesskey": "k",
                  "myapikey": "m", "clientsecret": "c", "usercredential": "u"}
        self.assertEqual(hello_log.redact(masked), {k: "***" for k in masked})
        kept = {"maxtokens": "1", "tokens": "t", "keys": "k", "keyword": "w", "monkey": "m"}
        self.assertEqual(hello_log.redact(kept), kept)

    def test_bearer_any_case(self):
        self.assertEqual(hello_log.redact("auth header BeArEr xxxxxxxx1 sent"), "auth header Bearer *** sent")
        self.assertEqual(hello_log.redact({"note": "BEARER abcdefgh12"}), {"note": "***"})

    def test_url_query_secret_names_exact(self):
        self.assertEqual(hello_log.redact("https://h/p?max_tokens=5&keyword=hi&monkey=1&token=t&KEY=k"
                                          "&access_token=a&api_key=b"),
                         "https://h/p?max_tokens=5&keyword=hi&monkey=1&token=***&KEY=***&access_token=***&api_key=***")

    def test_redact_applies_to_flow_and_body(self):
        logger, db = capture_log("mind")
        hello_log.emit("svc.start", args={"password": "p", "endpoint": "mongodb://h/?authSource=a&token=t"},
                       body={"input": {"secret": "s", "messages": ["hi"]}})
        self.assertTrue(logger.flush())
        f = db.flows(MONTH)[0]
        self.assertEqual(f["args"], {"password": "***", "endpoint": "mongodb://h/?authSource=***&token=***"})
        text = db.bodies(MONTH)[f["body"]["input"]]
        self.assertEqual(json.loads(text), {"secret": "***", "messages": ["hi"]})

    def test_uri_userinfo_masked(self):
        logger, db = capture_log("mind")
        hello_log.emit("svc.start", args={"db_uri": "mongodb://u:p@h:27017/x", "url": "https://a:b@h/p"},
                       ref={"session": "20261115/x-c1"},
                       body={"input": "connect mongodb://u:p@h/x and mongodb+srv://u:p@c.h/y now"})
        self.assertTrue(logger.flush())
        f = db.flows(MONTH)[0]
        self.assertEqual(f["args"], {"db_uri": "***", "url": "https://***@h/p"})
        self.assertEqual(f["ref"], {"session": "20261115/x-c1"})
        text = db.bodies(MONTH)[f["body"]["input"]]
        self.assertEqual(text, "connect mongodb://***@h/x and mongodb+srv://***@c.h/y now")
        self.assertEqual(hello_log.redact({"MONGO_URI": "m", "Cookie": "c", "credentials": "x", "dsn": "d",
                                           "session_token": "t", "pwd": "p", "session": "20261115/x-c1"}),
                         {"MONGO_URI": "***", "Cookie": "***", "credentials": "***", "dsn": "***",
                          "session_token": "***", "pwd": "***", "session": "20261115/x-c1"})
        for line in spool_lines(logger.spool_dir, "mind"):
            self.assertNotIn(b"u:p", line)

    def test_free_text_secret_patterns(self):
        logger, db = capture_log("mind")
        hello_log.emit("call.chat", sum="用 sk-ABCDEFGH123 调用",
                       body={"input": "my key is sk-ABCDEFGH123 ok",
                             "context": "Authorization: Bearer abcdefgh12345\nnext", "output": "a sk-short task"})
        self.assertTrue(logger.flush())
        f = db.flows(MONTH)[0]
        bodies = db.bodies(MONTH)
        self.assertEqual(bodies[f["body"]["input"]], "my key is sk-*** ok")
        self.assertEqual(bodies[f["body"]["context"]], "Authorization: Bearer ***\nnext")
        self.assertEqual(bodies[f["body"]["output"]], "a sk-short task")       # too short to be a key
        self.assertEqual(f["sum"], "用 sk-*** 调用")
        for line in spool_lines(logger.spool_dir, "mind"):
            self.assertNotIn(b"ABCDEFGH123", line)
            self.assertNotIn(b"abcdefgh12345", line)

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

    def test_emit_odd_kwarg_names(self):
        logger, db = capture_log("mind")
        hello_log.emit("call.chat", kind="other", self="me", fields=1, _id="x", seq=99)
        logger.emit("call.chat", self="me", kind="other")
        self.assertTrue(logger.flush())
        flows = db.flows(MONTH)
        self.assertEqual([(f["kind"], f["self"], f["seq"]) for f in flows], [("call.chat", "me", 1), ("call.chat", "me", 2)])
        self.assertEqual(flows[0]["fields"], 1)
        self.assertEqual(flows[0]["_id"], f"mind:{logger.run}:1")

    def test_lone_surrogate_does_not_lose_the_batch(self):
        logger, db = capture_log("ear")
        hello_log.emit("heard.asr", sum="ok 1")
        hello_log.emit("heard.asr", sum="半个表情\ud83d", body={"input": "x\ud83dy", "\udc00name": "v"})
        hello_log.emit("heard.asr", sum="ok 2")
        self.assertTrue(logger.flush())
        flows = db.flows(MONTH)
        self.assertEqual([f["sum"] for f in flows], ["ok 1", "半个表情?", "ok 2"])
        self.assertEqual(db.bodies(MONTH)[flows[1]["body"]["input"]], "x?y")
        self.assertIn("?name", flows[1]["body"])
        self.assertEqual(len(spool_lines(logger.spool_dir, "ear")), 3)
        self.assertEqual(logger.stats()["dropped"], 0)

    def test_record_mongo_refuses_is_rejected_not_blocking(self):
        class InvalidDocument(Exception):
            pass

        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:1"] = InvalidDocument("cannot encode object")
        hello_log.emit("call.chat", sum="bad")
        hello_log.emit("call.chat", sum="good")
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["good"])
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (1, 1, 0))
        hello_log.emit("call.chat", sum="later")
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["good", "later"])

    def test_server_refused_document_counts_as_rejected(self):
        from log_fixtures import FakeBulkWriteError

        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:1"] = FakeBulkWriteError(
            {"writeErrors": [{"index": 0, "code": 2, "errmsg": "document too large"}], "writeConcernErrors": []})
        hello_log.emit("call.chat", sum="refused")
        self.assertTrue(logger.flush())
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (1, 0, 0))

    def test_record_that_always_times_out_waits_never_rejected(self):
        # a network-class failure never rejects (it may be the DB going away); the record waits
        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:2"] = TimeoutError("socket timed out")
        for name in ("before", "huge", "after"):
            hello_log.emit("call.chat", sum=name)
        results = [logger.flush() for _ in range(5)]
        self.assertEqual(results, [False] * 5)
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["before"])   # split pass got up to it
        st = logger.stats()
        self.assertEqual((st["rejected"], st["backlog_lines"]), (0, 2))
        del db.poison[f"mind:{logger.run}:2"]
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["before", "huge", "after"])

    def test_db_drops_during_split_pass_loses_nothing(self):
        class InvalidDocument(Exception):
            pass

        logger, db = capture_log("mind")
        ids = [f"mind:{logger.run}:{i}" for i in (1, 2, 3)]
        db.poison[ids[2]] = InvalidDocument("cannot encode")      # forces a record-by-record pass

        def go_down(op, collection, written):
            if op == "insert_many" and written == [ids[0]]:
                db.fail = ConnectionError("mongo went away")

        db.fail = ConnectionError("down while the three are spooled")    # so they ship as one chunk
        for name in ("one", "two", "three"):
            hello_log.emit("call.chat", sum=name)
        self.assertFalse(logger.flush())
        db.fail, db.after_write = None, go_down
        self.assertFalse(logger.flush())
        st = logger.stats()
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["one"])
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (0, 1, 2))
        first_line = spool_lines(logger.spool_dir, "mind")[0]
        self.assertEqual(spool_offset(logger.spool_dir, "mind"), len(first_line) + 1)
        db.after_write, db.fail = None, None
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["one", "two"])
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (1, 2, 0))

    def test_lone_poison_record_is_rejected(self):
        class InvalidDocument(Exception):
            pass

        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:1"] = InvalidDocument("cannot encode")
        hello_log.emit("call.chat", sum="alone")
        self.assertTrue(logger.flush())
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (1, 0, 0))

    def test_db_refusing_everything_keeps_all_cheaply(self):
        class OperationFailure(Exception):
            pass

        logger, db = capture_log("mind")
        db.fail = OperationFailure("Document failed validation")      # same error for every write
        for i in range(20):
            hello_log.emit("call.chat", sum=str(i))
        self.assertFalse(logger.flush())
        del db.calls[:]
        self.assertFalse(logger.flush())                  # one more full attempt
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (0, 0, 20))
        self.assertGreater(len(db.calls), 0)
        self.assertLessEqual(len(db.calls), SHIP_TRIES_CALLS)
        db.fail = None
        self.assertTrue(logger.flush())
        self.assertEqual(len(db.flows(MONTH)), 20)

    def test_db_down_keeps_every_line(self):
        logger, db = capture_log("mind")
        db.fail = ConnectionError("mongo down")
        for i in range(3):
            hello_log.emit("call.chat", sum=str(i))
        for _ in range(5):
            self.assertFalse(logger.flush())
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (0, 0, 3))
        self.assertEqual(spool_offset(logger.spool_dir, "mind"), 0)
        db.fail = None
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["0", "1", "2"])

    def test_spooling_continues_while_mongo_is_slow(self):
        db = FakeDB()
        db.delay_s = 1
        logger, _ = capture_log("ear", db=db)
        hello_log.emit("heard.asr", sum="first")
        time.sleep(0.3)                                   # the writer is now inside slow Mongo calls
        hello_log.emit("heard.asr", sum="second")
        t = time.monotonic()
        seen = False
        while time.monotonic() - t < 1.5 and not seen:
            seen = any(b'"second"' in line for line in spool_lines(logger.spool_dir, "ear"))
            time.sleep(0.05)
        self.assertTrue(seen, "second record did not reach the spool while Mongo was busy")
        self.assertLess(time.monotonic() - t, 1.3)
        self.assertEqual(db.flows(), [])                  # nothing shipped yet: spooled first

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

    def _shipped_file(self, spool, day, shipped_ms):
        path = spool / f"mind-{day}.jsonl"
        path.write_text('{"flow": {}}\n')
        marker = spool / f"mind-{day}.offset"
        marker.write_text(str(path.stat().st_size))
        os.utime(marker, (shipped_ms / 1000, shipped_ms / 1000))
        return path

    def test_spool_kept_7_days_after_fully_shipped(self):
        spool = temp_dir()
        old = self._shipped_file(spool, "20261101", T0 - 8 * 86400000)       # shipped 8 days ago
        late = self._shipped_file(spool, "20261102", T0 - 1 * 86400000)      # 8 days old, shipped yesterday
        replayed = spool / "mind-20261103.jsonl"
        line = {"flow": {"_id": "mind:0000abcd:1", "at": T0 - 7 * 86400000, "svc": "mind", "kind": "svc.beat"},
                "bodies": {}}
        replayed.write_text(json.dumps(line) + "\n")                     # never shipped: replayed now
        now = [T0]
        logger, db = capture_log("mind", spool_dir=spool, clock_ms=lambda: now[0])
        self.assertTrue(logger.flush())
        self.assertFalse(old.exists())
        self.assertFalse((spool / "mind-20261101.offset").exists())
        self.assertTrue(late.exists())
        self.assertTrue(replayed.exists())
        self.assertEqual([f["_id"] for f in db.flows()], ["mind:0000abcd:1"])
        now[0] = T0 + 6 * 86400000                       # 6 days after the replay: still kept
        self.assertTrue(logger.flush())
        self.assertTrue(replayed.exists())
        self.assertFalse(late.exists())                  # 7 days after it was shipped
        now[0] = T0 + 7 * 86400000 + 1000
        self.assertTrue(logger.flush())
        self.assertFalse(replayed.exists())

    def test_old_unshipped_spool_kept_while_db_down(self):
        spool = temp_dir()
        old = spool / "mind-20261101.jsonl"
        line = {"flow": {"_id": "mind:0000abcd:1", "at": T0 - 9 * 86400000, "svc": "mind", "kind": "svc.beat"},
                "bodies": {}}
        old.write_text(json.dumps(line) + "\n")
        os.utime(old, ((T0 - 9 * 86400000) / 1000,) * 2)
        down = FakeDB()
        down.fail = ConnectionError("mongo down")
        now = [T0]
        logger, _ = capture_log("mind", db=down, spool_dir=spool, clock_ms=lambda: now[0])
        self.assertFalse(logger.flush())
        now[0] = T0 + 30 * 86400000
        self.assertFalse(logger.flush())
        self.assertTrue(old.exists())
        self.assertEqual(logger.stats()["backlog_lines"], 1)

    # ---- I1 (final review): failures that are not about one record never reject records

    def assert_kept_then_shipped(self, logger, db, names, retry=lambda: None):
        for _ in range(3):
            self.assertFalse(logger.flush())
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (0, 0, len(names)))
        retry()
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], names)

    def test_bad_uri_on_connect_keeps_records(self):
        import types

        class InvalidURI(Exception):
            pass

        class ConfigurationError(Exception):
            pass

        for exc in (InvalidURI("Username and password must be escaped"), ConfigurationError("bad option")):
            with self.subTest(exc=repr(exc)):
                db, state = FakeDB(), {"exc": exc}

                def client(uri, **kw):
                    if state["exc"] is not None:
                        raise state["exc"]
                    return {"hello": db}

                fake = types.ModuleType("pymongo")
                fake.MongoClient = client
                with patch.dict(sys.modules, {"pymongo": fake}):
                    logger = hello_log.init("mind", db_uri="mongodb://u:p@ss@h/", spool_dir=temp_dir(),
                                            clock_ms=lambda: T0, lifecycle=False)
                    hello_log.emit("call.chat", sum="a")
                    hello_log.emit("call.chat", sum="b")
                    self.assert_kept_then_shipped(logger, db, ["a", "b"], lambda: state.update(exc=None))

    def test_index_creation_failure_keeps_records(self):
        class OperationFailure(Exception):
            code = 67                                    # CannotCreateIndex

        logger, db = capture_log("mind")
        col = db["log_" + MONTH]

        def refuse(keys, **kw):
            raise OperationFailure("cannot create index")

        col.create_index = refuse
        hello_log.emit("call.chat", sum="a")
        hello_log.emit("call.chat", sum="b")
        self.assert_kept_then_shipped(logger, db, ["a", "b"], lambda: delattr(col, "create_index"))

    def test_index_already_there_with_other_options_is_fine(self):
        class OperationFailure(Exception):
            code = 85                                    # IndexOptionsConflict: an index on these keys exists

        logger, db = capture_log("mind")

        def conflict(keys, **kw):
            raise OperationFailure("index exists with different options")

        db["log_" + MONTH].create_index = conflict
        hello_log.emit("call.chat", sum="a")
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["a"])

    def test_server_wide_failure_with_few_records_keeps_them(self):
        class OperationFailure(Exception):
            def __init__(self, msg, code):
                super().__init__(msg)
                self.code = code

        # 14031 = OutOfDiskSpace; 99999 = a refusal we have no name for: neither rejects 2 records
        for code in (14031, 99999):
            with self.subTest(code=code):
                logger, db = capture_log("mind")
                db.fail = OperationFailure("server says no", code)
                hello_log.emit("call.chat", sum="a")
                hello_log.emit("call.chat", sum="b")
                self.assert_kept_then_shipped(logger, db, ["a", "b"], lambda: setattr(db, "fail", None))

    def test_db_wide_write_error_codes_are_not_rejections(self):
        from log_fixtures import FakeBulkWriteError

        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:1"] = FakeBulkWriteError(
            {"writeErrors": [{"index": 0, "code": 14031, "errmsg": "out of disk space"}], "writeConcernErrors": []})
        hello_log.emit("call.chat", sum="a")
        self.assert_kept_then_shipped(logger, db, ["a"], lambda: db.poison.clear())

    def test_server_refused_poison_next_to_good_record_still_rejected(self):
        class OperationFailure(Exception):
            code = 2                                     # BadValue, for this one document only

        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:1"] = OperationFailure("bad value")
        hello_log.emit("call.chat", sum="poison")
        self.assertFalse(logger.flush())                 # alone: nothing proves the DB takes writes, it waits
        self.assertEqual(logger.stats()["rejected"], 0)
        hello_log.emit("call.chat", sum="good")          # e.g. the next svc.beat
        self.assertTrue(logger.flush())
        self.assertEqual([f["sum"] for f in db.flows(MONTH)], ["good"])
        st = logger.stats()
        self.assertEqual((st["rejected"], st["shipped"], st["backlog_lines"]), (1, 1, 0))

    def test_lone_unencodable_record_waits_when_ping_fails(self):
        class InvalidDocument(Exception):
            pass

        logger, db = capture_log("mind")
        db.poison[f"mind:{logger.run}:1"] = InvalidDocument("cannot encode")
        db.ping_fail = ConnectionError("no answer")
        hello_log.emit("call.chat", sum="alone")
        self.assertFalse(logger.flush())
        self.assertEqual(logger.stats()["rejected"], 0)
        db.ping_fail = None
        self.assertTrue(logger.flush())
        self.assertEqual(logger.stats()["rejected"], 1)

    def test_free_sk_needs_a_word_boundary(self):
        self.assertEqual(hello_log.redact("task-finished at mask-abcdefgh1234"), "task-finished at mask-abcdefgh1234")
        self.assertEqual(hello_log.redact("x (sk-ABCDEFGH123) and:sk-ABCDEFGH123"), "x (sk-***) and:sk-***")
        self.assertEqual(hello_log.redact("notBearer abcdefgh12345"), "notBearer abcdefgh12345")
        self.assertEqual(hello_log.redact("h: Bearer abcdefgh12345"), "h: Bearer ***")


class HelloLogLifecycleTest(unittest.TestCase):
    """Task 2: trace scopes, heartbeat, svc.start / svc.stop."""

    def tearDown(self):
        if hello_log._current is not None:
            hello_log._current.close(timeout_s=0)
            hello_log._current = None

    def keep_sigterm(self):
        prev = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, prev)

    def test_new_trace(self):
        a, b = hello_log.new_trace(), hello_log.new_trace()
        self.assertRegex(a, r"^[0-9a-f]{12}$")
        self.assertNotEqual(a, b)

    def test_scope_fills_trace_and_kind(self):
        logger, db = capture_log("notes")
        with hello_log.scope("call.notes", trace="t1", ref={"minute": 5}):
            hello_log.emit(None, sum="a")
            hello_log.emit("call.eye", trace="t2", ref={"minute": 6}, sum="b")      # explicit fields win
            hello_log.emit(None, trace=None, sum="c")                              # None = not given
        hello_log.emit(None, sum="d")
        self.assertTrue(logger.flush())
        a, b, c, d = db.flows(MONTH)
        self.assertEqual((a["kind"], a["trace"], a["ref"], a["sum"]), ("call.notes", "t1", {"minute": 5}, "a"))
        self.assertEqual((b["kind"], b["trace"], b["ref"], b["sum"]), ("call.eye", "t2", {"minute": 6}, "b"))
        self.assertEqual((c["kind"], c["trace"]), ("call.notes", "t1"))
        self.assertEqual(d["kind"], "event")
        self.assertNotIn("trace", d)
        self.assertNotIn("ref", d)

    def test_scope_nested_and_thread_isolated(self):
        logger, db = capture_log("mind")
        self.assertEqual(hello_log.current(), {})
        with hello_log.scope("call.say", trace="t1", by="asr", ref={"a": 1}):
            with hello_log.scope("call.say.speak", ref={"b": 2}):
                self.assertEqual(hello_log.current(),
                                 {"kind": "call.say.speak", "trace": "t1", "by": "asr", "ref": {"b": 2}})
                seen = []

                def other():
                    seen.append(hello_log.current())
                    hello_log.emit(None, sum="other thread")
                t = threading.Thread(target=other)
                t.start()
                t.join()
                self.assertEqual(seen, [{}])
                hello_log.emit(None, sum="inner")
            self.assertEqual(hello_log.current(), {"kind": "call.say", "trace": "t1", "by": "asr", "ref": {"a": 1}})
            hello_log.emit(None, sum="outer")
        self.assertEqual(hello_log.current(), {})
        self.assertTrue(logger.flush())
        by_sum = {f["sum"]: f for f in db.flows(MONTH)}
        self.assertEqual(by_sum["other thread"]["kind"], "event")
        self.assertNotIn("trace", by_sum["other thread"])
        inner, outer = by_sum["inner"], by_sum["outer"]
        self.assertEqual((inner["kind"], inner["trace"], inner["by"], inner["ref"]),
                         ("call.say.speak", "t1", "asr", {"b": 2}))
        self.assertEqual((outer["kind"], outer["trace"], outer["ref"]), ("call.say", "t1", {"a": 1}))

    def test_beat_counts_and_state(self):
        now = [T0]
        logger, db = capture_log("eye", clock_ms=lambda: now[0], lifecycle=True, install_signal=False)
        logger.set_beat_state(lambda: {"eye": True, "mind": False})
        hello_log.emit("call.eye", sum="1")
        hello_log.emit("call.eye", sum="2")
        hello_log.emit("heard.asr", sum="3")
        self.assertFalse(logger._beat_once())                       # not a minute yet
        now[0] += 60000
        logger._beat_once()
        self.assertTrue(logger.flush())
        beats = [f for f in db.flows(MONTH) if f["kind"] == "svc.beat"]
        self.assertEqual(len(beats), 1)
        extra = beats[0]["extra"]
        self.assertEqual(extra["counts"]["call.eye"], 2)
        self.assertEqual(extra["counts"]["heard.asr"], 1)
        self.assertEqual(extra["state"], {"eye": True, "mind": False})
        for k in ("dropped", "rejected", "spool_errors", "db_errors", "backlog_lines"):
            self.assertEqual(extra[k], 0, k)
        # counts are per minute and the beat does not count itself; a failing state fn still beats
        hello_log.emit("call.eye", sum="4")
        logger.set_beat_state(lambda: 1 / 0)
        now[0] += 60000
        logger._beat_once()
        self.assertTrue(logger.flush())
        beats = [f for f in db.flows(MONTH) if f["kind"] == "svc.beat"]
        self.assertEqual(len(beats), 2)
        self.assertEqual(beats[1]["extra"]["counts"], {"call.eye": 1})
        self.assertIn("ZeroDivisionError", beats[1]["extra"]["state"]["error"])

    def test_beat_counts_agree_with_seq_under_concurrency(self):
        import hello_log_query
        now = [T0]
        logger, db = capture_log("ear", clock_ms=lambda: now[0], lifecycle=True, install_signal=False)
        logger._beat_stop.set()                                    # beats only from this test

        def spam():
            for i in range(500):
                hello_log.emit("heard.asr", sum=str(i))
                if i % 50 == 0:
                    time.sleep(0.002)                              # let beats land in between

        threads = [threading.Thread(target=spam) for _ in range(4)]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            now[0] += 60000
            logger._beat_once()
            time.sleep(0.003)
        for t in threads:
            t.join()
        now[0] += 60000
        logger._beat_once()
        self.assertTrue(logger.flush(10))
        beats = [f for f in db.flows(MONTH) if f["kind"] == "svc.beat"]
        self.assertGreaterEqual(len(beats), 2)
        self.assertEqual(sum(b["extra"]["counts"].get("heard.asr", 0) for b in beats), 2000)
        gaps = hello_log_query.gaps(db, T0 - 1, now[0] + 1, beat_ms=10 ** 9)
        self.assertEqual([g for g in gaps if g["why"] == "seq_jump"], [])

    def test_svc_error_one_line_and_throttled(self):
        now = [T0]
        logger, db = capture_log("mind", clock_ms=lambda: now[0])
        try:
            raise KeyError("x" * 500 + "\nsecond line")
        except KeyError as e:
            exc = e
        for _ in range(5):
            hello_log.error(exc, where="mind.loop")             # same message: one record a minute
        hello_log.error(RuntimeError("other"), where="mind.loop")
        now[0] += 60000
        hello_log.error(exc, where="mind.loop")
        self.assertTrue(logger.flush())
        errs = [f for f in db.flows(MONTH) if f["kind"] == "svc.error"]
        self.assertEqual(len(errs), 3)
        e0 = errs[0]
        self.assertEqual((e0["ok"], e0["extra"]["where"]), (False, "mind.loop"))
        self.assertTrue(e0["err"].startswith("KeyError: 'xxx"))
        self.assertLessEqual(len(e0["err"]), 300)
        self.assertNotIn("\n", e0["err"])
        self.assertNotIn("Traceback", e0["err"])
        self.assertEqual(errs[1]["err"], "RuntimeError: other")
        self.assertEqual(logger._err_throttled, 4)
        hello_log._current = None
        hello_log.error(exc, where="x")                          # before init: nothing, no raise

    def test_beat_thread_is_daemon_and_stops_on_close(self):
        logger, _ = capture_log("eye", lifecycle=True, install_signal=False)
        t = logger._beat_thread
        self.assertTrue(t.daemon)
        self.assertTrue(t.is_alive())
        logger.close()
        t.join(2)
        self.assertFalse(t.is_alive())

    def test_start_record(self):
        logger, db = capture_log("mind", lifecycle=True, install_signal=False, code_version="v-abc123",
                                 args={"token": "s3cr3t", "port": 8080, "mongo": "mongodb://u:pw@h/x"})
        self.assertTrue(logger.flush())
        first = db.flows(MONTH)[0]
        self.assertEqual((first["kind"], first["seq"]), ("svc.start", 1))
        extra = first["extra"]
        self.assertEqual(extra["code_version"], "v-abc123")
        self.assertEqual(extra["pid"], os.getpid())
        self.assertEqual(extra["args"]["token"], "***")
        self.assertEqual(extra["args"]["port"], 8080)
        self.assertNotIn("pw", json.dumps(extra["args"]))

    def test_stop_on_sigterm(self):
        logger, db = capture_log("tts", lifecycle=True, install_signal=False)
        hello_log.emit("call.tts", sum="x")
        logger._on_stop("sigterm")
        logger.on_stop("exit")                                       # only the first stop is recorded
        hello_log._atexit()
        self.assertTrue(logger.flush())
        flows = db.flows(MONTH)
        self.assertEqual(flows[-1]["kind"], "svc.stop")
        self.assertEqual(flows[-1]["extra"], {"reason": "sigterm"})
        self.assertEqual([f["kind"] for f in flows].count("svc.stop"), 1)

    def test_atexit_records_exit(self):
        hello_log._atexit()                                          # no logger: nothing happens
        logger, db = capture_log("notes", lifecycle=True, install_signal=False)
        hello_log._atexit()
        self.assertTrue(logger.flush())
        self.assertEqual(db.flows(MONTH)[-1]["extra"], {"reason": "exit"})

    def test_sigterm_handler_chains(self):
        self.keep_sigterm()
        called = []
        signal.signal(signal.SIGTERM, lambda signum, frame: called.append(signum))
        logger, db = capture_log("eye", lifecycle=True)
        handler = signal.getsignal(signal.SIGTERM)
        self.assertIs(handler, hello_log._on_sigterm)
        handler(signal.SIGTERM, None)
        self.assertEqual(called, [signal.SIGTERM])
        self.assertTrue(logger.flush())
        self.assertEqual(db.flows(MONTH)[-1]["extra"], {"reason": "sigterm"})
        # a second init does not chain to itself
        logger, db = capture_log("eye", lifecycle=True)
        hello_log._on_sigterm(signal.SIGTERM, None)
        self.assertEqual(called, [signal.SIGTERM, signal.SIGTERM])

    def test_sigterm_default_exits_ignore_keeps_ignoring(self):
        self.keep_sigterm()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        logger, db = capture_log("eye", lifecycle=True)
        with self.assertRaises(SystemExit) as cm:
            hello_log._on_sigterm(signal.SIGTERM, None)
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue(logger.flush())
        self.assertEqual(db.flows(MONTH)[-1]["kind"], "svc.stop")
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        logger, db = capture_log("eye", lifecycle=True)
        self.assertIsNone(hello_log._on_sigterm(signal.SIGTERM, None))
        self.assertTrue(logger.flush())
        self.assertEqual(db.flows(MONTH)[-1]["extra"], {"reason": "sigterm"})

    def test_install_signal_off_or_off_main_thread(self):
        self.keep_sigterm()
        mine = lambda signum, frame: None                            # noqa: E731
        signal.signal(signal.SIGTERM, mine)
        capture_log("eye", lifecycle=True, install_signal=False)
        self.assertIs(signal.getsignal(signal.SIGTERM), mine)
        errors = []

        def start():
            try:
                capture_log("eye", lifecycle=True)
            except Exception as e:                                   # pragma: no cover
                errors.append(e)
        t = threading.Thread(target=start)
        t.start()
        t.join()
        self.assertEqual(errors, [])
        self.assertIs(signal.getsignal(signal.SIGTERM), mine)


    def test_real_sigterm_records_stop_and_exits_0(self):
        """A real process under SIG_DFL: SIGTERM leaves svc.stop in the spool and exits with status 0."""
        import subprocess
        spool = temp_dir()
        code = ("import os, signal, sys, time; sys.path.insert(0, sys.argv[1]); import hello_log; "
                "hello_log.init('eye', spool_dir=sys.argv[2], code_version='v1'); "
                "print('ready', flush=True); time.sleep(30)")
        common = str(Path(__file__).resolve().parents[1] / "server" / "common")
        p = subprocess.Popen([sys.executable, "-B", "-c", code, common, str(spool)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "ready")
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(15), 0)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
        kinds = [json.loads(line)["flow"]["kind"] for f in sorted(spool.glob("eye-*.jsonl"))
                 for line in f.read_text().splitlines()]
        self.assertEqual(kinds[0], "svc.start")
        self.assertEqual(kinds.count("svc.stop"), 1)

if __name__ == "__main__":
    unittest.main()
