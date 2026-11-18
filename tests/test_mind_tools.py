"""hello's tool framework (B1 2026-11-18; server/mind/tools.py, tool_impls.py; rules docs/hello-tools.md): argument
checks, the one envelope, run-log rows, the function-calling export, the first three tools and the HTTP endpoints.

    .private/venv-ear/bin/python -B -m unittest tests.test_mind_tools -v
"""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB
from mind import api, tool_impls
from mind.store import Store
from mind.tools import Paid, ToolError, Tools, load_registry, validate

REG = load_registry()


class FrameworkTest(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.tools = Tools(REG, {"switches_status": lambda a: {"x": 1},
                                 "cost_today": lambda a: Paid({"day": a.get("day")}, {"amount": 0.01, "cur": "CNY"}),
                                 "memory_search": lambda a: (_ for _ in ()).throw(ToolError("switch_off", "记忆关着")),
                                 "people_list": lambda a: 1 / 0},
                           log=lambda kind, **f: self.rows.append((kind, f)))

    def test_the_registry_is_found(self):
        self.assertGreaterEqual(len(REG["tools"]), 50)

    def test_success_and_cost(self):
        env = self.tools.call("cost_today", {"day": "2026-11-18"}, trace="t1", caller="test")
        self.assertEqual((env["ok"], env["data"], env["cost"], env["trace"], env["error"]),
                         (True, {"day": "2026-11-18"}, {"amount": 0.01, "cur": "CNY"}, "t1", None))
        kind, f = self.rows[-1]
        self.assertEqual((kind, f["trace"], f["by"], f["ok"], f["cost"]), ("call.tool.cost_today", "t1", "test", True,
                                                                           {"amount": 0.01, "cur": "CNY"}))

    def test_errors_come_back_in_the_envelope(self):
        self.assertEqual(self.tools.call("nope")["error"]["code"], "not_available")
        self.assertEqual(self.tools.call("turn_head", {"to": "left45"})["error"]["code"], "not_available")  # no impl yet
        self.assertEqual(self.tools.call("memory_search", {"query": "x"})["error"],
                         {"code": "switch_off", "message": "记忆关着"})
        self.assertEqual(self.tools.call("people_list")["error"]["code"], "failed")             # a bug: no 500
        self.assertEqual(self.tools.call("memory_search", {})["error"]["code"], "bad_args")    # query required
        self.assertTrue(all(r[1]["ok"] is False for r in self.rows[-5:]))

    def test_argument_checks(self):
        p = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 5},
                                              "s": {"type": "string", "maxLength": 3, "enum": ["a", "bb"]},
                                              "b": {"type": "boolean"}, "l": {"type": "array", "items": {"type": "string"}}},
             "required": ["n"]}
        self.assertEqual(validate(p, {"n": 2.0, "s": "bb", "b": True, "l": ["x"]}), {"n": 2, "s": "bb", "b": True, "l": ["x"]})
        for bad in ({}, {"n": 0}, {"n": 2, "s": "c"}, {"n": 2, "b": 1}, {"n": True}, {"n": 2, "zz": 1}, {"n": 2, "l": [1]}, []):
            with self.assertRaises(ToolError):
                validate(p, bad)

    def test_export_only_live_tools(self):
        self.assertEqual(self.tools.export(), [t for t in self.tools.export(include_unlisted=True)
                                               if REG and next(x for x in REG["tools"] if x["name"] == t["function"]["name"])["status"] == "live"])
        names = [t["function"]["name"] for t in self.tools.export(include_unlisted=True)]
        self.assertIn("switches_status", names)
        self.assertNotIn("turn_head", names)                       # no implementation: never exported


class Pause:
    def view(self, now):
        return {"paused": True, "why": "away", "spent_cny": 3.2, "cap_cny": 20}


class MindToolsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.db["switches"].update_one({"_id": "eye"}, {"$set": {"on": True}}, upsert=True)
        self.db["switches"].update_one({"_id": "mouth"}, {"$set": {"mode": "tts"}}, upsert=True)

    def impls(self, **kw):
        sw = lambda: {"tingwu": {"on": False}, "eye": {"on": True}, "notes": {"on": True}, "realtime": {"on": True},
                      "mouth": {"mode": "tts"}, "pause": Pause().view(0), "billing": {"on": True}}
        return tool_impls.mind_tools(switches=sw, store=self.store, clock_ms=lambda: 1795000000000, **kw)

    def test_switches_status(self):
        d = self.impls()["switches_status"]({})
        self.assertEqual(d["switches"]["eye"], {"title": "识别（看画面）", "on": True})
        self.assertEqual(d["switches"]["realtime"]["title"], "Docking（实时对话）")
        self.assertEqual(d["pause"], {"paused": True, "why": "没人在，暂停花钱的功能", "spent_cny": 3.2, "cap_cny": 20})
        self.assertEqual(d["mouth"], {"mode": "tts", "title": "合成说话"})

    def test_mode_status(self):
        class MS:
            def current(self):
                return {"mode": "crowd", "since": 1795000000000, "look": {"label": "DOCK"}}

            def robot(self):
                return {"connected": True, "at": 1795000000000}
        d = self.impls(mode_store=MS())["mode_status"]({})
        self.assertEqual((d["mode"], d["name"], d["label"], d["robot_online"]), ("crowd", "Docking", "DOCK", True))
        talk = next(m for m in d["modes"] if m["id"] == "talk")
        self.assertIn("forward", talk["how_to_enter"])
        with self.assertRaises(ToolError):
            self.impls()["mode_status"]({})

    def test_cost_today(self):
        led = {"by": [{"svc": "ear", "model": "qwen-audio", "cur": "CNY", "amount": 1.234567, "n": 40},
                      {"svc": "mind", "model": "luna", "cur": "USD", "amount": 0.02, "n": 9}],
               "total": {"CNY": 1.234567, "USD": 0.02}, "unknown_n": 1}
        days = []
        d = self.impls(ledger=lambda day: (days.append(day), led)[1])["cost_today"]({})
        self.assertEqual(days, ["2026-11-18"])                   # Beijing day of the clock
        self.assertEqual((d["total"], d["top"][0]["amount"], d["cap_cny"]), ({"CNY": 1.234567, "USD": 0.02}, 1.2346, 20))
        with self.assertRaises(ToolError):
            self.impls()["cost_today"]({})                       # no log query deployed
        with self.assertRaises(ToolError):
            self.impls(ledger=lambda day: led)["cost_today"]({"day": "18/11"})


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.server = api.make_server(Store(self.db), "k", port=0, ctx={})
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}" + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer k", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_tools_list_and_call(self):
        st, d = self.call("/tools?all=1")
        self.assertEqual(st, 200)
        self.assertIn("switches_status", d["available"])
        self.assertTrue(all(t["type"] == "function" for t in d["tools"]))
        st, env = self.call("/tool/call", {"name": "switches_status", "args": {}, "trace": "abc", "caller": "test"})
        self.assertEqual((st, env["ok"], env["trace"]), (200, True, "abc"))
        self.assertIn("realtime", env["data"]["switches"])
        st, env = self.call("/tool/call", {"name": "mode_status"})
        self.assertEqual((env["ok"], env["error"]["code"]), (False, "not_available"))   # no mode store here
        st, env = self.call("/tool/call", {"name": "switches_status", "args": {"x": 1}, "trace": "bad trace!"})
        self.assertEqual((env["error"]["code"], env["trace"]), ("bad_args", None))


if __name__ == "__main__":
    unittest.main()
