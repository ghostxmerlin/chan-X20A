"""日志 page (run/audit log, 2026-11-16): mind's read-only GET /log, /log/body, /log/chain, /log/ledger, /log/gaps,
/log/last over hello_log_query (server/common); format in docs/hello-log.md."""
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mind_fixtures  # noqa: E402,F401 - puts server/ and server/common on sys.path
from log_fixtures import FakeDB  # noqa: E402 - find / sort / limit / $or / $regex / list_collection_names
from mind import api  # noqa: E402
from mind.store import Store  # noqa: E402

T0 = 1794300000000          # 2026-11-10 16:40:00 +08:00
MIN = 60_000
SHA = "a" * 64


def rec(svc, seq, at, kind, run="r1", **fields):
    return {"_id": f"{svc}:{run}:{seq}", "svc": svc, "run": run, "seq": seq, "at": at, "kind": kind, **fields}


class LogApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.server = api.make_server(Store(self.db), "k", port=0, ctx={})
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, token="k"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}" + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def put(self, *rows, month="202611"):
        self.db["log_" + month].insert_many([dict(r) for r in rows])

    def test_log_requires_token(self):
        for p in ("/log", "/log/body?at=1&id=" + SHA, "/log/chain?trace=t1&at=1", "/log/ledger?day=2026-11-10",
                  "/log/gaps", "/log/last"):
            self.assertEqual(self.call(p, token=None)[0], 401, p)
            self.assertEqual(self.call(p, token="wrong")[0], 401, p)

    def test_log_filters_pass_through(self):
        self.put(rec("ear", 1, T0, "svc.start"),
                 rec("ear", 2, T0 + 1000, "call.realtime.turn", cost={"amount": 0.01, "cur": "CNY"},
                     body={"input": SHA}, trace="t1"),
                 rec("ear", 3, T0 + 2000, "gate.realtime", trace="t1"),
                 rec("mind", 1, T0 + 3000, "call.chat", cost={"amount": None, "cur": "USD"}, trace="t1"),
                 rec("ear", 4, T0 + 4000, "call.tingwu"),
                 rec("ear", 5, T0 + 5000, "call.realtime.turn", cost={"amount": 0.02, "cur": "CNY"}))
        q = f"/log?from={T0}&to={T0 + MIN}"
        st, d = self.call(q + "&kind=call.")                                         # a trailing dot = prefix
        self.assertEqual(st, 200)
        self.assertEqual([r["_id"] for r in d["rows"]], ["ear:r1:5", "ear:r1:4", "mind:r1:1", "ear:r1:2"])
        self.assertEqual(d["rows"][-1]["body"], {"input": SHA})                      # bodies by id only
        self.assertFalse(d["more"])
        st, d = self.call(q + "&kind=call.&svc=ear&cost=1&limit=1")
        self.assertEqual(([r["_id"] for r in d["rows"]], d["more"]), (["ear:r1:5"], True))
        st, d = self.call(q + "&kind=call.&svc=ear&cost=1&limit=1&before=ear:r1:5")     # next page
        self.assertEqual([r["_id"] for r in d["rows"]], ["ear:r1:2"])
        st, d = self.call(q + "&cost=0&svc=ear")
        self.assertEqual([r["_id"] for r in d["rows"]], ["ear:r1:4", "ear:r1:3", "ear:r1:1"])
        st, d = self.call(q + "&kind=call.realtime")                                 # no dot: that kind exactly
        self.assertEqual(d["rows"], [])
        st, d = self.call(q + "&kind=call.realtime.turn&trace=t1")
        self.assertEqual([r["_id"] for r in d["rows"]], ["ear:r1:2"])
        for bad in ("&svc=other", "&kind=Call;", "&cost=2", "&limit=x", "&trace=a%20b", "&before=nope"):
            self.assertEqual(self.call(q + bad)[0], 400, bad)
        self.assertEqual(self.call(f"/log?from={T0}&to={T0}")[0], 400)
        self.assertEqual(self.call(f"/log?from={T0}&to={T0 + 32 * 86400_000}")[0], 400)   # at most 31 days
        st, d = self.call("/log")                                                    # default: the last hour
        self.assertEqual(st, 200)
        self.assertLessEqual(abs(d["to"] - int(time.time() * 1000)), 5000)
        self.assertEqual(d["to"] - d["from"], 3600_000)

    def test_log_body(self):
        self.db["logbody_202611"].insert_many([{"_id": SHA, "text": "主人：你好\nhello：你好呀"}])
        st, d = self.call(f"/log/body?at={T0}&id={SHA}")
        self.assertEqual((st, d), (200, {"text": "主人：你好\nhello：你好呀"}))
        self.assertEqual(self.call(f"/log/body?at={T0}&id={'b' * 64}")[0], 404)
        self.assertEqual(self.call(f"/log/body?at={T0 + 30 * 86400_000}&id={SHA}")[0], 404)   # another month's table
        self.assertEqual(self.call(f"/log/body?at={T0}&id=../x")[0], 400)
        self.assertEqual(self.call(f"/log/body?id={SHA}")[0], 400)
        self.assertEqual(self.call(f"/log/body?at=99999999999999999&id={SHA}")[0], 400)     # a time out of range
        self.assertEqual(self.call(f"/log/body?at={T0}&id={SHA}%0A")[0], 400)

    def test_log_chain(self):
        self.put(rec("ear", 2, T0 + 2000, "call.realtime.turn", trace="t1"),
                 rec("ear", 1, T0, "heard.asr", trace="t1"),
                 rec("mind", 1, T0 + 1000, "call.chat", trace="t1"),
                 rec("ear", 3, T0 + 3000, "call.tingwu", trace="t2"))
        st, d = self.call(f"/log/chain?trace=t1&at={T0}")
        self.assertEqual(st, 200)
        self.assertEqual([r["_id"] for r in d["rows"]], ["ear:r1:1", "mind:r1:1", "ear:r1:2"])   # oldest first
        self.assertEqual(self.call(f"/log/chain?at={T0}")[0], 400)
        self.assertEqual(self.call(f"/log/chain?trace=t1")[0], 400)

    def test_log_ledger_day(self):
        self.put(rec("ear", 1, T0, "call.realtime.turn", model="qwen", cost={"amount": 0.01, "cur": "CNY"}),
                 rec("ear", 2, T0 + 1000, "call.realtime.turn", model="qwen", cost={"amount": None, "cur": "CNY"}),
                 rec("mind", 1, T0 + 2000, "call.chat", model="m", cost={"amount": 0.5, "cur": "USD"}),
                 rec("mind", 2, T0 + 86400_000, "call.chat", model="m", cost={"amount": 9.0, "cur": "USD"}))
        st, d = self.call("/log/ledger?day=2026-11-10")
        self.assertEqual(st, 200)
        self.assertEqual((d["day"], d["total"], d["unknown_n"]), ("2026-11-10", {"CNY": 0.01, "USD": 0.5}, 1))
        self.assertEqual([(b["svc"], b["n"], b["unknown_n"]) for b in d["by"]], [("ear", 2, 1), ("mind", 1, 0)])
        st, d = self.call("/log/ledger")                                             # default: today (Beijing)
        self.assertEqual(st, 200)
        self.assertRegex(d["day"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(self.call("/log/ledger?day=2026-13-40")[0], 400)

    def test_log_gaps(self):
        self.put(rec("ear", 1, T0, "svc.start"), rec("ear", 2, T0 + MIN, "svc.beat"),
                 rec("ear", 3, T0 + 6 * MIN, "svc.beat"), rec("ear", 5, T0 + 7 * MIN, "svc.beat"))
        st, d = self.call(f"/log/gaps?from={T0}&to={T0 + 8 * MIN}")
        self.assertEqual(st, 200)
        self.assertEqual([(g["svc"], g["why"], g["from_ms"], g["to_ms"]) for g in d["gaps"]],
                         [("ear", "no_beat", T0 + MIN, T0 + 6 * MIN), ("ear", "seq_jump", T0 + 6 * MIN, T0 + 7 * MIN)])
        self.assertEqual((d["from"], d["to"]), (T0, T0 + 8 * MIN))
        self.assertEqual(self.call(f"/log/gaps?from={T0}&to={T0 - 1}")[0], 400)

    def test_log_last_per_service(self):
        now = int(time.time() * 1000)
        mo = time.strftime("%Y%m", time.gmtime(now / 1000 + 8 * 3600))
        old = now - 3 * 86400_000
        old_mo = time.strftime("%Y%m", time.gmtime(old / 1000 + 8 * 3600))
        self.put(rec("ear", 7, now - 5000, "heard.asr"), rec("ear", 6, now - 9000, "svc.beat"), month=mo)
        self.put(rec("tts", 3, old, "svc.beat", run="r9"), month=old_mo)              # silent for 3 days
        st, d = self.call("/log/last")
        self.assertEqual(st, 200)
        self.assertEqual(sorted(d["svcs"]), ["ear", "eye", "memory", "mind", "notes", "tts"])
        self.assertEqual((d["svcs"]["ear"]["at"], d["svcs"]["ear"]["kind"]), (now - 5000, "heard.asr"))
        self.assertEqual((d["svcs"]["tts"]["at"], d["svcs"]["tts"]["kind"], d["svcs"]["tts"]["run"]), (old, "svc.beat", "r9"))
        self.assertIsNone(d["svcs"]["mind"])
        self.assertLessEqual(abs(d["now"] - now), 5000)

    def test_log_last_is_cached_for_a_minute(self):
        now = int(time.time() * 1000)
        mo = time.strftime("%Y%m", time.gmtime(now / 1000 + 8 * 3600))
        self.put(rec("ear", 1, now - 5000, "svc.beat"), month=mo)
        col = self.db["log_" + mo]
        finds, orig = [], col.find
        col.find = lambda flt=None, projection=None: finds.append(flt) or orig(flt, projection)
        first = self.call("/log/last")[1]
        n = len(finds)
        self.assertGreater(n, 0)
        self.put(rec("ear", 2, now - 1000, "heard.asr"), month=mo)
        second = self.call("/log/last")[1]
        self.assertEqual(len(finds), n)                                              # no DB read within 60 s
        self.assertEqual(second, first)                                              # its own "now": ages as of then
        saved = api.LOG_LAST_CACHE_MS
        api.LOG_LAST_CACHE_MS = 0
        self.addCleanup(setattr, api, "LOG_LAST_CACHE_MS", saved)
        third = self.call("/log/last")[1]
        self.assertGreater(len(finds), n)
        self.assertEqual(third["svcs"]["ear"]["kind"], "heard.asr")

    def test_log_query_missing_is_503(self):
        saved = api.log_query
        api.log_query = None
        self.addCleanup(setattr, api, "log_query", saved)
        st, d = self.call("/log")
        self.assertEqual(st, 503)
        self.assertIn("hello_log_query", d["error"])


if __name__ == "__main__":
    unittest.main()
