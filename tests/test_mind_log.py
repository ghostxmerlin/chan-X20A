"""mind's side of the run/audit log (server/common/hello_log.py, Task 4): llm.chat records every model call
(kind from the caller's scope), switch writes record before/after/by, mode inputs / changes, preset results, the
chat turn carries the ear's trace, and each entry point's heartbeat state. FakeDB, no Mongo, no network."""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, capture_log, stop_log   # noqa: E402
import hello_log                                              # noqa: E402
from mind import api, billing, eye, fusion, llm, memory_extract, model_calls, runner, runlog, segments   # noqa: E402
from mind.billing import Billing, apply_preset                # noqa: E402
from mind.mode_store import ModeStore                         # noqa: E402
from mind.store import Store                                  # noqa: E402

KEY = "sk-xTVlTESTKEY0123456789"
ENV = {"base_url": "http://model.invalid/v1", "api_key": KEY}
B64 = "QUJD" * 2000


class Resp:
    def __init__(self, obj):
        self.raw = json.dumps(obj).encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(content="好的，我看到了。", usage=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg = {"role": "assistant", "content": None, "tool_calls": tool_calls}
    return {"choices": [{"message": msg}], "usage": usage if usage is not None else {"prompt_tokens": 1000, "completion_tokens": 200}}


def urlopen_returning(obj, seen=None):
    def fake(req, timeout=None):
        if seen is not None:
            seen.append(req)
        return Resp(obj)
    return mock.patch("urllib.request.urlopen", fake)


class LogCase(unittest.TestCase):
    def setUp(self):
        self.flows = capture_log("mind")

    def tearDown(self):
        stop_log()

    def one(self, kind):
        rows = self.flows(kind)
        self.assertEqual(len(rows), 1, f"{kind}: {[r['kind'] for r in self.flows()]}")
        return rows[0]

    def body(self, rec, name):
        return self.flows.db.bodies(hello_log.month_of(rec["at"]))[rec["body"][name]]


class LlmChatTest(LogCase):
    def messages(self):
        return [{"role": "system", "content": "你是 hello 的眼睛。"},
                {"role": "user", "content": [{"type": "text", "text": "这一分钟按时间顺序的画面，主人在桌前"},
                                             {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + B64}}]}]

    def test_llm_chat_logs_call(self):
        body = {"model": "gpt-6-luna", "messages": self.messages(), "temperature": 0}
        with urlopen_returning(reply()):
            with hello_log.scope("call.eye", trace="t", ref={"minute": T0}):
                resp, ms, err = llm.chat(ENV["base_url"], KEY, body)
        self.assertIsNone(err)
        self.assertEqual(resp["choices"][0]["message"]["content"], "好的，我看到了。")   # return value unchanged
        r = self.one("call.eye")
        usage = {"prompt_tokens": 1000, "completion_tokens": 200}
        self.assertEqual((r["model"], r["ok"], r["trace"], r["ref"], r["usage"]), ("gpt-6-luna", True, "t", {"minute": T0}, usage))
        self.assertEqual(r["cost"], {"amount": llm.cost_usd("gpt-6-luna", usage), "cur": "USD", "est": True,
                                     "basis": "llm.PRICES 2026-11-15"})
        self.assertEqual(r["key"], "sk-xTVl")
        self.assertNotIn(KEY, json.dumps(r, ensure_ascii=False))
        self.assertIsInstance(r["ms"], int)
        self.assertTrue(r["sum"].endswith("主人在桌前"))
        self.assertLessEqual(len(r["sum"]), 60)
        self.assertEqual(json.loads(self.body(r, "system")), ["你是 hello 的眼睛。"])
        self.assertEqual(self.body(r, "output"), "好的，我看到了。")
        inp = self.body(r, "input")
        self.assertIn("主人在桌前", inp)
        self.assertIn("[image omitted", inp)
        self.assertNotIn("QUJDQUJD", inp)                  # no base64 in the stored body
        self.assertNotIn(KEY, json.dumps(self.flows.db.bodies(hello_log.month_of(r["at"])), ensure_ascii=False))

    def test_llm_chat_logs_error(self):
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b"upstream broke"))
        with mock.patch("urllib.request.urlopen", boom):
            resp, ms, err = llm.chat(ENV["base_url"], KEY, {"model": "gpt-6-luna", "messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual((resp, err), (None, "HTTP 500: upstream broke"))
        r = self.one("call.llm")                          # no scope: call.llm
        self.assertEqual((r["ok"], r["err"][:4], r["sum"]), (False, "HTTP", "你好"))
        self.assertIsNone(r["cost"]["amount"])
        self.assertNotIn("output", r.get("body", {}))

    def test_tool_call_output_and_unknown_price(self):
        calls = [{"id": "c1", "type": "function", "function": {"name": "eye", "arguments": "{\"people_count\": 1}"}}]
        with urlopen_returning(reply(tool_calls=calls)):
            llm.chat(ENV["base_url"], KEY, {"model": "some-new-model", "messages": [{"role": "user", "content": "看"}]})
        r = self.one("call.llm")
        self.assertIsNone(r["cost"]["amount"])            # price unknown: amount null, usage kept
        self.assertEqual(r["usage"]["prompt_tokens"], 1000)
        self.assertEqual(json.loads(self.body(r, "output")), calls)

    def test_no_logger_still_works(self):
        stop_log()
        with urlopen_returning(reply()):
            resp, ms, err = llm.chat(ENV["base_url"], KEY, {"model": "gpt-6-luna", "messages": []})
        self.assertIsNone(err)


class SpyChat:
    """A llm.chat stand-in that remembers the log scope of every call."""
    def __init__(self, inner):
        self.inner, self.scopes = inner, []

    def __call__(self, base_url, key, body, timeout=90):
        self.scopes.append(hello_log.current())
        return self.inner(base_url, key, body, timeout)


class CallSiteTest(LogCase):
    def test_eye_minute_is_call_eye_with_minute_and_no_image(self):
        from test_mind_eye import EyeTest, eye_json
        t = EyeTest("test_frames_call_once_with_images_and_m12_lines")
        t.setUp()
        self.addCleanup(t.doCleanups)
        t.frame(T0 + 1000, 1)
        tool = [{"id": "c", "type": "function", "function": {"name": "eye", "arguments": eye_json()}}]
        old = eye.CHAT
        eye.CHAT = llm.chat
        self.addCleanup(setattr, eye, "CHAT", old)
        with urlopen_returning(reply(tool_calls=tool)):
            rec = t.run_at()
        self.assertEqual(rec["source"], "model")
        r = self.one("call.eye")
        self.assertEqual((r["ref"], r["model"], len(r["trace"])), ({"minute": T0}, eye.EYE_MODEL, 12))
        self.assertIn("[image omitted", self.body(r, "input"))
        self.assertNotIn(";base64,", self.body(r, "input"))

    def test_eye_retry_shares_the_trace(self):
        from test_mind_eye import EyeTest
        t = EyeTest("test_bad_reply_retries_once_then_fails_and_backs_off")
        t.setUp()
        self.addCleanup(t.doCleanups)
        t.frame(T0 + 1000, 1)
        t.fake.reply_next("not json")
        t.fake.reply_next("not json")
        spy = SpyChat(t.fake)
        eye.CHAT = spy
        self.addCleanup(setattr, eye, "CHAT", t.fake)
        t.run_at()
        self.assertEqual([s["kind"] for s in spy.scopes], ["call.eye", "call.eye"])
        self.assertEqual(spy.scopes[0]["trace"], spy.scopes[1]["trace"])

    def test_fusion_and_segment_kinds(self):
        from test_mind_fusion import FusionTest, T
        t = FusionTest("test_first_minute_calls_and_logs_a_run")
        t.setUp()
        spy = SpyChat(t.fake)
        fusion.CHAT = spy
        self.addCleanup(setattr, fusion, "CHAT", t.fake)
        t.run_at(T)
        self.assertEqual((spy.scopes[0]["kind"], spy.scopes[0]["ref"]["minute"]), ("call.fusion", T))
        from test_mind_segments import T as ST, UpdateTest as SegUpdate
        st = SegUpdate("test_closed_segment_gets_summarized_once")
        st.setUp()
        spy2 = SpyChat(st.fake)
        fusion.CHAT = spy2
        st.add(ST, ["seen"] * 5 + ["likely_absent"] * 4)
        st.update(ST + 20 * 60_000)
        self.assertTrue(spy2.scopes)
        self.assertEqual({s["kind"] for s in spy2.scopes}, {"call.segment"})
        self.assertEqual(spy2.scopes[0]["ref"]["minute"], ST)

    def test_notes_kind_and_cid(self):
        from test_mind_notes import ENV as NENV, S as NS, UpdateTest, notes_json, sent
        from mind import notes
        t = UpdateTest("test_unknown_continues_id_is_ignored")
        t.setUp()
        spy = SpyChat(t.fake)
        notes.CHAT = spy
        self.addCleanup(setattr, notes, "CHAT", t.fake)
        t.fake.reply_next(notes_json())
        conv = notes.update(NENV, t.store, NS, None, [sent(T0)], T0 + 60_000 + 10_000)
        self.assertEqual((spy.scopes[0]["kind"], spy.scopes[0]["ref"]["cid"]), ("call.notes", conv["cid"]))

    def test_run_json_kind_is_call_plus_step(self):
        store = Store(FakeDB())
        s = {"tz": "Asia/Shanghai", "budget_usd_per_day": 20.0}
        schema = model_calls.object_schema({"text": {"type": "string"}})
        with urlopen_returning(reply(content=json.dumps({"text": "嗨"}))):
            out, run_id, err = model_calls.run_json(ENV, store, s, "say_line", "say-line-v1", schema,
                                                    [{"role": "user", "content": "写一句"}], T0 + 5000)
            model_calls.run_json(ENV, store, s, "memory", "m1", schema, [{"role": "user", "content": "记"}], T0, lane="replay")
        self.assertEqual((out, err), ({"text": "嗨"}, None))
        r = self.one("call.say.line")
        self.assertEqual(r["ref"], {"minute": T0, "lane": "live", "step": "say_line"})
        self.assertEqual(self.one("call.memory")["ref"]["lane"], "replay")

    def test_say_round_lines_share_one_trace(self):
        from test_mind_say_round import RoutedChat, SayRoundTest
        t = SayRoundTest("test_round_writes_five_plus_huyu_and_ranks")
        t.setUp()
        routed = RoutedChat()
        lock, scopes = threading.Lock(), []

        def chat(base, key, body, timeout=90):
            with lock:
                scopes.append(hello_log.current())
            return routed(base, key, body, timeout)
        t.write(chat)                                 # five lines + huyu in a thread pool, then the judge
        self.assertEqual(sorted({x["kind"] for x in scopes}), ["call.say.huyu", "call.say.judge", "call.say.line"])
        self.assertEqual(len({x["trace"] for x in scopes}), 1)     # one round, one trace (pool threads included)
        self.assertEqual(len(scopes), 7)


class ChatTraceTest(LogCase):
    def test_chat_turn_carries_the_ears_trace(self):
        from test_mind_chat import ChatBase, heard
        t = ChatBase("run")
        t.setUp()
        self.addCleanup(t.tearDown)
        t.chat.chat_fn = llm.chat
        with urlopen_returning(reply(content="我在听，你说。")):
            r = t.chat.heard(heard(trace="ear0trace01"))
            self.assertTrue(r["accepted"])
            t.chat.process_once()
        rec = self.one("call.chat")
        self.assertEqual((rec["trace"], rec["ref"]["tid"]), ("ear0trace01", r["tid"]))
        self.assertTrue(rec["sum"].endswith("今天中午吃什么好呢"))

    def test_chat_turn_without_trace_gets_one(self):
        from test_mind_chat import ChatBase, heard
        t = ChatBase("run")
        t.setUp()
        self.addCleanup(t.tearDown)
        spy = SpyChat(t.model)
        t.chat.chat_fn = spy
        t.chat.heard(heard())
        t.chat.process_once()
        self.assertEqual(spy.scopes[0]["kind"], "call.chat")
        self.assertEqual(len(spy.scopes[0]["trace"]), 12)

    def test_bad_trace_is_ignored(self):
        from mind import chat as chat_mod
        from test_mind_chat import heard
        self.assertIsNone(chat_mod.parse(heard(trace=123))["trace"])
        self.assertEqual(chat_mod.parse(heard(trace="x" * 200))["trace"], "x" * 64)


class SwitchTest(LogCase):
    def ctx(self, db, store=None):
        return runner._BillingCtx(db, store, None, None, None, None, None, lambda: T0)

    def test_switch_set_logs_before_after(self):
        db = FakeDB().add("switches", {"_id": "tingwu", "on": True})
        self.ctx(db).set_switch("tingwu", False)
        self.assertIs(db["switches"].find_one({"_id": "tingwu"})["on"], False)
        r = self.one("switch.set")
        self.assertEqual((r["extra"], r["by"], r["ok"]), ({"name": "tingwu", "before": True, "after": False}, "mode", True))
        self.assertIn("听悟", r["sum"])

    def test_mind_and_mouth_switches(self):
        db = FakeDB().add("switches", {"_id": "mouth", "mode": "clips"})
        store = Store(db)
        ctx = self.ctx(db, store)
        ctx.set_mind(False)
        ctx.set_mouth("tts")
        rows = self.flows("switch.set")
        self.assertEqual([r["extra"] for r in rows], [{"name": "mind", "before": True, "after": False},
                                                      {"name": "mouth", "before": "clips", "after": "tts"}])

    def test_failed_write_is_logged_and_raised(self):
        db = FakeDB()
        db["switches"].fail = RuntimeError("db down")
        with self.assertRaises(RuntimeError):
            self.ctx(db).set_switch("eye", True)
        r = self.one("switch.set")
        self.assertEqual((r["ok"], r["extra"]["before"], r["extra"]["after"]), (False, None, True))
        self.assertIn("RuntimeError", r["err"])

    def test_extract_switch(self):
        db = FakeDB()
        memory_extract.set_switch(db, False, T0)
        r = self.one("switch.set")
        self.assertEqual((r["extra"], r["by"]), ({"name": "memory_extract", "before": True, "after": False}, "page"))

    def test_say_workers(self):
        from mind import say_auto, say_prefetch
        class St:
            def set_state(self, **kw):
                pass
        w = say_prefetch.Worker(St(), settings_fn=dict, mouth_fn=lambda: "tts", ctx_fn=None, write_fn=None)
        a = say_auto.Worker(St(), prefetch=w, settings_fn=dict, mouth_fn=lambda: "tts", play_fn=None)
        a.set_enabled(True, T0)
        got = [(r["extra"]["name"], r["extra"]["before"], r["extra"]["after"]) for r in self.flows("switch.set")]
        self.assertEqual(got, [("say_prefetch", False, True), ("say_auto", False, True)])

    def test_page_switches(self):
        db = FakeDB().add("switches", {"_id": "notes", "on": True})
        store = Store(db)
        server = api.make_server(store, "k", "127.0.0.1", 0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def post(obj):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{obj.pop('_path', '/switches')}",
                                         data=json.dumps(obj).encode(), method="POST",
                                         headers={"Authorization": "Bearer k", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        self.assertEqual(post({"name": "notes", "on": False}), 200)
        self.assertEqual(post({"name": "mouth", "mode": "tts"}), 200)
        self.assertEqual(post({"_path": "/switch", "enabled": False}), 200)
        rows = self.flows("switch.set")
        self.assertEqual([(r["extra"]["name"], r["extra"]["before"], r["extra"]["after"], r["by"]) for r in rows],
                         [("notes", True, False, "page"), ("mouth", "clips", "tts", "page"), ("mind", True, False, "page")])


class ModeAndPresetTest(LogCase):
    def test_preset_apply_logged(self):
        from test_mind_billing import FakeCtx
        b = Billing(FakeCtx(tingwu=True, mind=True))
        r = apply_preset(0, b, source="page", now_ms=0)
        self.assertEqual(r["result"]["code"], "ok")
        rec = self.one("preset.apply")
        self.assertEqual((rec["extra"]["n"], rec["extra"]["failed"], rec["by"], rec["ok"]), (0, [], "page", True))
        self.assertEqual(rec["extra"]["code"], "ok")

    def test_preset_failed_steps(self):
        from test_mind_billing import FakeCtx
        ctx = FakeCtx()
        def broken(name, on):
            raise RuntimeError("down")
        ctx.set_switch = broken
        apply_preset(0, Billing(ctx), source="asr", now_ms=0)
        rec = self.one("preset.apply")
        self.assertEqual((rec["ok"], rec["extra"]["failed"]), (False, ["tingwu", "eye", "notes", "realtime"]))

    def test_mode_input_logged(self):
        ms = ModeStore(FakeDB(), clock_ms=lambda: 0)
        ms.apply("digit", 3, source="asr", device="robot", boot="b1", dev_seq=9, now_ms=10)
        r = self.one("mode.input")
        self.assertEqual((r["by"], r["extra"]["input"], r["extra"]["value"], r["extra"]["code"]),
                         ("asr", "digit", 3, "undefined_preset"))
        self.assertEqual(self.flows("mode.change"), [])

    def test_mode_change_and_digit_preset_share_a_trace(self):
        from test_mind_billing import FakeCtx
        ctx = FakeCtx()
        b = Billing(ctx)
        ctx.mode_store.apply("digit", 0, source="asr", device="robot", boot="b1", dev_seq=1, now_ms=10)
        inp, pre = self.flows("mode.input")[-1], self.one("preset.apply")
        self.assertEqual(inp["trace"], pre["trace"])
        ctx.mode_store.apply("play", None, source="page", now_ms=20)
        ch = self.one("mode.change")
        self.assertEqual((ch["by"], ch["mode"], ch["extra"]["from"]["mode"], ch["extra"]["to"]["mode"]),
                         ("page", "chat", "talk", "chat"))

    def test_report_change_and_billing_set(self):
        ms = ModeStore(FakeDB(), clock_ms=lambda: 0)
        ms.report({"seq": 1, "mode": "chat", "dir": None, "host": None, "return_to": None, "by": "asr", "input": "play",
                   "effects": []}, boot="b1", now_ms=5)
        self.assertEqual(self.one("mode.change")["extra"]["to"]["mode"], "chat")
        ms.set_billing(False, source="page", now_ms=6)
        r = self.one("billing.set")
        self.assertEqual((r["extra"], r["by"]), ({"before": True, "after": False}, "page"))


class EntryTest(LogCase):
    def test_beat_state_reads_switches_and_mind(self):
        db = FakeDB().add("switches", {"_id": "tingwu", "on": True}, {"_id": "mouth", "mode": "tts"},
                          {"_id": "eye_status", "running": True})
        store = Store(db)
        store.set_state(enabled=False)
        self.assertEqual(runlog.beat_state(db, store), {"tingwu": True, "mouth": "tts", "mind": False})
        db["switches"].fail = RuntimeError("down")
        st = runlog.beat_state(db, store)            # never raises
        self.assertIn("error", st)

    def test_start_never_raises_and_reads_code_version(self):
        from log_fixtures import temp_dir
        stop_log()
        d = temp_dir()                                   # removed at exit: the writer may still be finishing
        cv = d / "CODE_VERSION"
        cv.write_text("abc1234\n")
        with mock.patch.object(runlog, "SPOOL_DIR", str(d)), mock.patch.object(runlog, "CODE_VERSION_FILE", str(cv)):
            lg = runlog.start("eye", {}, {"settings": "x"}, install_signal=False)
            self.assertIsNotNone(lg)
            self.assertEqual((lg.svc, lg.code_version), ("eye", "abc1234"))
            lg.close(2)
        with mock.patch.object(runlog.hello_log, "init", side_effect=OSError("no")):
            self.assertIsNone(runlog.start("eye", {}, None))
        hello_log._current = None
        self.assertEqual(runlog.code_version(str(d / "missing")), "")

    def test_entry_points_start_the_log_even_without_mongo(self):
        import signal
        from mind import eye_runner, memory_worker, notes_runner
        stop_log()
        prev = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, prev)
        from log_fixtures import temp_dir
        d = str(temp_dir())                              # removed at exit: the writer may still be finishing
        for svc, main in (("mind", runner.main), ("eye", eye_runner.main), ("notes", notes_runner.main),
                          ("memory", memory_worker.main)):
            with mock.patch.object(runlog, "SPOOL_DIR", d), \
                    mock.patch.dict(sys.modules, {"pymongo": types.SimpleNamespace(MongoClient=None)}), \
                    mock.patch.object(sys, "argv", ["x", "--hubmix-env", os.path.join(d, "missing.env")]):
                with self.assertRaises(SystemExit):          # MONGO_URI missing: the service refuses to start
                    main()
            lg = hello_log.get()
            self.assertEqual(lg.svc, svc)                    # ... after the log started (spool only)
            lg.close(2)
            hello_log._current = None


    def test_runlog_error_records_svc_error_and_never_raises(self):
        stop_log()
        flows = capture_log("mind")
        self.addCleanup(stop_log)
        runlog.error(ValueError("bad\nthing"), "mind.loop")
        runlog.error(ValueError("bad\nthing"), "mind.loop")      # throttled
        errs = flows("svc.error")
        self.assertEqual([(f["err"], f["extra"]["where"], f["ok"]) for f in errs], [("ValueError: bad thing", "mind.loop", False)])
        runlog.error(KeyError("某人的私事"), "memory.loop", type_only=True)
        errs = flows("svc.error")
        self.assertEqual([(f["err"], f["extra"]["where"]) for f in errs][-1], ("KeyError", "memory.loop"))
        self.assertNotIn("私事", json.dumps(errs, ensure_ascii=False))
        with mock.patch.object(runlog.hello_log, "error", side_effect=RuntimeError("x")):
            runlog.error(ValueError("y"), "mind.loop")             # never raises
        self.assertIsNone(runlog._Off.error(ValueError("z"), where="w"))

    def test_runner_loops_record_svc_error(self):
        """Every `except Exception` directly inside a runner's `while True` loop (the top-level handler that keeps the
        service running) calls runlog.error, and memory_worker.tick's catch-all does too."""
        import ast
        root = Path(__file__).resolve().parents[1] / "server" / "mind"
        found = {}
        for name in ("runner.py", "eye_runner.py", "notes_runner.py", "memory_worker.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for loop in ast.walk(tree):
                if not (isinstance(loop, ast.While) and isinstance(loop.test, ast.Constant) and loop.test.value is True):
                    continue
                for node in loop.body:
                    if not isinstance(node, ast.Try):
                        continue
                    for h in node.handlers:
                        if isinstance(h.type, ast.Name) and h.type.id == "Exception":
                            calls = [c for c in ast.walk(h) if isinstance(c, ast.Call)
                                     and isinstance(c.func, ast.Attribute) and c.func.attr == "error"
                                     and isinstance(c.func.value, ast.Name) and c.func.value.id == "runlog"]
                            found.setdefault(name, []).append(len(calls))
        self.assertEqual(found, {"runner.py": [1], "eye_runner.py": [1], "notes_runner.py": [1],
                                 "memory_worker.py": [1, 1]})
        src = (root / "memory_worker.py").read_text(encoding="utf-8")
        self.assertIn("runlog.error(e,'memory.tick',type_only=True)", src)
        # memory errors can carry private content: every memory call records the type only
        self.assertEqual(src.count("runlog.error("), src.count("type_only=True)"))

    def test_mind_runs_without_hello_log(self):
        """hello_log not importable at all: every mind path still works, nothing is recorded."""
        code = ("import sys, json; sys.modules['hello_log'] = None; sys.path.insert(0, sys.argv[1]);"
                "from unittest import mock;"
                "from mind import llm, runlog, runner, billing, mode_store;"
                "assert isinstance(runlog.hello_log, runlog._Off);"
                "assert runlog.start('mind', {}, None) is None;"
                "R = type('R', (), {'read': lambda s: json.dumps({'choices': [{'message': {'content': 'x'}}]}).encode(),"
                "'__enter__': lambda s: s, '__exit__': lambda s, *a: False});"
                "p = mock.patch('urllib.request.urlopen', lambda req, timeout=None: R());"
                "p.start(); out = llm.chat('http://m.invalid', 'k', {'model': 'gpt-6-luna', 'messages': []}); p.stop();"
                "assert out[2] is None, out;"
                "ms = mode_store.ModeStore(__import__('mind_fixtures').FakeDB(), clock_ms=lambda: 0);"
                "ms.apply('play', None, source='page', now_ms=1);"
                "print('ok')")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent))
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run([sys.executable, "-B", "-c", code, str(root / "server")], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1:]), (0, ["ok"]), r.stderr)
        self.assertIn("hello_log is not deployed", r.stdout)


if __name__ == "__main__":
    unittest.main()
