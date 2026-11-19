"""深入研究 (server/mind/research.py, 2026-11-18): rounds, the per-task and daily caps, the report hook, the tools.
Offline: a fake Responses API (send) and fake search backends; no network, no key.

    .private/venv-ear/bin/python -B -m unittest tests.test_mind_research -v
"""
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, budget_pause, capture_log, stop_log
from mind import research, tool_impls
from mind.store import Store
from mind.tools import ToolError, Tools, load_registry

REG = load_registry()
T0 = 1795000000000


def message(text, urls=()):
    return {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": [
        {"type": "url_citation", "url": u, "title": f"标题{u[-1]}"} for u in urls]}]}


class FakeApi:
    """The Responses API: web_search calls answer with findings + citations, JSON steps by their format name."""
    def __init__(self, search_usage=None, enough_after=1, final=None):
        self.bodies = []
        self.search_usage = search_usage or {"input_tokens": 8000, "output_tokens": 1500,
                                             "input_tokens_details": {"cached_tokens": 0}}
        self.reviews = 0
        self.enough_after = enough_after
        self.final = final or {"spoken": "第一句。第二句！第三句？第四句。", "report": "结论：……", "sources": [1, 2, 99]}
        self.n = 0

    def __call__(self, body):
        self.bodies.append(body)
        if body.get("tools"):
            self.n += 1
            q = body["input"].split("这次要查：")[1].split("\n")[0]
            out = [{"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": q}},
                   message(f"关于{q}的要点", [f"https://ex.com/{self.n}", "https://ex.com/shared"])]
            return {"output": out, "usage": dict(self.search_usage)}, 1000, None
        name = body["text"]["format"]["name"]
        usage = {"input_tokens": 500, "output_tokens": 300}
        if name == "plan":
            data = {"questions": ["小问题一", "小问题二", "小问题三", "多出来的"]}
        elif name == "review":
            self.reviews += 1
            data = {"enough": self.reviews > self.enough_after, "questions": ["小问题四", "小问题一"]}
        else:
            data, usage = self.final, {"input_tokens": 3000, "output_tokens": 2000}
        return {"output": [message(json.dumps(data, ensure_ascii=False))], "usage": usage}, 800, None


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.now = [T0]
        self.reports = []
        self.settings = {"research": {"cap_cny_per_task": 2.0}}

    def tick(self):
        self.now[0] += 1000
        return self.now[0]

    def runner(self, api=None, search=None, report=None, **kw):
        self.api = api or FakeApi()
        ids = iter(f"r{i}" for i in range(1, 100))
        return research.Runner(self.db, {"base_url": "https://x.invalid/v1", "api_key": "sk-test"},
                               settings_fn=lambda: self.settings, send=self.api, search=search,
                               report=report or self.reports.append, clock_ms=self.tick, new_id=lambda: next(ids), **kw)


class PriceTest(unittest.TestCase):
    def test_sol_price_from_usage(self):
        u = {"input_tokens": 10000, "output_tokens": 2000, "input_tokens_details": {"cached_tokens": 4000}}
        # 6000 × $2 + 4000 × $0.1 + 2000 × $10 per million, + 3 searches × $0.01
        self.assertAlmostEqual(research.cost_usd("gpt-6.2-sol", u, 3), 0.012 + 0.0004 + 0.02 + 0.03)
        self.assertAlmostEqual(research.cost_usd("gpt-6.2-sol", {"prompt_tokens": 200_000}), 0.4)
        self.assertAlmostEqual(research.cost_usd("gpt-6.2-sol", {"input_tokens": 300_000}), 1.2)     # long tier $4
        self.assertIsNone(research.cost_usd("no-such-model", u))

    def test_parse_output(self):
        resp = {"output": [{"type": "reasoning"}, {"type": "web_search_call"}, {"type": "web_search_call"},
                           message("答", ["https://a.cn/1", "https://a.cn/1", "https://a.cn/2"])]}
        text, sources, n = research.parse_output(resp)
        self.assertEqual((text, n, [s["url"] for s in sources]), ("答", 2, ["https://a.cn/1", "https://a.cn/2"]))

    def test_three_sentences(self):
        self.assertEqual(research.three_sentences("一。二！三？四。"), "一。二！三？")


class JobTest(Base):
    def tearDown(self):
        stop_log()

    def test_normal_depth_rounds_answer_and_report(self):
        flows = capture_log()
        r = self.runner()
        job = r.submit("桌面机器人怎么判断是不是在跟它说话", "normal", asked_by="owner")
        self.assertEqual((job["_id"], job["state"], job["ahead"], job["cap_cny"], job["model"]),
                         ("r1", "queued", 0, 2.0, "gpt-6.2-sol"))
        d = r.run_one("r1")
        kinds = [s["kind"] for s in d["steps"]]
        # plan (3 questions kept) -> round 1: 3 searches -> review (1 new, the repeat dropped) -> round 2 -> final
        self.assertEqual(kinds, ["plan", "search", "search", "search", "review", "search", "final"])
        self.assertEqual([s.get("q") for s in d["steps"] if s["kind"] == "search"], ["小问题一", "小问题二", "小问题三", "小问题四"])
        self.assertEqual(d["state"], "done")
        self.assertIsNone(d["stopped"])
        self.assertEqual(d["answer"]["spoken"], "第一句。第二句！第三句？")
        self.assertEqual([s["url"] for s in d["sources"]], ["https://ex.com/1", "https://ex.com/shared"])
        # 4 searches ¥0.2952 each + plan/review ¥0.0288 each + final (3000 in, 2000 out) ¥0.1872, at 7.2
        self.assertAlmostEqual(d["cost_cny"], round(4 * 0.2952 + 2 * 0.0288 + 0.1872, 4), places=3)
        self.assertLessEqual(d["cost_cny"], 2.0)
        self.assertEqual(len(self.reports), 1)
        self.assertEqual(self.reports[0]["_id"], "r1")
        self.assertTrue(d["reported"])
        searches = [b for b in self.api.bodies if b.get("tools")]
        self.assertEqual((searches[0]["model"], searches[0]["tools"]), ("gpt-6.2-sol", [{"type": "web_search"}]))
        rows = flows("call.research.search")
        self.assertEqual(len(rows), 4)
        self.assertEqual((rows[0]["cost"]["cur"], rows[0]["model"]), ("USD", "gpt-6.2-sol"))
        self.assertAlmostEqual(rows[0]["cost"]["amount"], 0.041)
        self.assertEqual([x["kind"] for x in flows() if x["kind"].startswith("call.research.")].count("call.research.final"), 1)
        self.assertEqual(len(flows("research.end")), 1)

    def test_quick_depth_is_one_round(self):
        r = self.runner()
        r.submit("问题", "quick")
        d = r.run_one("r1")
        self.assertEqual([s["kind"] for s in d["steps"]], ["plan", "search", "search", "final"])

    def test_task_cap_is_one_usd_by_default(self):
        """Owner 2026-11-19: 1 USD a task (was 2 CNY); billing is quoted in USD from now on."""
        self.settings = {}
        cfg = self.runner().config()
        self.assertEqual(cfg["cap_usd_per_task"], 1.0)
        self.assertAlmostEqual(cfg["cap_cny_per_task"], 1.0 * research.pause.DEFAULTS["usd_cny"])
        self.settings = {"research": {"cap_usd_per_task": 0.5}}
        self.assertAlmostEqual(self.runner().config()["cap_cny_per_task"], 0.5 * research.pause.DEFAULTS["usd_cny"])

    def test_cap_ends_the_searching_and_keeps_money_for_the_answer(self):
        self.settings = {"research": {"cap_cny_per_task": 1.0}}
        r = self.runner()
        r.submit("问题", "deep")
        d = r.run_one("r1")
        self.assertEqual([s["kind"] for s in d["steps"]], ["plan", "search", "search", "stop", "final"])
        self.assertEqual((d["state"], d["stopped"]), ("done", "task_cap"))
        self.assertEqual(d["answer"]["spoken"], "第一句。第二句！第三句？")
        self.assertLessEqual(d["cost_cny"], 1.0)
        final = next(b for b in self.api.bodies if b.get("text", {}).get("format", {}).get("name") == "final")
        self.assertLessEqual(final["max_output_tokens"], research.FINAL_MAX_OUT)

    def test_cap_reached_stops_and_reports_what_was_found(self):
        # one search costs far more than estimated (20k output tokens = $0.2 + ...): the cap is spent after it
        api = FakeApi(search_usage={"input_tokens": 20000, "output_tokens": 20000})
        self.settings = {"research": {"cap_cny_per_task": 1.0}}
        r = self.runner(api=api)
        r.submit("问题", "normal")
        d = r.run_one("r1")
        self.assertEqual([s["kind"] for s in d["steps"]], ["plan", "search", "stop"])
        self.assertEqual((d["state"], d["stopped"]), ("done", "task_cap"))
        self.assertTrue(d["answer"]["report"].startswith("（这次研究的钱用到上限了"))
        self.assertIn("关于小问题一的要点", d["answer"]["report"])
        self.assertTrue(d["answer"]["spoken"].startswith("这次研究的钱用到上限了，先停了。"))
        self.assertFalse(any(b.get("text", {}).get("format", {}).get("name") == "final" for b in api.bodies))
        self.assertEqual(len(self.reports), 1)

    def test_daily_cap(self):
        r = self.runner()
        budget_pause(self.db)
        with self.assertRaises(ToolError) as e:
            r.submit("问题")
        self.assertEqual(e.exception.code, "budget")
        self.db["switches"].docs.clear()
        r.submit("问题")
        budget_pause(self.db)                                  # reached while the job waited
        d = r.run_one("r1")
        self.assertEqual((d["state"], d["stopped"], d["error"]), ("failed", "daily_cap", "今天的花费到上限了"))
        self.assertEqual(self.api.bodies, [])
        self.assertEqual(len(self.reports), 1)

    def test_day_left_counts_too(self):
        # today's cap minus what is counted so far (the ledger sees this job's spend as it goes)
        r = self.runner(day_left_fn=lambda: 0.6 - ((r.get("r1") or {}).get("cost_cny") or 0))
        r.submit("问题", "deep")
        d = r.run_one("r1")
        self.assertEqual(d["stopped"], "task_cap")              # what is left of the day is the smaller cap
        self.assertEqual(sum(1 for s in d["steps"] if s["kind"] == "search"), 1)

    def test_report_once_even_when_the_hook_fails(self):
        calls = []

        def hook(job):
            calls.append(job["_id"])
            raise RuntimeError("ear down")
        r = self.runner(report=hook)
        r.submit("问题", "quick")
        r.run_one("r1")
        r.run_one("r1")                                         # not queued any more: nothing runs again
        r._report("r1")
        d = r.get("r1")
        self.assertEqual(calls, ["r1"])
        self.assertTrue(d["reported"])
        self.assertIn("ear down", d["report_error"])

    def test_cancel(self):
        r = self.runner()
        r.submit("一", "quick")
        r.submit("二", "quick")
        self.assertTrue(r.cancel("r1"))
        self.assertEqual(r.run_one("r1")["state"], "cancelled")
        r._cancel.add("r2")                                 # as cancel() does for a running job
        self.assertEqual(r.run_one("r2")["state"], "cancelled")
        self.assertEqual(self.reports, [])

    def test_pluggable_search_backend_and_failures(self):
        class Search:
            def __init__(self):
                self.asked = []

            def search(self, topic, question, max_out):
                self.asked.append(question)
                if len(self.asked) == 1:
                    return {"text": "", "sources": [], "searches": 0, "usage": {}, "model": "gpt-6.2-sol",
                            "ms": 5, "err": "HTTP 500: busy", "body": None}
                return {"text": "查到了", "sources": [{"title": "t", "url": "https://b.cn"}], "searches": 1,
                        "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                        "model": "gemini-3-flash-preview-search", "ms": 5, "err": None, "body": None}
        s = Search()
        r = self.runner(search=s)
        r.submit("问题", "quick")
        d = r.run_one("r1")
        self.assertEqual(s.asked, ["小问题一", "小问题二"])
        self.assertEqual(d["state"], "done")
        self.assertEqual(d["steps"][1]["err"], "HTTP 500: busy")
        self.assertFalse(any(b.get("tools") for b in self.api.bodies))   # the plug-in did the searching

    def test_nothing_found_fails(self):
        class Empty:
            def search(self, topic, question, max_out):
                return {"text": "", "sources": [], "searches": 1, "usage": {}, "model": "gpt-6.2-sol", "ms": 1,
                        "err": "没有查到内容", "body": None}
        api = FakeApi(final={"spoken": "", "report": "", "sources": []})
        r = self.runner(api=api, search=Empty())
        r.submit("问题", "quick")
        d = r.run_one("r1")
        self.assertEqual((d["state"], d["error"]), ("failed", "最后整理没成功"))
        self.assertEqual(len(self.reports), 1)

    def test_web_search_tool_name_falls_back(self):
        sent = []

        def send(body):
            sent.append(body["tools"][0]["type"])
            if body["tools"][0]["type"] == "web_search":
                return None, 10, "HTTP 400: unknown tool type"
            return {"output": [message("要点", ["https://c.cn"])], "usage": {}}, 10, None
        ws = research.ResponsesWebSearch(send, "gpt-6.2-sol")
        self.assertEqual(ws.search("总", "小", 100)["text"], "要点")
        ws.search("总", "小", 100)
        self.assertEqual(sent, ["web_search", "web_search_preview", "web_search_preview"])

    def test_refusals(self):
        r = research.Runner(self.db, None, settings_fn=lambda: {})
        with self.assertRaises(ToolError) as e:
            r.submit("问题")
        self.assertEqual(e.exception.code, "not_available")      # no key
        self.settings = {"research": {"model": "unknown-model"}}
        with self.assertRaises(ToolError) as e:
            self.runner().submit("问题")
        self.assertEqual(e.exception.code, "not_available")      # no price: no cap
        self.settings = {}
        r = self.runner()
        for _ in range(research.MAX_QUEUED):
            r.submit("问题")
        with self.assertRaises(ToolError) as e:
            r.submit("问题")
        self.assertEqual(e.exception.code, "refused")

    def test_restart_recovery(self):
        self.db.add("research", {"_id": "old", "topic": "旧", "at": T0 - 5000, "state": "running", "reported": False},
                    {"_id": "wait", "topic": "等", "at": T0 - 4000, "state": "queued", "reported": False,
                     "depth": "quick", "model": "gpt-6.2-sol", "cap_cny": 2.0, "steps": []})
        r = self.runner()
        r.recover()
        self.assertEqual((r.get("old")["state"], r.get("old")["error"]), ("failed", "mind 重启了，研究中断"))
        self.assertEqual([j["_id"] for j in self.reports], ["old"])
        self.assertEqual(r._q.get_nowait(), "wait")

    def test_worker_thread(self):
        done = threading.Event()
        r = self.runner(report=lambda job: done.set())
        r.start()
        try:
            r.submit("问题", "quick")
            self.assertTrue(done.wait(5))
            self.assertEqual(r.get("r1")["state"], "done")
        finally:
            r.stop()


class ToolsTest(Base):
    def tools(self, runner):
        return Tools(REG, tool_impls.mind_tools(switches=lambda: {}, store=Store(self.db), research=runner),
                     log=lambda kind, **f: None)

    def test_registry(self):
        by = {t["name"]: t for t in REG["tools"]}
        self.assertEqual((by["research_status"]["group"], by["research_status"]["level"], by["research_status"]["status"]),
                         ("world", "R", "live"))
        self.assertEqual((by["deep_research"]["level"], by["deep_research"]["status"]), ("C", "live"))

    def test_start_and_status(self):
        r = self.runner()
        t = self.tools(r)
        env = t.call("deep_research", {"question": "桌面机器人怎么判断是不是在跟它说话", "depth": "quick"})
        self.assertTrue(env["ok"], env)
        self.assertEqual((env["data"]["job_id"], env["data"]["say"], env["data"]["state"]), ("r1", "开始研究", "queued"))
        self.assertEqual(t.call("deep_research", {"question": "x", "depth": "huge"})["error"]["code"], "bad_args")
        listing = t.call("research_status", {})["data"]
        self.assertEqual((listing["total"], listing["queued"], listing["jobs"][0]["state_zh"]), (1, 1, "排队中"))
        r.run_one("r1")
        one = t.call("research_status", {"id": "r1"})["data"]
        self.assertEqual((one["state_zh"], one["spoken"], one["report"]), ("做完了", "第一句。第二句！第三句？", "结论：……"))
        self.assertLessEqual(len(one["sources"]), 8)
        self.assertEqual(t.call("research_status", {"id": "nope"})["error"]["code"], "bad_args")
        self.assertEqual(t.call("research_status", {})["data"]["jobs"][0]["spoken"], "第一句。第二句！第三句？")

    def test_without_a_runner(self):
        t = self.tools(None)
        self.assertEqual(t.call("deep_research", {"question": "x"})["error"]["code"], "not_available")


if __name__ == "__main__":
    unittest.main()
