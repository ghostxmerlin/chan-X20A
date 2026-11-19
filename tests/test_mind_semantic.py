"""按意思找 (server/mind/semantic.py, tool semantic_search): offline, with a deterministic fake embedder; no network.

    .private/venv-ear/bin/python -B -m unittest tests.test_mind_semantic -v
"""
import hashlib
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, budget_pause, capture_log, stop_log
from mind import semantic, tool_impls
from mind.library import Library
from mind.store import Store
from mind.tools import Paid, ToolError, Tools, load_registry

SH = ZoneInfo("Asia/Shanghai")
NOW = int(datetime(2026, 11, 18, 19, 30, tzinfo=SH).timestamp() * 1000)


def at(*a):
    return int(datetime(*a, tzinfo=SH).timestamp() * 1000)


def fake_vec(text, d=semantic.DIM):
    """Bag of characters and two-character words hashed into d buckets: texts sharing words point the same way."""
    v = [0.0] * d
    t = "".join(ch for ch in text if ch.isalnum())
    for w in list(t) + [t[i:i + 2] for i in range(len(t) - 1)]:
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % d] += 1.0 if (h >> 64) & 1 else 0.8
    return v


class FakeEmbed:
    def __init__(self):
        self.calls = []

    def __call__(self, texts, purpose="sync"):
        self.calls.append((list(texts), purpose))
        tokens = sum(len(t) for t in texts)
        return [fake_vec(t) for t in texts], {"tokens": tokens, "cny": semantic.cost_cny(tokens), "est": False}

    def texts(self):
        return [t for batch, _ in self.calls for t in batch]


def seed(db):
    lib = Library(db)
    put = lambda c, f: lib.put(c, f, item_id=None, expected_version=None, scope_id="office", now_ms=NOW - 86400_000)
    wang = put("people_cards", {"kind": "regular", "display": "小王"})
    put("knowledge_items", {"kind": "place", "name": "园区咖啡店", "aliases": ["楼下咖啡"], "statement": "楼下的咖啡店，美式最好喝",
                            "subjects": ["owner"]})
    mem = put("memory_items", {"kind": "event", "title": "一起买咖啡", "statement": "主人和小王下午去买了美式咖啡",
                               "subjects": ["owner", wang["person_id"]], "when": {"type": "point", "start_ms": NOW - 7200_000}})
    db["conversations"].insert_one({"cid": "c1", "start_ms": at(2026, 11, 17, 14), "end_ms": at(2026, 11, 17, 14, 20),
                                    "title": "板子识别不了", "notes": "主人说 M12 板子识别不了人脸，要重新刷固件"})
    db["conversations"].insert_one({"cid": "c2", "start_ms": at(2026, 11, 18, 12), "end_ms": at(2026, 11, 18, 12, 10),
                                    "title": "午饭", "notes": "商量中午吃面条还是米饭"})
    db["conversations"].insert_one({"cid": "c3", "start_ms": at(2026, 11, 18, 13), "end_ms": None, "title": "", "notes": ""})
    seg = lambda start, v, title, text: db["mind_segments"].insert_one(
        {"lane": "live", "start": start, "end": start + 1800_000, "version": v, "summary": {"title": title, "text": text}})
    seg(at(2026, 11, 18, 9), 1, "主人在写代码", "主人在敲键盘")
    seg(at(2026, 11, 18, 9), 2, "主人在写代码调板子", "主人一边敲键盘一边调 M12 板子")     # the newest version counts
    seg(at(2026, 11, 18, 15), 1, "没人在", "办公室安静")
    db["mind_segments"].insert_one({"lane": "replay", "start": 1, "end": 2, "version": 1, "summary": {"title": "回放", "text": "x"}})
    return mem


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.mem = seed(self.db)
        self.embed = FakeEmbed()

    def test_backfill_every_kind_once(self):
        r = semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        self.assertEqual({k: v["embedded"] for k, v in r["kinds"].items()},
                         {"memory": 1, "knowledge": 1, "notes": 2, "segment": 2, "doc": 0})   # c3 has no text, replay lane not; no material
        self.assertEqual((r["embedded"], r["left"], r["stopped"]), (6, 0, None))
        self.assertAlmostEqual(r["spent_cny"], semantic.cost_cny(sum(len(t) for t in self.embed.texts())), places=9)
        v = self.db["vectors"].find_one({"_id": "segment:" + str(at(2026, 11, 18, 9))})
        self.assertEqual((v["kind"], v["model"], v["dim"], len(v["vec"])), ("segment", "text-embedding-v4", 512, 512))
        self.assertIn("调 M12 板子", v["text"])
        self.assertAlmostEqual(sum(x * x for x in v["vec"]), 1.0, places=6)            # stored unit length
        k = self.db["vectors"].find_one({"kind": "knowledge"})
        self.assertIn("园区咖啡店：也叫楼下咖啡：楼下的咖啡店，美式最好喝", self.embed.texts())
        self.assertLessEqual(len(k["text"]), semantic.PREVIEW)
        self.assertTrue(all(p == "sync" for _, p in self.embed.calls))

    def test_hash_skip_change_and_removal(self):
        semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        n = len(self.embed.calls)
        again = semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        self.assertEqual((again["embedded"], again["removed"], len(self.embed.calls)), (0, 0, n))   # nothing paid
        self.assertEqual(again["kinds"]["notes"]["fresh"], 2)
        self.db["conversations"].update_one({"cid": "c2"}, {"$set": {"notes": "最后决定吃面条"}})
        self.db["memory_recall"].update_one({"memory_id": self.mem["memory_id"]}, {"$set": {"recallable": False}})
        r = semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        self.assertEqual((r["embedded"], r["removed"]), (1, 1))
        self.assertEqual(self.embed.calls[-1][0], ["午饭：最后决定吃面条"])
        self.assertIsNone(self.db["vectors"].find_one({"kind": "memory"}))
        with mock.patch.dict("os.environ", {"MIND_SEMANTIC_DIM": "256"}):              # another dimension: all again
            self.assertEqual(semantic.plan(self.db)[2]["notes"]["todo"], 2)

    def test_budget_stop_and_resume(self):
        texts = [t["text"] for t in semantic.plan(self.db)[0]]
        one = semantic.cost_cny(len(texts[0]))
        r = semantic.sync(self.db, embed_fn=self.embed, budget_cny=one, chunk=1, now_ms=NOW)
        self.assertEqual((r["stopped"], r["embedded"], r["left"]), ("budget", 1, 5))   # the 2nd would go over
        self.assertLessEqual(r["spent_cny"], one)
        self.assertEqual(self.embed.texts(), texts[:1])                                 # newest first
        r = semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        self.assertEqual((r["stopped"], r["embedded"], r["left"]), (None, 5, 0))

    def test_dry_run_and_daily_cap(self):
        r = semantic.sync(self.db, dry_run=True)
        self.assertEqual((r["embedded"], r["left"], self.db.count("vectors")), (0, 6, 0))
        self.assertGreater(r["est_cny"], 0)
        budget_pause(self.db)
        r = semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        self.assertEqual((r["stopped"], r["embedded"], self.embed.calls), ("daily_cap", 0, []))

    def test_embed_error_stops(self):
        def broken(texts, purpose):
            raise semantic.EmbedError("HTTP 500: x", {"tokens": 3, "cny": 0.000001})
        r = semantic.sync(self.db, embed_fn=broken, now_ms=NOW)
        self.assertTrue(r["stopped"].startswith("error: HTTP 500"))
        self.assertEqual((r["embedded"], r["tokens"]), (0, 3))
        self.assertTrue(semantic.sync(self.db, embed_fn=None, now_ms=NOW)["stopped"].startswith("error: no embedder"))

    def test_periodic_skips_while_paused(self):
        paused = iter([True, False])
        sleeps = []
        semantic.periodic(self.db, self.embed, paused_fn=lambda: next(paused), sleep=sleeps.append, rounds=2)
        self.assertEqual((sleeps, self.db.count("vectors")), ([semantic.SYNC_EVERY_S], 6))

    def test_sync_flag_defaults_on(self):              # owner approved RAG 2026-11-18
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(semantic.sync_enabled())
        with mock.patch.dict("os.environ", {"MIND_SEMANTIC_SYNC": "0"}):
            self.assertFalse(semantic.sync_enabled())


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        seed(self.db)
        self.embed = FakeEmbed()
        semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)

    def test_cosine_ranking(self):
        hits, info = semantic.search(self.db, "板子识别不了", k=3, embed_fn=self.embed)
        self.assertEqual(hits[0]["_id"], "notes:c1")
        self.assertEqual([h["score"] for h in hits], sorted((h["score"] for h in hits), reverse=True))
        self.assertEqual((info["scanned"], self.embed.calls[-1][1]), (6, "search"))
        self.assertNotIn("vec", hits[0])
        coffee, _ = semantic.search(self.db, "买美式咖啡", k=1, embed_fn=self.embed)
        self.assertIn(coffee[0]["kind"], ("memory", "knowledge"))

    def test_kinds_filter(self):
        hits, info = semantic.search(self.db, "板子", kinds=["segment"], k=5, embed_fn=self.embed)
        self.assertEqual({h["kind"] for h in hits}, {"segment"})
        self.assertEqual(info["scanned"], 2)
        with self.assertRaises(ValueError):
            semantic.search(self.db, "x", kinds=["speech"], embed_fn=self.embed)

    def test_vectors_of_another_model_are_ignored(self):
        with mock.patch.dict("os.environ", {"MIND_SEMANTIC_MODEL": "qwen3.7-text-embedding-flash"}):
            hits, info = semantic.search(self.db, "板子", embed_fn=self.embed)
        self.assertEqual((hits, info["scanned"]), ([], 0))


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        seed(self.db)
        self.embed = FakeEmbed()
        semantic.sync(self.db, embed_fn=self.embed, now_ms=NOW)
        self.t = tool_impls.mind_tools(switches=lambda: {}, store=Store(self.db), clock_ms=lambda: NOW, embed=self.embed)

    def test_hits_and_how_to_open(self):
        out = self.t["semantic_search"]({"query": "上次说的那个板子问题", "limit": 3})
        self.assertIsInstance(out, dict)          # not Paid (2026-11-19): the call.embed row carries the cost
        d = out
        self.assertEqual(len(d["hits"]), 3)
        top = d["hits"][0]
        self.assertEqual((top["kind"], top["kind_title"], top["id"], top["title"], top["when"]),
                         ("notes", "纪要", "c1", "板子识别不了", "11-17 14:00"))
        self.assertEqual(top["open"], {"tool": "notes_search", "args": {"from": "2026-11-17 14:00", "to": "2026-11-17 14:21"}})
        seg = self.t["semantic_search"]({"query": "调板子", "kinds": ["segment"], "limit": 1})["hits"][0]
        self.assertEqual((seg["title"], seg["when"], seg["until"], seg["open"]["tool"]),
                         ("主人在写代码调板子", "11-18 09:00", "09:30", "review_period"))
        mem = self.t["semantic_search"]({"query": "买美式咖啡", "kinds": ["memory"]})["hits"][0]
        self.assertEqual((mem["people"], mem["only_for_owner"], mem["open"]["tool"]), (["owner", "小王"], False, "memory_search"))
        know = self.t["semantic_search"]({"query": "咖啡店", "kinds": ["knowledge"]})["hits"][0]
        self.assertEqual((know["title"], know["aliases"], know["open"]["tool"]), ("园区咖啡店", ["楼下咖啡"], "knowledge_search"))

    def test_memory_off_means_no_memory_hits(self):
        """Security review 2026-11-18: the same gate as memory_search (recall.load_pool, memory.apply)."""
        from mind import settings as settings_mod
        s = settings_mod.load(None)
        s["memory"] = {**(s.get("memory") or {}), "apply": False}
        t = tool_impls.mind_tools(switches=lambda: {}, store=Store(self.db), clock_ms=lambda: NOW, embed=self.embed,
                                  settings_fn=lambda: s)
        self.assertEqual(t["semantic_search"]({"query": "买美式咖啡", "kinds": ["memory"]})["hits"], [])

    def test_gone_source_is_dropped(self):
        self.db["conversations"].update_one({"cid": "c1"}, {"$set": {"title": "", "notes": ""}})   # before the next sync
        d = self.t["semantic_search"]({"query": "板子识别不了", "kinds": ["notes"]})
        self.assertNotIn("c1", [h["id"] for h in d["hits"]])

    def test_not_available_budget_and_empty(self):
        t = tool_impls.mind_tools(switches=lambda: {}, store=Store(self.db), clock_ms=lambda: NOW)
        with self.assertRaises(ToolError) as e:
            t["semantic_search"]({"query": "x"})
        self.assertEqual(e.exception.code, "not_available")
        empty = tool_impls.mind_tools(switches=lambda: {}, store=Store(FakeDB()), clock_ms=lambda: NOW, embed=self.embed)
        self.assertIn("note", empty["semantic_search"]({"query": "x"}))
        budget_pause(self.db)
        with self.assertRaises(ToolError) as e:
            self.t["semantic_search"]({"query": "x"})
        self.assertEqual(e.exception.code, "budget")

    def test_through_the_framework(self):
        rows = []
        tools = Tools(load_registry(), self.t, log=lambda kind, **f: rows.append((kind, f)))
        env = tools.call("semantic_search", {"query": "板子", "kinds": ["notes"], "limit": 2})
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["kinds"], ["纪要"])
        self.assertIsNone(env["cost"])                     # the call.embed row carries it (no double count)
        self.assertEqual(rows[-1][0], "call.tool.semantic_search")
        bad = tools.call("semantic_search", {"query": "x", "kinds": ["person_note"]})
        self.assertEqual(bad["error"]["code"], "bad_args")
        spec = next(t for t in load_registry()["tools"] if t["name"] == "semantic_search")
        self.assertEqual(spec["status"], "live")                                            # deployed + backfilled 2026-11-18
        self.assertTrue(tools.call("semantic_search", spec["example"])["ok"])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class EmbedCallTest(unittest.TestCase):
    """semantic.embed itself, with urllib's urlopen replaced (no network, no money)."""
    def setUp(self):
        self.flows = capture_log()
        self.addCleanup(stop_log)
        self.sent = []

    def urlopen(self, req, timeout=None):
        body = json.loads(req.data)
        self.sent.append((req, body))
        data = [{"index": i, "embedding": [1.0] + [0.0] * (body["dimensions"] - 1), "object": "embedding"}
                for i in reversed(range(len(body["input"])))]
        return FakeResponse(json.dumps({"data": data, "usage": {"total_tokens": 7 * len(body["input"])}}).encode())

    def test_batches_usage_cost_and_log(self):
        with mock.patch("urllib.request.urlopen", self.urlopen):
            vecs, info = semantic.embed([f"第{i}句" for i in range(23)], key="sk-test-secret-123", purpose="sync")
        self.assertEqual([len(b["input"]) for _, b in self.sent], [10, 10, 3])          # text-embedding-v4: 10 a request
        req, body = self.sent[0]
        self.assertEqual((body["model"], body["dimensions"], body["encoding_format"]), ("text-embedding-v4", 512, "float"))
        self.assertEqual(req.full_url, semantic.URL)
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-test-secret-123")
        self.assertEqual((len(vecs), info["tokens"], info["est"]), (23, 161, False))
        self.assertAlmostEqual(info["cny"], 161 * 0.5 / 1e6)
        row = self.flows("call.embed")[-1]
        self.assertEqual((row["ok"], row["model"], row["key"], row["cost"]["cur"]), (True, "text-embedding-v4", "sk-test", "CNY"))
        self.assertNotIn("sk-test-secret-123", json.dumps(self.flows(), ensure_ascii=False))

    def test_errors(self):
        with self.assertRaises(semantic.EmbedError):
            with mock.patch.dict("os.environ", {}, clear=True):
                semantic.embed(["x"])                                                       # no key
        import urllib.error

        def down(req, timeout=None):
            raise urllib.error.URLError("refused")
        with mock.patch("urllib.request.urlopen", down), self.assertRaises(semantic.EmbedError):
            semantic.embed(["x"], key="sk-test-secret-123")
        self.assertFalse(self.flows("call.embed")[-1]["ok"])
        self.assertIsNone(semantic.embedder(None))


if __name__ == "__main__":
    unittest.main()
