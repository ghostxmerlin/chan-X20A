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


from datetime import datetime
from zoneinfo import ZoneInfo
from mind import tool_time

SH = ZoneInfo("Asia/Shanghai")
NOW = int(datetime(2026, 11, 18, 19, 30, tzinfo=SH).timestamp() * 1000)     # a Wednesday


def at(*a):
    return int(datetime(*a, tzinfo=SH).timestamp() * 1000)


class TimeWordsTest(unittest.TestCase):
    def p(self, t):
        return tool_time.parse(t, NOW)

    def test_points_and_spans(self):
        self.assertEqual(self.p("1小时前"), (NOW - 3600_000,) * 2)
        self.assertEqual(self.p("半小时前"), (NOW - 1800_000,) * 2)
        self.assertEqual(self.p("三天前")[0], NOW - 3 * 86400_000)
        self.assertEqual(self.p("今天"), (at(2026, 11, 18), at(2026, 11, 19)))
        self.assertEqual(self.p("昨天下午"), (at(2026, 11, 17, 12), at(2026, 11, 17, 18)))
        self.assertEqual(self.p("今天上午"), (at(2026, 11, 18, 6), at(2026, 11, 18, 12)))
        self.assertEqual(self.p("下午3点半"), (at(2026, 11, 18, 15, 30),) * 2)
        self.assertEqual(self.p("昨天晚上8点"), (at(2026, 11, 17, 20),) * 2)
        self.assertEqual(self.p("9:00"), (at(2026, 11, 18, 9),) * 2)
        self.assertEqual(self.p("2026-11-17 09:15"), (at(2026, 11, 17, 9, 15),) * 2)
        self.assertEqual(self.p("11-16"), (at(2026, 11, 16), at(2026, 11, 17)))
        self.assertEqual(self.p("上周三"), (at(2026, 11, 11), at(2026, 11, 12)))
        self.assertEqual(self.p("周五")[0], at(2026, 11, 13))          # a bare later weekday = last week's
        self.assertEqual(self.p("这周"), (at(2026, 11, 16), NOW))
        with self.assertRaises(ToolError):
            self.p("很久以前")

    def test_span(self):
        self.assertEqual(tool_time.span(None, None, NOW), (NOW - 86400_000, NOW))
        self.assertEqual(tool_time.span("1小时前", None, NOW), (NOW - 3600_000, NOW))
        self.assertEqual(tool_time.span("昨天", None, NOW), (at(2026, 11, 17), at(2026, 11, 18)))
        self.assertEqual(tool_time.span("今天", None, NOW), (at(2026, 11, 18), NOW))            # capped at now
        self.assertEqual(tool_time.span("前天", "昨天", NOW), (at(2026, 11, 16), at(2026, 11, 18)))
        with self.assertRaises(ToolError):
            tool_time.span("2026-09-01", "今天", NOW)                                 # more than 31 days


class ReviewToolsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.t = tool_impls.mind_tools(switches=lambda: {}, store=self.store, clock_ms=lambda: NOW)

    def test_notes_search_by_time_and_word(self):
        c = self.db["conversations"]
        c.insert_one({"cid": "c1", "start_ms": at(2026, 11, 18, 10), "end_ms": at(2026, 11, 18, 10, 20), "title": "比特币价格",
                      "notes": "核实 BTC"})
        c.insert_one({"cid": "c2", "start_ms": at(2026, 11, 18, 15), "end_ms": None, "last_ms": at(2026, 11, 18, 15, 5),
                      "title": "午饭", "notes": "吃什么"})
        c.insert_one({"cid": "c3", "start_ms": at(2026, 11, 16, 9), "end_ms": at(2026, 11, 16, 9, 9), "title": "旧的", "notes": ""})
        d = self.t["notes_search"]({})
        self.assertEqual([x["title"] for x in d["conversations"]], ["午饭", "比特币价格"])        # last 24 h, newest first
        d = self.t["notes_search"]({"query": "btc", "from": "今天"})
        self.assertEqual((d["total"], d["conversations"][0]["start"]), (1, "11-18 10:00"))
        self.assertEqual(self.t["notes_search"]({"from": "上周三"})["total"], 0)

    def test_review_period(self):
        self.db["conversations"].insert_one({"cid": "c1", "start_ms": at(2026, 11, 17, 14), "end_ms": at(2026, 11, 17, 14, 30),
                                             "title": "改固件", "notes": "刷 0.5.06"})
        self.db["vision"].insert_one({"at": at(2026, 11, 17, 14, 1), "event": "face_named", "name": "me"})
        self.db["speech"].insert_one({"t": at(2026, 11, 17, 14, 2), "text": "hello"})
        d = self.t["review_period"]({"from": "昨天", "detail": "full"})
        self.assertEqual(d["range"], ["11-17 00:00", "11-18 00:00"])
        self.assertEqual([(x["title"], x["notes"]) for x in d["conversations"]], [("改固件", "刷 0.5.06")])
        self.assertEqual(d["people_seen"], ["主人"])
        brief = self.t["review_period"]({"from": "前天", "to": "今天"})
        self.assertNotIn("notes", brief["conversations"][0])

    def test_scene_now_without_data(self):
        with self.assertRaises(ToolError):
            self.t["scene_now"]({})
        self.db["vision_looks"].insert_one({"kind": "scene", "at": NOW - 10_000, "text": "主人在敲键盘"})
        d = self.t["scene_now"]({})
        self.assertEqual(d["camera_now"], {"seconds_ago": 10, "text": "主人在敲键盘"})


from mind.library import Library


class PeopleMemoryToolsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.lib = Library(self.db)
        put = lambda c, f: self.lib.put(c, f, item_id=None, expected_version=None, scope_id="office", now_ms=NOW - 3600_000)
        self.wang = put("people_cards", {"kind": "regular", "display": "小王", "aliases": ["王工"]})
        put("people_notes", {"person_id": self.wang["person_id"], "kind": "preference", "text": "喜欢喝美式"})
        put("knowledge_items", {"kind": "place", "name": "园区咖啡店", "statement": "楼下的咖啡店，美式最好喝",
                                "subjects": ["owner"]})
        put("memory_items", {"kind": "event", "title": "一起买咖啡", "statement": "主人和小王下午去买了美式咖啡",
                             "subjects": ["owner", self.wang["person_id"]],
                             "when": {"type": "point", "start_ms": NOW - 7200_000}})
        self.t = tool_impls.mind_tools(switches=lambda: {}, store=Store(self.db), clock_ms=lambda: NOW)

    def test_people(self):
        names = [p["display"] for p in self.t["people_list"]({})["people"]]
        self.assertIn("小王", names)
        d = self.t["person_lookup"]({"who": "王工"})                       # an alias
        self.assertEqual((d["display"], d["kind"]), ("小王", "常见的人"))
        self.assertEqual(d["notes"], [{"kind": "preference", "text": "喜欢喝美式"}])
        with self.assertRaises(ToolError):
            self.t["person_lookup"]({"who": "张三"})

    def test_knowledge_and_memory(self):
        k = self.t["knowledge_search"]({"query": "咖啡"})
        self.assertEqual(k["items"][0]["name"], "园区咖啡店")
        m = self.t["memory_search"]({"query": "美式咖啡"})
        self.assertTrue(any("美式咖啡" in x["statement"] for x in m["memories"]), m)
        only_wang = self.t["memory_search"]({"query": "咖啡", "person": "小王"})
        self.assertEqual(only_wang["person"], "小王")
        self.assertTrue(all("小王" in x["people"] for x in only_wang["memories"]))


class SpeechToolsTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        sp = lambda band, name=None, best=None: {"band": band, "name": name, "best": best}
        rows = [(NOW - 3600_000, "比特币涨了吗", sp("known", "me"), False),
                (NOW - 3000_000, "我觉得比特币要跌", sp("unsure", None, "me"), False),
                (NOW - 120_000, "中午吃什么", sp("unknown"), False),
                (NOW - 60_000, "这个板子还是识别不了", sp("known", "me"), False),
                (NOW - 50_000, "", sp("known", "me"), False)]
        for i, (t, text, speaker, sounding) in enumerate(rows):
            self.db["speech"].insert_one({"session": "s1", "id": f"s-{i}", "t": t, "pos": [i, i + 1], "text": text,
                                          "speaker": speaker, "self": {"sounding": sounding}})
        self.db["sounds"].insert_one({"t": NOW - 30_000, "kind": "knock", "label": "敲门", "dur": 1.24, "self": {"sounding": False}})
        self.db["sounds"].insert_one({"t": NOW - 20_000, "kind": "speech", "label": "说话", "dur": 2.0, "self": {"sounding": True}})
        self.db["realtime_turns"].insert_one({"at": NOW - 600_000, "state": "done", "heard": "你好", "reply": "在呢"})
        self.db["realtime_turns"].insert_one({"at": NOW - 500_000, "state": "muted_quiet", "heard": "x", "reply": "嗯"})
        self.db["chat_turns"].insert_one({"at": NOW - 400_000, "text": "几点了", "reply": "七点半"})
        self.t = tool_impls.mind_tools(switches=lambda: {}, store=Store(self.db), clock_ms=lambda: NOW)

    def test_speech_search(self):
        d = self.t["speech_search"]({"query": "比特币"})
        self.assertEqual([(x["text"], x["who"]) for x in d["lines"]],
                         [("我觉得比特币要跌", "不确定（像主人）"), ("比特币涨了吗", "主人")])
        d = self.t["speech_search"]({"who": "主人", "from": "今天"})
        self.assertEqual([x["text"] for x in d["lines"]], ["这个板子还是识别不了", "比特币涨了吗"])   # known only, no empty text
        with self.assertRaises(ToolError):
            self.t["speech_search"]({})

    def test_recent_speech_and_sounds(self):
        d = self.t["recent_speech"]({"minutes": 5})
        self.assertEqual([x["text"] for x in d["lines"]], ["中午吃什么", "这个板子还是识别不了"])   # oldest first
        self.assertEqual(d["lines"][0]["who"], "不认识的声音")
        s = self.t["recent_sounds"]({"minutes": 5})
        self.assertEqual(s["events"], [{"time": "19:29", "until": "19:29", "kind": "敲门", "times": 1, "dur_s": 1.2}])
        for i in range(3):                                                      # a run of typing: one entry
            self.db["sounds"].insert_one({"t": NOW - 15_000 + i * 2000, "kind": "typing", "label": "键盘打字", "dur": 1.0})
        s = self.t["recent_sounds"]({"minutes": 5})
        self.assertEqual((s["events"][-1]["kind"], s["events"][-1]["times"], s["events"][-1]["dur_s"]), ("键盘打字", 3, 3.0))

    def test_hello_said(self):
        d = self.t["hello_said"]({"from": "1小时前"})
        self.assertEqual([(x["mode"], x["said"]) for x in d["lines"]], [("对话", "七点半"), ("Docking", "在呢")])
