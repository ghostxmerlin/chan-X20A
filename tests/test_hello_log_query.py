"""Read side of the run/audit log (server/common/hello_log_query.py) on log_fixtures.FakeDB."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_fixtures import T0, FakeDB          # noqa: E402
import hello_log                             # noqa: E402
import hello_log_query as q                  # noqa: E402

MIN = 60000
MONTH = "202611"
DAY_MS = 86400000


def bj(y, mo, d, h=0, mi=0, s=0, ms=0):
    from datetime import datetime, timedelta, timezone
    t = datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=timezone(timedelta(hours=8)))
    return int(t.timestamp() * 1000)


def put(db, at, *, svc="mind", run="r1", seq=1, kind="call.chat", **kw):
    rec = {"_id": f"{svc}:{run}:{seq}", "at": at, "svc": svc, "run": run, "seq": seq, "kind": kind}
    rec.update(kw)
    db["log_" + hello_log.month_of(at)].docs[rec["_id"]] = rec
    return rec


class FlowsTest(unittest.TestCase):
    def test_flows_cross_month(self):
        db = FakeDB()
        a = bj(2026, 11, 30, 23, 59, 59, 999)
        b = bj(2026, 12, 1)
        put(db, a, seq=1)
        put(db, b, seq=2)
        got = q.flows(db, a - 1000, b + 1000)
        self.assertEqual([r["at"] for r in got], [b, a])
        self.assertEqual(len(q.flows(db, a, b)), 1)          # end is exclusive

    def test_flows_filters(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="call.chat", cost={"amount": 0.1, "cur": "USD"})
        put(db, T0 + 1, seq=2, kind="gate.realtime")
        put(db, T0 + 2, seq=3, kind="call.eye", svc="eye", run="e1", cost={"amount": None, "cur": "USD"})
        put(db, T0 + 3, seq=4, kind="calltrap.x")
        r = lambda **kw: sorted(x["seq"] for x in q.flows(db, T0 - 1, T0 + 10, **kw))
        self.assertEqual(r(kind_prefix="call."), [1, 3])      # prefix is literal, anchored
        self.assertEqual(r(svc="eye"), [3])
        self.assertEqual(r(has_cost=True), [1, 3])
        self.assertEqual(r(has_cost=False), [2, 4])
        self.assertEqual(r(kinds=["gate.realtime", "call.eye"]), [2, 3])
        put(db, T0 + 4, seq=5, trace="t9")
        self.assertEqual(r(trace="t9"), [5])

    def test_limit_and_paging_equal_timestamps(self):
        db = FakeDB()
        for i in range(1, 6):
            put(db, T0 + (i // 2) * 10, seq=i)               # seq 2,3 and 4,5 share an at
        seen, before = [], None
        while True:
            page = q.flows(db, T0 - 1, T0 + 100, limit=2, before_id=before)
            if not page:
                break
            seen += [r["_id"] for r in page]
            before = page[-1]["_id"]
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        ats = [db["log_202611"].docs[i]["at"] for i in seen]
        self.assertEqual(ats, sorted(ats, reverse=True))

    def test_limit_cap(self):
        db = FakeDB()
        for i in range(1, 8):
            put(db, T0 + i, seq=i)
        with patch.object(q, "MAX_LIMIT", 3):
            self.assertEqual(len(q.flows(db, T0, T0 + 100, limit=99999)), 3)
            self.assertEqual(len(q.flows(db, T0, T0 + 100, limit=0)), 1)

    def test_unknown_before_id_is_empty(self):
        db = FakeDB()
        put(db, T0, seq=1)
        self.assertEqual(q.flows(db, T0 - 1, T0 + 100, before_id="mind:r1:99"), [])

    def test_span_over_60_months_rejected(self):
        with self.assertRaises(ValueError):
            q.flows(FakeDB(), T0 - 70 * 31 * DAY_MS, T0)

    def test_paging_across_months(self):
        db = FakeDB()
        a, b = bj(2026, 11, 30, 23, 59), bj(2026, 12, 1, 0, 1)
        put(db, a, seq=1)
        put(db, b, seq=2)
        p1 = q.flows(db, a - 1, b + 1, limit=1)
        p2 = q.flows(db, a - 1, b + 1, limit=1, before_id=p1[-1]["_id"])
        self.assertEqual([p1[0]["at"], p2[0]["at"]], [b, a])


class BodyChainTest(unittest.TestCase):
    def test_body(self):
        db = FakeDB()
        db["logbody_202611"].docs["abc"] = {"_id": "abc", "text": "hello"}
        self.assertEqual(q.body(db, T0, "abc"), "hello")
        self.assertIsNone(q.body(db, T0, "nope"))
        self.assertIsNone(q.body(db, bj(2026, 12, 5), "abc"))

    def test_chain_orders_by_time(self):
        db = FakeDB()
        put(db, T0 + 20, seq=3, trace="t1")
        put(db, T0, seq=1, trace="t1", svc="ear", run="e")
        put(db, T0 + 10, seq=2, trace="t1")
        put(db, T0 + 5, seq=4, trace="other")
        put(db, T0 + 3 * DAY_MS, seq=5, trace="t1")           # outside the +-1 day window
        got = q.chain(db, "t1", T0 + 10)
        self.assertEqual([r["at"] for r in got], [T0, T0 + 10, T0 + 20])


class LedgerTest(unittest.TestCase):
    def test_ledger_sums_by_currency(self):
        db = FakeDB()
        c = lambda amount, cur="USD": {"amount": amount, "cur": cur, "est": True, "basis": "p1"}
        put(db, T0, seq=1, model="m1", cost=c(0.01))
        put(db, T0 + 1, seq=2, model="m1", cost=c(0.02))
        put(db, T0 + 2, seq=3, model="m2", svc="tts", run="t", cost=c(0.004, "CNY"))
        put(db, T0 + 3, seq=4, model="m1", cost=c(None))
        put(db, T0 + 4, seq=5)                                 # no cost
        put(db, T0 + DAY_MS, seq=6, model="m1", cost=c(5))     # next day
        out = q.ledger(db, "2026-11-10")
        self.assertEqual(out["total"], {"USD": 0.03, "CNY": 0.004})
        self.assertEqual(out["unknown_n"], 1)
        by = {(r["svc"], r["model"], r["cur"]): r for r in out["by"]}
        self.assertEqual(by[("mind", "m1", "USD")]["amount"], 0.03)
        self.assertEqual(by[("mind", "m1", "USD")]["n"], 3)
        self.assertEqual(by[("mind", "m1", "USD")]["unknown_n"], 1)
        self.assertEqual(by[("tts", "m2", "CNY")]["amount"], 0.004)

    def test_ledger_cur_none_kept(self):
        db = FakeDB()
        put(db, T0, seq=1, model="m", cost={"amount": 0.5, "cur": None})
        out = q.ledger(db, "2026-11-10")
        self.assertEqual(out["total"], {"?": 0.5})
        self.assertEqual(out["by"][0]["cur"], "?")

    def test_ledger_empty(self):
        self.assertEqual(q.ledger(FakeDB(), "2026-11-10"), {"by": [], "total": {}, "unknown_n": 0})


def beat(db, at, seq, svc="mind", run="r1", counts=None):
    return put(db, at, svc=svc, run=run, seq=seq, kind="svc.beat", extra={"counts": counts or {}})


class GapsTest(unittest.TestCase):
    def test_gaps_no_beat_and_seq_jump(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start", extra={"code_version": "x"})
        beat(db, T0 + MIN, 2)
        beat(db, T0 + 2 * MIN, 3)
        beat(db, T0 + 5 * MIN, 4)                              # missing 3 minutes
        for i, seq in enumerate((5, 10)):                      # seq 5..10 used, only 2 records counted: 4 lost
            put(db, T0 + 6 * MIN + i * 1000, seq=seq, kind="call.chat")
        beat(db, T0 + 6 * MIN + 30000, 11, counts={"call.chat": 2})
        beat(db, T0 + 7 * MIN, 12)
        got = q.gaps(db, T0 - 1, T0 + 8 * MIN)
        self.assertEqual(len(got), 2, got)
        nb = [g for g in got if g["why"] == "no_beat"][0]
        sj = [g for g in got if g["why"] == "seq_jump"][0]
        self.assertEqual((nb["svc"], nb["run"], nb["from_ms"], nb["to_ms"]), ("mind", "r1", T0 + 2 * MIN, T0 + 5 * MIN))
        self.assertEqual((sj["from_ms"], sj["to_ms"], sj["lost"]), (T0 + 5 * MIN, T0 + 6 * MIN + 30000, 4))

    def test_gaps_reads_only_lifecycle_rows(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start")
        for i in range(2, 30):
            put(db, T0 + i * 1000, seq=i, kind="call.chat")
        beat(db, T0 + MIN, 30, counts={"svc.start": 1, "call.chat": 28})     # every seq accounted for
        seen = []
        col = db["log_" + MONTH]
        orig = col.find
        col.find = lambda flt=None, projection=None, **kw: seen.append((flt, projection, kw)) or orig(flt, projection)
        self.assertEqual(q.gaps(db, T0 - 1, T0 + 2 * MIN), [])
        self.assertEqual(len(seen), 1)
        flt, projection, kw = seen[0]
        self.assertEqual(flt["kind"], {"$in": ["svc.beat", "svc.start", "svc.stop"]})
        self.assertNotIn("body", projection)
        self.assertEqual(kw.get("max_time_ms"), q.QUERY_MS)

    def test_every_query_has_a_time_limit(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start", trace="t1", cost={"amount": 1, "cur": "CNY"})
        calls = []
        col = db["log_" + MONTH]
        of = col.find
        col.find = lambda flt=None, projection=None, **kw: calls.append(("find", kw)) or of(flt, projection)
        col.find_one = lambda flt, *a, **kw: calls.append(("find_one", kw)) or next(iter(of(flt)), None)
        q.flows(db, T0 - 1, T0 + 1, before_id="mind:r1:1")
        q.chain(db, "t1", T0)
        q.ledger(db, "2026-11-10")
        q.gaps(db, T0 - 1, T0 + 1)
        q.last_seen(db, ["mind"], T0)
        self.assertGreaterEqual(len(calls), 6)
        for name, kw in calls:
            self.assertEqual(kw.get("max_time_ms"), q.QUERY_MS, name)

    def test_killed_and_quickly_restarted_is_no_stop(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start")
        beat(db, T0 + MIN, 2)
        put(db, T0 + MIN + 20000, run="r2", seq=1, kind="svc.start")        # systemd restarted it 20 s later
        beat(db, T0 + 2 * MIN + 20000, 2, run="r2")
        got = q.gaps(db, T0 - 1, T0 + 3 * MIN)
        self.assertEqual([(g["why"], g["run"], g["from_ms"], g["to_ms"]) for g in got],
                         [("no_stop", "r1", T0 + MIN, T0 + MIN + 20000)])

    def test_gaps_ignores_intentional_stop(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start")
        beat(db, T0 + MIN, 2)
        put(db, T0 + 90000, seq=3, kind="svc.stop", extra={"reason": "sigterm"})
        put(db, T0 + 10 * MIN, run="r2", seq=1, kind="svc.start")
        beat(db, T0 + 11 * MIN, 2, run="r2")
        self.assertEqual(q.gaps(db, T0 - 1, T0 + 12 * MIN), [])

    def test_killed_run_is_a_gap(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start")
        beat(db, T0 + MIN, 2)
        put(db, T0 + 10 * MIN, run="r2", seq=1, kind="svc.start")
        got = q.gaps(db, T0 - 1, T0 + 12 * MIN)
        self.assertEqual([(g["why"], g["run"]) for g in got], [("no_stop", "r1")])

    def test_service_dead_at_end_of_range(self):
        db = FakeDB()
        put(db, T0, seq=1, kind="svc.start")
        beat(db, T0 + MIN, 2)
        got = q.gaps(db, T0 - 1, T0 + 10 * MIN)
        self.assertEqual([(g["why"], g["from_ms"], g["to_ms"]) for g in got], [("no_beat", T0 + MIN, T0 + 10 * MIN)])
        # a query into the future does not count time that has not happened
        self.assertEqual(q.gaps(db, T0 - 1, T0 + 10 * MIN, now_ms=T0 + 2 * MIN), [])
        # an intentional stop at the end is fine
        put(db, T0 + 2 * MIN, seq=3, kind="svc.stop", extra={"reason": "exit"})
        self.assertEqual(q.gaps(db, T0 - 1, T0 + 10 * MIN), [])

    def test_run_started_before_range_no_false_gap(self):
        db = FakeDB()
        put(db, T0 - 50 * MIN, seq=1, kind="svc.start")
        for i in range(2, 12):
            beat(db, T0 - 50 * MIN + (i - 1) * MIN, i)
        beat(db, T0 - 39 * MIN + 38 * MIN, 12)      # a beat just before the range
        for i in range(13, 16):
            beat(db, T0 + (i - 13) * MIN, i)
        self.assertEqual(q.gaps(db, T0, T0 + 3 * MIN), [])

    def test_range_starts_inside_gap(self):
        db = FakeDB()
        beat(db, T0 - MIN, 1)
        beat(db, T0 + 3 * MIN, 2)
        got = q.gaps(db, T0, T0 + 4 * MIN)
        self.assertEqual([(g["why"], g["from_ms"]) for g in got], [("no_beat", T0 - MIN)])

    def test_seq_jump_across_month_boundary(self):
        db = FakeDB()
        a, b = bj(2026, 11, 30, 23, 59, 30), bj(2026, 12, 1, 0, 0, 10)
        beat(db, a, 7)
        beat(db, b, 9)
        got = q.gaps(db, a - 1000, b + 1000)
        self.assertEqual([(g["why"], g["from_ms"], g["to_ms"]) for g in got], [("seq_jump", a, b)])

    def test_gaps_per_service(self):
        db = FakeDB()
        beat(db, T0, 1)
        beat(db, T0 + MIN, 2)
        beat(db, T0 + 30000, 1, svc="eye", run="e")
        beat(db, T0 + 90000, 2, svc="eye", run="e")
        self.assertEqual(q.gaps(db, T0 - 1, T0 + 3 * MIN), [])


class LastSeenTest(unittest.TestCase):
    """last_seen: each service's newest record for the 日志 page, cheap: any kind only in the last 5 minutes, else the
    newest svc.beat / start / stop (the (kind, at) index), newest month first, at most 2 months back."""

    def count_finds(self, db):
        seen = []
        for name in list(db.collections):
            col = db.collections[name]
            orig = col.find
            col.find = lambda flt=None, projection=None, _o=orig, _n=name, **kw: (seen.append((_n, flt))
                                                                                    or _o(flt, projection))
        return seen

    def test_newest_row_per_service_across_a_month_boundary(self):
        db = FakeDB()
        now = bj(2026, 12, 1, 0, 10)
        put(db, now - 60000, svc="ear", seq=9, kind="heard.asr")                     # fresh, any kind
        put(db, now - 90000, svc="ear", seq=8, kind="svc.beat")
        put(db, bj(2026, 11, 30, 23, 58), svc="tts", seq=4, kind="svc.beat", run="r7")   # last month, 12 min ago
        put(db, bj(2026, 11, 30, 23, 50), svc="tts", seq=3, kind="svc.beat", run="r7")
        put(db, bj(2026, 11, 30, 23, 59), svc="tts", seq=5, kind="call.tts", run="r7")  # older than 5 min: not looked at
        put(db, bj(2026, 10, 2), svc="eye", seq=1, kind="svc.stop")                  # 2 months back: still found
        put(db, bj(2026, 9, 2), svc="notes", seq=1, kind="svc.beat")                 # 3 months back: too old
        put(db, now + 60000, svc="mind", seq=1, kind="svc.beat")                     # after now: ignored
        out = q.last_seen(db, ["ear", "mind", "eye", "notes", "memory", "tts"], now)
        self.assertEqual(out["ear"], {"at": now - 60000, "kind": "heard.asr", "run": "r1"})
        self.assertEqual(out["tts"], {"at": bj(2026, 11, 30, 23, 58), "kind": "svc.beat", "run": "r7"})
        self.assertEqual(out["eye"]["kind"], "svc.stop")
        self.assertIsNone(out["notes"])
        self.assertIsNone(out["mind"])
        self.assertIsNone(out["memory"])

    def test_stops_at_the_first_month_with_a_hit_and_skips_missing_tables(self):
        db = FakeDB()
        now = bj(2026, 12, 1, 0, 10)
        put(db, bj(2026, 11, 30, 23, 0), svc="tts", seq=1, kind="svc.beat")
        put(db, bj(2026, 10, 30, 23, 0), svc="tts", seq=0, kind="svc.beat")
        seen = self.count_finds(db)
        self.assertEqual(q.last_seen(db, ["tts"], now)["tts"]["at"], bj(2026, 11, 30, 23, 0))
        self.assertNotIn("log_202610", [n for n, _ in seen])                          # stopped at November
        self.assertTrue(all(n.startswith("log_2026") for n, _ in seen))
        self.assertTrue(all(f.get("svc") == "tts" for _, f in seen))


if __name__ == "__main__":
    unittest.main()
