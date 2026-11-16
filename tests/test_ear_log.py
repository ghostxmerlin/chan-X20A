"""The ear's side of the run/audit log (Task 5): links, heard sentences and their trace, 听悟, 围观 realtime (session,
context, gate, rescue, hush, turn, search, filler), 视觉爆发 (trigger, call.look), robot.state, and SIGTERM on the ear's
own loop. FakeDB and fake sockets only: nothing paid, no Mongo, no network. The realtime / ear_server parts need the
ear's deps (websockets, opuslib_next, sherpa_onnx); run them in the ear test venv:

    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib ../../.tools/ear-test-venv/bin/python -B -m unittest tests.test_ear_log -v
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "server" / "ear"))
from log_fixtures import capture_log as _capture, temp_dir   # noqa: E402  (puts server/common on sys.path)
import hello_log                                              # noqa: E402
import prices                                                 # noqa: E402
import ear_log                                                # noqa: E402
import hello_db                                               # noqa: E402
import mouth_link                                             # noqa: E402
import vision_burst as vb                                     # noqa: E402

try:
    import opuslib_next  # noqa: F401
    import websockets  # noqa: F401
    HAVE_RT = True
except ImportError:
    HAVE_RT = False
try:
    import ear_server                                         # noqa: E402  (numpy, sherpa_onnx)
    HAVE_EAR = True
except Exception:                                             # noqa: BLE001
    ear_server = None
    HAVE_EAR = False
if HAVE_RT:
    import test_realtime_link as trl                          # noqa: E402  (its fake DashScope socket and robot)
    import realtime_link as rl                                # noqa: E402
    HubBase = trl.HubBase
else:
    HubBase = unittest.IsolatedAsyncioTestCase

MONTH = "202611"
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 3000


def capture():
    logger, db = _capture("ear")
    ear_log.reset_robot_state()

    def flows(kind=None):
        logger.flush()
        return [r for r in db.flows() if kind is None or r["kind"] == kind]
    flows.logger, flows.db = logger, db
    return flows


def stop_log():
    if hello_log._current is not None:
        hello_log._current.close(timeout_s=0)
        hello_log._current = None


def bodies(flows, rec):
    texts = flows.db.bodies(MONTH)
    return {name: texts.get(sha) for name, sha in (rec.get("body") or {}).items()}


class Resp:
    def __init__(self, obj):
        self.raw = json.dumps(obj).encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class NullDB:
    """hello_db's database: takes every write, keeps nothing."""
    def __getitem__(self, name):
        return SimpleNamespace(replace_one=lambda *a, **k: None, update_one=lambda *a, **k: None)


def look_reply(text="桌前坐着一个人在看手机。"):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 418, "completion_tokens": 38}}


# ---------------------------------------------------------------- plain parts (system python too)

class PlainTest(unittest.TestCase):
    def setUp(self):
        self.flows = capture()

    def tearDown(self):
        stop_log()

    def failed(self, exc):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        fut = loop.create_future()
        fut.set_exception(exc)
        return fut

    def test_task_failed_records_svc_error(self):
        fut = self.failed(RuntimeError("boom\nsecond line"))
        with mock.patch.object(mouth_link, "log", lambda *a, **k: None), \
                mock.patch.object(vb, "log", lambda *a, **k: None):
            mouth_link.ModeRelay._done(SimpleNamespace(_bg=set()), fut)
            vb.VisionBurst._done(SimpleNamespace(), fut)
            vb.VisionBurst._done(SimpleNamespace(), fut)               # same message within a minute: throttled
        errs = self.flows("svc.error")
        self.assertEqual([(r["extra"]["where"], r["ok"], r["err"]) for r in errs],
                         [("mode.task", False, "RuntimeError: boom second line"),
                          ("burst.task", False, "RuntimeError: boom second line")])

    def test_refused_links_one_record_per_ip_a_minute_rest_in_the_beat(self):
        t = [100.0]
        clock = lambda: t[0]
        with mock.patch.object(ear_log, "_refused_at", {}):
            for _ in range(5):
                ear_log.link_refused("hearing", peer=("10.0.0.9", 5000), clock=clock)
            ear_log.link_refused("mouth", peer=("10.0.0.7", 5001), clock=clock)
            t[0] += 61
            ear_log.link_refused("hearing", peer=("10.0.0.9", 5002), clock=clock)
        opens = self.flows("link.open")
        self.assertEqual([(r["ok"], r["err"]) for r in opens], [(False, "bad_token")] * 3)   # 9, 7, 9 again
        lg = self.flows.logger
        self.assertEqual(lg._bumps, {"refused": 4})
        lg._next_beat = 0
        lg._beat_once()
        beat = self.flows("svc.beat")[-1]
        self.assertEqual(beat["extra"]["refused"], 4)
        self.assertEqual(lg._bumps, {})
        lg.bump("counts")                                          # a beat field name is never overwritten
        self.assertEqual(lg._bumps, {})

    def test_heard_asr_starts_a_trace_on_the_sentence(self):
        db = hello_db.HelloDB("mongodb://unused", client_factory=NullDB)
        ev = {"type": "speech", "phase": "end", "id": "s-3", "pos": [16000, 40000], "t": 1, "dur": 1.5,
              "text": "你好呀，今天天气怎么样", "lang": "zh", "emotion": "happy",
              "speaker": {"band": "known", "name": "person-a", "score": 0.8}}
        db.event("20261116/120000-stackchan-x-c1", ev)
        recs = self.flows("heard.asr")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertRegex(r["trace"], r"^[0-9a-f]{12}$")
        self.assertEqual(ev["trace"], r["trace"])                      # kept on the event for what follows
        self.assertEqual(r["sum"], "你好呀，今天天气怎么样")
        self.assertEqual(r["ref"], {"session": "20261116/120000-stackchan-x-c1", "pos": [16000, 40000], "id": "s-3"})
        self.assertEqual(bodies(self.flows, r)["input"], "你好呀，今天天气怎么样")
        self.assertEqual(hello_db.speech_doc("x", ev)["trace"], r["trace"])
        db.event("s", {"type": "speech", "phase": "end", "id": "s-4", "text": ""})   # nothing heard: no record
        self.assertEqual(len(self.flows("heard.asr")), 1)

    def test_chat_heard_carries_the_trace_to_mind(self):
        body = mouth_link.chat_body({"id": "s-1", "text": "你好", "trace": "abc123abc123"}, "sess")
        self.assertEqual(body["trace"], "abc123abc123")
        self.assertIsNone(mouth_link.chat_body({"id": "s-1", "text": "你好"}, "sess")["trace"])

    def test_robot_state_only_on_change(self):
        relay = mouth_link.ModeRelay(lambda *a, **k: (200, {}))
        try:
            relay.note_report({"type": "mode_report", "seq": 1, "mode": "crowd", "dir": None, "host": None,
                               "return_to": None, "by": "asr", "input": "forward"}, 1)
            relay.note_report({"type": "mode_report", "seq": 2, "mode": "crowd", "dir": None, "host": None,
                               "return_to": None, "by": "asr", "input": "tap"}, 1)
            relay.note_report({"type": "mode_report", "seq": 3, "mode": "chat", "dir": None, "host": None,
                               "return_to": None, "by": "touch", "input": "back"}, 1)
        finally:
            relay.close()
        recs = self.flows("robot.state")
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["extra"]["before"], None)
        self.assertEqual(recs[0]["extra"]["after"]["mode"], "crowd")
        self.assertEqual((recs[1]["extra"]["before"]["mode"], recs[1]["extra"]["after"]["mode"]), ("crowd", "chat"))
        self.assertEqual(recs[1]["by"], "touch")

    def test_overlapping_reconnect_records_no_false_off(self):
        # fix round 1: the robot reconnects while its old socket is half open: c2 on, then c1 closes
        robot = "stackchan-7c4f"
        ear_log.robot_link("hearing", robot, "c1", True)
        ear_log.robot_link("hearing", robot, "c2", True)                # no change: still on
        ear_log.robot_link("hearing", robot, "c1", False)               # the old one goes: c2 still on
        ear_log.robot_link("hearing", "stackchan-other", "c3", True)    # another robot is its own state
        ear_log.robot_link("hearing", robot, "c2", False)               # the last one: off
        ear_log.robot_link("hearing", robot, "c9", False)               # never on: nothing
        recs = [(r["extra"]["device"], r["extra"]["before"], r["extra"]["after"]) for r in self.flows("robot.state")]
        self.assertEqual(recs, [(robot, None, "on"), ("stackchan-other", None, "on"), (robot, "on", "off")])

    def test_tingwu_cost_follows_the_price_table(self):
        c = ear_log.tingwu_cost(60)
        self.assertEqual((c["cur"], c["est"]), ("CNY", True))
        self.assertAlmostEqual(c["amount"], prices.TINGWU_CNY_PER_HOUR / 60)
        self.assertIn("2026-11-16", c["basis"])
        with mock.patch.object(prices, "TINGWU_CNY_PER_HOUR", None):
            self.assertIsNone(ear_log.tingwu_cost(60)["amount"])

    def test_prices_have_a_basis_each(self):
        for name in ("TINGWU_CNY_PER_HOUR", "REALTIME_SEARCH_CNY_PER_CALL", "TTS_CNY_PER_10K_CHARS",
                     "TTS_CNY_PER_M_IN", "TTS_CNY_PER_M_OUT"):
            self.assertTrue(hasattr(prices, name))
            self.assertIn("2026-11-16", prices.BASIS[name])
            v = getattr(prices, name)
            self.assertTrue(v is None or v > 0)
            if v is None:
                self.assertIn("unknown", prices.BASIS[name])

    def test_search_count(self):
        self.assertEqual(ear_log.search_count({"plugins": {"search": {"count": 2, "strategy": "agent"}}}), 2)
        self.assertEqual(ear_log.search_count({"input_tokens": 3}), 0)
        self.assertEqual(ear_log.search_count(None), 0)

    def test_look_logged_without_base64(self):
        seen = []

        def fake(req, timeout=None):
            seen.append(json.loads(req.data))
            return Resp(look_reply())
        recognise = vb.dashscope_look("sk-xTVlLOOKKEY0123456789")
        with mock.patch("urllib.request.urlopen", fake), \
                ear_log.scope(trace="aaaabbbbcccc", ref={"path": "frames/s/1.jpg", "lid": "b1-1"}):
            text, usage, ms, err = recognise(JPEG, "眼前有 1 个人：一个认不出的人。")
        self.assertEqual((text, err), ("桌前坐着一个人在看手机。", None))
        self.assertIn("base64,", json.dumps(seen[0]))                # the model got the photo ...
        recs = self.flows("call.look")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["trace"], r["ref"]), ("aaaabbbbcccc", {"path": "frames/s/1.jpg", "lid": "b1-1"}))
        self.assertEqual((r["ok"], r["model"], r["key"], r["cost"]["cur"]), (True, vb.LOOK_MODEL, "sk-xTVl", "USD"))
        self.assertAlmostEqual(r["cost"]["amount"], vb.cost_usd(usage))
        b = bodies(self.flows, r)
        self.assertEqual((b["system"], b["output"]), (vb.LOOK_SYSTEM, "桌前坐着一个人在看手机。"))
        dump = json.dumps(self.flows.db.bodies(MONTH), ensure_ascii=False) + json.dumps(recs, ensure_ascii=False)
        self.assertNotIn("base64,", dump)                             # ... the log did not
        self.assertIn("[image omitted", b["input"])
        self.assertNotIn("LOOKKEY", dump)
        # params = exactly what went out besides the messages
        self.assertEqual(json.loads(b["params"]), {k: v for k, v in seen[0].items() if k != "messages"})

    def test_failed_look_is_logged_with_its_error(self):
        def fake(req, timeout=None):
            raise OSError("offline")
        with mock.patch("urllib.request.urlopen", fake):
            out = vb.dashscope_look("sk-x")(JPEG)
        self.assertEqual(out[3], "OSError")
        r = self.flows("call.look")[0]
        self.assertEqual((r["ok"], r["err"]), (False, "OSError"))


class ScopeIsolationTest(unittest.IsolatedAsyncioTestCase):
    """Carried from the Task 2 review: scope() is per asyncio task."""

    def setUp(self):
        self.flows = capture()

    def tearDown(self):
        stop_log()

    async def test_gathered_tasks_keep_their_own_trace(self):
        async def one(t, n):
            with hello_log.scope("trigger.test", trace=t):
                for i in range(n):
                    await asyncio.sleep(0.001)
                    hello_log.emit(None, extra={"i": i, "who": t})
        await asyncio.gather(one("aaaaaaaaaaaa", 5), one("bbbbbbbbbbbb", 5))
        hello_log.emit("trigger.test", extra={"who": None})
        recs = self.flows("trigger.test")
        self.assertEqual(len(recs), 11)
        for r in recs:
            self.assertEqual(r.get("trace"), r["extra"]["who"])


class BurstLogTest(unittest.IsolatedAsyncioTestCase):
    """VisionBurst with the real dashscope_look on a fake urlopen: trigger.burst start / stop and one call.look per
    photo, all on the burst's trace, each look pointing at its frame and vision_looks row."""

    async def asyncSetUp(self):
        import test_vision_burst as tvb
        self.tvb = tvb
        vb.log = lambda *a, **k: None
        self.flows = capture()
        self.hub = tvb.Hub()
        self.urlopen = mock.patch("urllib.request.urlopen", lambda req, timeout=None: Resp(look_reply()))
        self.urlopen.start()
        self.burst = vb.VisionBurst(self.hub, vb.dashscope_look("sk-burstkey000"), db=tvb.DB(),
                                    cfg=tvb.fast_cfg(max_looks=2))
        self.hub.robot = tvb.Robot(SimpleNamespace(burst=self.burst, recognising=0))

    async def asyncTearDown(self):
        await self.burst.shutdown()
        self.urlopen.stop()
        stop_log()

    async def test_burst_and_looks_share_a_trace(self):
        with ear_log.scope(trace="feedfeedfeed"):
            self.assertEqual(self.burst.heard("hello 注意看！"), "start")
        end = time.monotonic() + 3
        while self.burst.running() and time.monotonic() < end:
            await asyncio.sleep(0.01)
        trig = self.flows("trigger.burst")
        self.assertEqual([r["extra"]["action"] for r in trig], ["start", "end"])
        self.assertEqual(trig[0]["extra"]["word"], "注意看")
        self.assertEqual(trig[1]["extra"]["why"], "cap")
        looks = self.flows("call.look")
        self.assertEqual(len(looks), 2)
        for r in looks + trig:
            self.assertEqual(r["trace"], "feedfeedfeed")
        self.assertTrue(looks[0]["ref"]["path"].startswith("frames/s/"))
        self.assertTrue(looks[0]["ref"]["lid"].startswith(self.burst._burst["id"] + "-"))


# ---------------------------------------------------------------- realtime (ear venv)

@unittest.skipUnless(HAVE_RT, "needs websockets and opuslib_next")
class RealtimeLogTest(HubBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.flows = capture()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        stop_log()

    def heard(self, text):
        db = hello_db.HelloDB("mongodb://unused", client_factory=NullDB)
        ev = {"type": "speech", "phase": "end", "id": "s-1", "pos": [0, 16000], "text": text}
        db.event("sess", ev)
        self.hub.local_heard(text, trace=ev["trace"])
        return ev["trace"]

    async def test_realtime_turn_logged_with_cost(self):
        ws = await self.open_ready()
        t = self.heard("你好呀，你在干嘛")
        await self.play_one(ws, "r1")
        turns = self.flows("call.realtime.turn")
        self.assertEqual(len(turns), 1)
        r = turns[0]
        usage = {"input_tokens_details": {"text_tokens": 400, "audio_tokens": 10},
                 "output_tokens_details": {"text_tokens": 5, "audio_tokens": 20}}
        self.assertEqual(r["cost"]["cur"], "CNY")
        self.assertAlmostEqual(r["cost"]["amount"], rl.cost_cny(usage))
        self.assertNotEqual(r["trace"], t)                              # R17: the turn's own trace ...
        self.assertEqual([(h["trace"], h["text"]) for h in r["extra"]["heard"]], [(t, "你好呀，你在干嘛")])   # + candidates
        self.assertGreater(r["extra"]["heard"][0]["at"], 1794000000000)    # wall ms, not the hub's clock
        self.assertEqual(r["model"], rl.MODEL)
        self.assertEqual(r["usage"], usage)
        self.assertEqual(r["ref"]["tid"], self.recs.turns[-1]["tid"])
        self.assertEqual(r["extra"]["state"], "done")
        b = bodies(self.flows, r)
        self.assertIn("你好呀，你在干嘛", b["input"])                   # the ear's own words
        self.assertEqual(b["output"], "你好呀")
        # the system input in effect for this turn (persona + 情景 / 纪要 context), the same text as the session's
        # instructions; the body table stores it once, so carrying it on every turn costs no space
        session = self.flows("call.realtime.session")[-1]
        self.assertTrue(b["system"])
        self.assertEqual(b["system"], bodies(self.flows, session)["system"])
        self.assertEqual(r["body"]["system"], session["body"]["system"])        # one body row, shared
        gates = self.flows("gate.realtime")
        self.assertEqual([(g["extra"]["verdict"], g["trace"]) for g in gates], [("play", r["trace"])])

    async def test_turn_system_follows_instruction_updates(self):
        ws = await self.open_ready()
        self.heard("你好呀，你在干嘛")
        await self.play_one(ws, "r1")
        first = bodies(self.flows, self.flows("call.realtime.turn")[-1])["system"]
        self.hub._instructions = lambda: "新的说明：情景 X，纪要 Y"          # e.g. a fresh mind context block
        self.hub._send_instructions("test")
        self.t[0] += 5
        self.heard("现在呢")
        await self.play_one(ws, "r2")
        second = bodies(self.flows, self.flows("call.realtime.turn")[-1])["system"]
        self.assertNotEqual(first, second)
        self.assertEqual(second, "新的说明：情景 X，纪要 Y")

    async def test_task_failed_records_svc_error(self):
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(KeyError("gone"))
        with mock.patch.object(rl, "log", lambda *a, **k: None):
            rl.RealtimeHub._done(SimpleNamespace(_bg=set()), fut)
        errs = self.flows("svc.error")
        self.assertEqual([(r["extra"]["where"], r["err"]) for r in errs], [("realtime.task", "KeyError: 'gone'")])

    async def test_run_log_failure_never_stops_the_voice_loop(self):
        # I4: the record builders raising (gate / turn: _heard_in, turn: cost, search: search_count) -> the reply
        # still plays, its realtime_turns row is written, the session stays open; a raising emit in context / hush
        # -> session.update and the hush still go out; each failure said once per helper on the console
        said = []
        boom = mock.Mock(side_effect=RuntimeError("record builder broke"))
        ws = await self.open_ready()
        with mock.patch.object(self.hub, "_heard_in", boom), mock.patch.object(ear_log, "search_count", boom), \
                mock.patch.object(ear_log, "cost", boom), \
                mock.patch.object(rl, "log", lambda *a, **k: said.append(a[0] if a else "")), \
                mock.patch.object(rl, "_LOG_FAILED", set()):
            sid = await self.play_one(ws, "r1")
            self.assertEqual(self.hub.state, "open")
            self.assertTrue(sid)
            self.assertGreater(len(self.link.frames()), 0)              # the reply's audio reached the robot
            self.assertEqual(len(self.recs.turns), 1)                    # and its realtime_turns row was written
            await self.play_one(ws, "r2")
            self.assertEqual(len(self.recs.turns), 2)
            with mock.patch.object(ear_log, "emit", boom):
                n = len([m for m in ws.sent if m.get("type") == "session.update"])
                self.hub._send_instructions("test")
                await asyncio.sleep(0.05)
                self.assertEqual(len([m for m in ws.sent if m.get("type") == "session.update"]), n + 1)
                self.hub._hush("闭嘴", trace="t1")
                self.assertEqual(self.hub.stats["hush"], 1)
        failed = [t for t in said if "run log" in str(t)]
        self.assertEqual(len(failed), len(set(failed)))                  # once per helper
        for name in ("_log_gate", "_log_turn", "_log_search", "context", "hush"):
            self.assertTrue(any(f"run log {name} " in t for t in failed), (name, failed))

    async def test_gate_mute_logged(self):
        ws = await self.open_ready()
        await trl.GateTest.reply(self, ws, "r1", "嗯。")
        gates = self.flows("gate.realtime")
        self.assertEqual(len(gates), 1)
        self.assertTrue(gates[0]["extra"]["verdict"].startswith("muted_"))
        self.assertTrue(gates[0]["extra"]["reason"])
        self.assertEqual(self.flows("call.realtime.turn")[0]["extra"]["state"], "muted_quiet")
        self.assertEqual(self.flows("call.realtime.turn")[0]["trace"], gates[0]["trace"])

    async def test_instructions_dedup(self):
        await self.open_ready()
        for _ in range(3):
            self.hub._send_instructions("test")
        ctx = [r for r in self.flows("call.realtime.context") if r["extra"]["why"] == "test"]
        self.assertEqual(len(ctx), 3)                                   # three flow records ...
        digests = {r["body"]["system"] for r in ctx}
        self.assertEqual(len(digests), 1)                               # ... one body
        sha = digests.pop()
        self.assertTrue(self.flows.db.bodies(MONTH)[sha].startswith(rl.PERSONA[:20]))
        upserts = [c for c in self.flows.db.ops("bulk_write", "logbody_" + MONTH) if sha in c[2]]
        self.assertEqual(len(upserts), 1)

    async def test_session_open_and_close(self):
        await self.open_ready()
        self.hub.close("idle")
        recs = self.flows("call.realtime.session")
        self.assertEqual([r["extra"]["state"] for r in recs], ["open", "close"])
        self.assertTrue(bodies(self.flows, recs[0])["system"].startswith(rl.PERSONA[:20]))
        self.assertEqual((recs[0]["key"], recs[1]["extra"]["why"]), ("k", "idle"))
        self.assertNotIn("v-secret", json.dumps(recs, ensure_ascii=False))     # the voice id is never logged

    async def test_rescue_hush_and_search(self):
        ws = await self.open_ready()
        t = self.heard("你看到了什么？")                                # look question -> no burst here: plain reply
        await trl.RescueTest.quiet(self, ws, "r1")
        rescue = self.flows("gate.rescue")
        self.assertEqual(len(rescue), 1)
        turn = self.flows("call.realtime.turn")[0]                       # the muted 「嗯」
        self.assertEqual((rescue[0]["extra"]["outcome"], rescue[0]["trace"]), ("asked", turn["trace"]))
        self.assertEqual([h["trace"] for h in turn["extra"]["heard"]], [t])
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "别说话了。"})
        await trl.settle(20)
        hush = self.flows("trigger.hush")
        self.assertEqual(len(hush), 1)
        ws.push({"type": "response.created", "response": {"id": "r9"}})
        ws.push({"type": "response.done", "response": {"id": "r9", "status": "completed", "usage": {
            "input_tokens_details": {"text_tokens": 2000}, "plugins": {"search": {"count": 1, "strategy": "agent"}}}}})
        await trl.settle(20)
        search = self.flows("call.realtime.search")
        self.assertEqual(len(search), 1)
        self.assertEqual(search[0]["usage"], {"count": 1})
        self.assertAlmostEqual(search[0]["cost"]["amount"], prices.REALTIME_SEARCH_CNY_PER_CALL)

    # ---- R17: a turn has its own trace; what the ear heard is listed as candidates by time ----
    async def at(self, ws, when, e=None):
        self.t[0] = when
        if e:
            ws.push(e)
        await trl.settle()

    async def quiet(self, ws, rid):
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio_transcript.done", "transcript": "嗯。"})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
        await trl.settle(20)

    def turn_heard(self, i):
        return [(h["text"], h["trace"]) for h in self.flows("call.realtime.turn")[i]["extra"]["heard"]]

    def b_input(self, i):
        return bodies(self.flows, self.flows("call.realtime.turn")[i])["input"] or ""

    def assert_own_traces(self, heard_traces):
        """Each turn's gate and record share one trace of its own, never a heard.asr trace."""
        turns, gates = self.flows("call.realtime.turn"), self.flows("gate.realtime")
        self.assertEqual([g["trace"] for g in gates], [r["trace"] for r in turns])
        self.assertEqual(len({r["trace"] for r in turns}), len(turns))
        self.assertFalse({r["trace"] for r in turns} & set(heard_traces))

    async def test_two_sentences_list_their_own_heard(self):
        # A's ASR comes in after its turn's VAD end; B starts 1 s later. Window = [start - 0.3 s, record written].
        ws = await self.open_ready()
        await self.at(ws, 1000.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1001.2
        ta = self.heard("从农民到进城打工")
        await self.quiet(ws, "rA")                                       # A's record at 1001.2: [999.7, 1001.2]
        await self.at(ws, 1002.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1003.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1003.3
        tb = self.heard("他们还没有学会如何成为")
        await self.quiet(ws, "rB")                                       # B's record at 1003.3: [1001.7, 1003.3]
        self.assertEqual(self.turn_heard(0), [("从农民到进城打工", ta)])
        self.assertEqual(self.turn_heard(1), [("他们还没有学会如何成为", tb)])
        self.assertNotIn("从农民到进城打工", self.b_input(1))
        self.assertIn("他们还没有学会如何成为", self.b_input(1))
        self.assert_own_traces([ta, tb])
        self.assertEqual([g["extra"]["heard"] and g["extra"]["heard"][0]["text"] for g in self.flows("gate.realtime")],
                         ["从农民到进城打工", "他们还没有学会如何成为"])

    async def test_window_rule_is_exact(self):
        # a sentence 0.29 s before speech_started is listed, one 0.31 s before is not, and so is one after the record
        ws = await self.open_ready()
        self.t[0] = 999.69
        self.heard("早了一点点")
        self.t[0] = 999.71
        tin = self.heard("刚好算进去")
        await self.at(ws, 1000.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_stopped"})
        await self.quiet(ws, "r1")
        self.t[0] = 1001.5
        self.heard("记录写完之后才到")
        self.assertEqual(self.turn_heard(0), [("刚好算进去", tin)])

    async def test_cold_open_turn_starts_at_the_ear_onset(self):
        # fix round 2 / R17: the session was closed; the onset that opens it starts the first segment, so the opening
        # sentence (recognised before the late burst speech_started) is listed; the turn has the cold trace
        self.hub.close("idle")
        self.t[0] = 999.5
        self.speak(3)                                                    # the ear's onset: opens the session
        self.assertEqual(self.hub.state, "connecting")
        cold = self.hub._cold["trace"]
        self.t[0] = 999.8
        self.speak(1, speaking=False)
        await trl.settle(20)
        ws = self.wss[-1]
        ws.push({"type": "session.updated", "session": {}})
        await trl.settle()
        self.t[0] = 1000.0
        t = self.heard("你好呀")
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1001.3, {"type": "input_audio_buffer.speech_stopped"})
        await self.quiet(ws, "r1")
        turn = self.flows("call.realtime.turn")[-1]
        self.assertEqual(turn["trace"], cold)
        self.assertEqual(self.turn_heard(-1), [("你好呀", t)])
        self.assertIn("你好呀", self.b_input(-1))
        self.assertIsNone(self.hub._cold)                                # used by that one segment only
        self.assert_own_traces([t])
        # the next segment starts at its own speech_started again
        self.t[0] = 1010.0
        self.heard("隔了很久的一句")
        await self.at(ws, 1020.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1021.0, {"type": "input_audio_buffer.speech_stopped"})
        await self.quiet(ws, "r2")
        self.assertEqual(self.turn_heard(-1), [])

    async def cold_open(self, onset):
        """A closed session opened by the ear's onset at `onset`; returns (ws, the cold trace)."""
        self.hub.close("idle")
        self.t[0] = onset
        self.speak(3)
        cold = self.hub._cold["trace"]
        self.t[0] = onset + 0.3
        self.speak(1, speaking=False)
        await trl.settle(20)
        ws = self.wss[-1]
        ws.push({"type": "session.updated", "session": {}})
        await trl.settle()
        return ws, cold

    async def test_stale_cold_onset_does_not_reach_a_late_turn(self):
        # R18 (review N1): the onset that opened the session was noise the server never turned into a segment. 40 s
        # later a sentence keeps its own trace for what it sets off; the turn at 1100 starts at its own speech_started
        ws, cold = await self.cold_open(999.5)
        self.hub.burst = trl.LookAnswerTest.Burst()
        self.t[0] = 1040.0
        ta = self.heard("这是什么？")                                     # a look question: look wait + burst ask
        self.assertEqual(self.hub._look_wait["trace"], ta)
        self.assertEqual(self.hub._trace_now(ta), ta)
        self.assertIsNone(self.hub._cold)
        self.t[0] = 1050.0
        th = self.heard("别说话了。")
        self.assertEqual([r["trace"] for r in self.flows("trigger.hush")], [th])
        await self.at(ws, 1100.0, {"type": "input_audio_buffer.speech_started"})
        self.assertEqual(self.hub._seg["start"], 1100.0)
        await self.at(ws, 1101.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1101.3
        tb = self.heard("今天天气怎么样")
        await self.quiet(ws, "rB")
        turn = self.flows("call.realtime.turn")[-1]
        self.assertNotEqual(turn["trace"], cold)
        self.assertEqual(self.turn_heard(-1), [("今天天气怎么样", tb)])
        self.assertNotIn("这是什么", self.b_input(-1))
        self.assert_own_traces([ta, th, tb])

    async def test_cold_onset_is_valid_for_five_seconds(self):
        # R18 edge: speech_started 4.9 s after the onset still uses it; 5.1 s after, the segment starts on its own
        for i, (gap, used) in enumerate(((4.9, True), (5.1, False))):
            base = 1000.0 + 100 * i
            ws, cold = await self.cold_open(base)
            self.t[0] = base + 0.5
            t = self.heard("你好呀")
            self.assertEqual(self.hub._trace_now(t), cold)               # inside the limit: the turn being opened
            await self.at(ws, base + gap, {"type": "input_audio_buffer.speech_started"})
            self.assertEqual(self.hub._seg["trace"] == cold, used, gap)
            self.assertEqual(self.hub._seg["start"], base if used else base + gap, gap)
            self.assertIsNone(self.hub._cold)
            await self.at(ws, base + gap + 0.5, {"type": "input_audio_buffer.speech_stopped"})
            await self.quiet(ws, f"r{i}")
            self.assertEqual(self.turn_heard(-1), [("你好呀", t)] if used else [], gap)

    async def test_stale_cold_onset_is_dropped_without_speech_started(self):
        # R18: speech_stopped alone (no speech_started seen) and _trace_now ignore an onset older than 5 s
        ws, cold = await self.cold_open(999.5)
        self.t[0] = 1004.4
        self.assertEqual(self.hub._trace_now("own"), cold)
        self.t[0] = 1004.6
        self.assertEqual(self.hub._trace_now("own"), "own")
        ws, cold = await self.cold_open(1100.0)
        await self.at(ws, 1105.2, {"type": "input_audio_buffer.speech_stopped"})
        self.assertEqual(self.hub._heard_turn["start"], 1105.2)
        self.assertNotEqual(self.hub._heard_turn["trace"], cold)

    async def test_no_onset_late_speech_started_is_not_listed(self):
        # round 2's no-onset repro under R17: the session was already open (no cold onset), the ear's ASR came 1 s
        # before speech_started: outside the window, so it is not a candidate (the documented limit)
        ws = await self.open_ready()
        self.t[0] = 1000.0
        self.heard("你好呀")
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1001.3, {"type": "input_audio_buffer.speech_stopped"})
        await self.quiet(ws, "r1")
        self.assertEqual(self.turn_heard(-1), [])

    # the reviewer's leak_check.py scenarios (round 3), expectations by the window rule
    async def test_leak_stale_onset_noise_long_before(self):
        ws = await self.open_ready()
        self.t[0] = 1010.0; self.speak(2); self.t[0] = 1010.3; self.speak(1, speaking=False)   # noqa: E702
        self.t[0] = 1010.5; tn = self.heard("嗯")                                                # noqa: E702
        await self.at(ws, 1060.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1061.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1061.3; tb = self.heard("今天天气怎么样")                                     # noqa: E702
        await self.quiet(ws, "rB")
        self.assertEqual(self.turn_heard(-1), [("今天天气怎么样", tb)])
        self.assertNotIn("嗯", self.b_input(-1))
        self.assert_own_traces([tn, tb])

    async def test_leak_stale_onset_after_a_turn(self):
        ws = await self.open_ready()
        await self.at(ws, 1000.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1001.1; self.speak(2); self.t[0] = 1001.2; self.speak(1, speaking=False)   # noqa: E702
        self.t[0] = 1001.4; ta = self.heard("从农民到进城打工")                                   # noqa: E702
        await self.quiet(ws, "rA")
        await self.at(ws, 1003.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1004.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1004.3; tb = self.heard("他们还没有学会如何成为")                             # noqa: E702
        await self.quiet(ws, "rB")
        self.assertEqual(self.turn_heard(0), [("从农民到进城打工", ta)])
        self.assertEqual(self.turn_heard(1), [("他们还没有学会如何成为", tb)])
        self.assert_own_traces([ta, tb])

    async def test_leak_db_off_lists_sentences_by_text(self):
        ws = await self.open_ready()
        await self.at(ws, 1000.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1001.2; self.hub.local_heard("从农民到进城打工", trace=None)                 # noqa: E702
        await self.quiet(ws, "rA")
        await self.at(ws, 1002.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1003.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1003.3; self.hub.local_heard("他们还没有学会如何成为", trace=None)           # noqa: E702
        await self.quiet(ws, "rB")
        self.assertEqual(self.turn_heard(0), [("从农民到进城打工", None)])
        self.assertEqual(self.turn_heard(1), [("他们还没有学会如何成为", None)])
        self.assertNotIn("从农民到进城打工", self.b_input(1))

    async def test_leak_sentence_while_sounding_outside_the_window(self):
        # A spoken while hello sounds (no audio up, no server turn), its ASR 0.6 s before B's speech_started
        ws = await self.open_ready()
        self.t[0] = 999.0; self.hub.feed(b"\x01\x00" * 960, True, True)                          # noqa: E702
        self.t[0] = 1000.0; self.hub.feed(b"\x01\x00" * 960, False, True)                        # noqa: E702
        self.t[0] = 1000.4; ta = self.heard("别说了")                                             # noqa: E702
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1002.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1002.3; tb = self.heard("你看这是什么")                                       # noqa: E702
        await self.quiet(ws, "rB")
        self.assertEqual(self.turn_heard(-1), [("你看这是什么", tb)])
        self.assertNotIn("别说了", self.b_input(-1))
        self.assert_own_traces([ta, tb])

    async def test_leak_sentence_while_sounding_inside_the_window_is_listed(self):
        # the same, but its ASR lands 0.2 s before B's speech_started: by the rule it is a candidate (a neighbour)
        ws = await self.open_ready()
        self.t[0] = 999.0; self.hub.feed(b"\x01\x00" * 960, True, True)                          # noqa: E702
        self.t[0] = 1000.0; self.hub.feed(b"\x01\x00" * 960, False, True)                        # noqa: E702
        self.t[0] = 1000.8; ta = self.heard("别说了")                                             # noqa: E702
        await self.at(ws, 1001.0, {"type": "input_audio_buffer.speech_started"})
        await self.at(ws, 1002.0, {"type": "input_audio_buffer.speech_stopped"})
        self.t[0] = 1002.3; tb = self.heard("你看这是什么")                                       # noqa: E702
        await self.quiet(ws, "rB")
        self.assertEqual(self.turn_heard(-1), [("别说了", ta), ("你看这是什么", tb)])

    async def test_judge_look_wait_carries_the_turn_trace(self):
        # R16 / R17: a look question found by the gate (the burst was not there when the words came): the look wait,
        # the filler and the gate all carry the turn's own trace
        self.hub.fillers = [("我看看哈", trl.pcm24(100))]
        ws = await self.open_ready()
        t = self.heard("这是什么？")                                     # no burst yet: no look wait
        self.assertIsNone(self.hub._look_wait)
        self.hub.burst = trl.LookAnswerTest.Burst()
        await trl.LookAnswerTest.instant(self, ws, "r1", "哇，是个黑色的圆球球！")
        gate = self.flows("gate.realtime")
        self.assertEqual([g["extra"]["verdict"] for g in gate], ["muted_wait_look"])
        own = gate[0]["trace"]
        self.assertNotEqual(own, t)
        self.assertEqual(self.hub._look_wait["trace"], own)
        self.assertEqual([r["trace"] for r in self.flows("filler.play")], [own])
        self.assertEqual(self.flows("call.realtime.turn")[0]["trace"], own)

    async def test_filler_play_logged(self):
        self.hub.burst = trl.LookAnswerTest.Burst()
        self.hub.fillers = [("我看看哈", trl.pcm24(100))]
        await self.open_ready()
        t = self.heard("这是什么？")
        await trl.settle(20)
        rec = self.flows("filler.play")
        self.assertEqual(len(rec), 1)
        self.assertEqual((rec[0]["trace"], rec[0]["sum"]), (t, "我看看哈"))
        self.assertEqual(self.flows("call.realtime.turn"), [])         # the filler is not a model call


# ---------------------------------------------------------------- ear_server (ear venv)

class FakeVisionWS:
    def __init__(self, messages):
        self.inbox, self.sent, self.closed = list(messages), [], None

    async def recv(self):
        if not self.inbox:
            raise ConnectionError("closed")
        return self.inbox.pop(0)

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class FakeHearingWS:
    def __init__(self):
        self.q, self.sent, self.close_code, self.remote_address = asyncio.Queue(), [], None, ("10.0.0.8", 1)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            self.close_code = 1000
            raise StopAsyncIteration
        return m

    async def send(self, m):
        self.sent.append(m)


@unittest.skipUnless(HAVE_EAR, "needs the ear's deps (numpy, sherpa_onnx)")
class EarServerLogTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flows = capture()

    async def asyncTearDown(self):
        stop_log()

    async def test_link_open_close_logged(self):
        ws = FakeVisionWS([json.dumps({"type": "hello", "source": "stackchan-7c4f", "device": {"boot_id": "b00t"}}),
                           json.dumps({"type": "vision", "event": "m12_online", "seq": 1}),
                           json.dumps({"type": "vision", "event": "face_found", "seq": 2, "face": 3})])
        link = await ear_server.serve_vision(ws, ("10.0.0.7", 5555), 4, None, None)
        self.assertEqual(link.events, 2)
        opens, closes = self.flows("link.open"), self.flows("link.close")
        self.assertEqual((len(opens), len(closes)), (1, 1))
        self.assertEqual((opens[0]["extra"]["link"], closes[0]["extra"]["link"]), ("vision", "vision"))
        self.assertEqual((opens[0]["extra"]["conn"], opens[0]["extra"]["peer"]), ("v4", "10.0.0.7:5555"))
        c = closes[0]["extra"]
        self.assertEqual((c["boot"], c["why"], c["counts"]["events"]), ("b00t", "closed", 2))
        self.assertIsInstance(closes[0]["ms"], int)
        state = self.flows("robot.state")
        self.assertEqual([(r["extra"]["part"], r["extra"]["after"]) for r in state], [("m12", "online")])

    def hearing_ctx(self, executor):
        import mouth_mode
        import tingwu_switch
        return SimpleNamespace(models=SimpleNamespace(vad_cfg=None, speakers={}, lock=__import__("threading").Lock(),
                                                      denoise_cfg=None, new_denoiser=lambda: None),
                               ex=executor, rec_root=str(temp_dir()), database=None, tingwu_keys=None,
                               tw_switch=tingwu_switch.SwitchReader(lambda: None), write_tingwu_status=lambda: None,
                               mouth=mouth_mode.ModeReader(lambda: None), mode_hub=None, rt_hub=None, ears=set(),
                               active=set())

    async def test_hearing_link_is_paired_even_when_the_ear_fails(self):
        # R16: link.open now happens inside the try, so an Ear that cannot be built still gets its link.close
        ctx = self.hearing_ctx(None)
        ws = FakeHearingWS()
        ctx.active.add(ws)
        with mock.patch.object(ear_server, "Ear", side_effect=RuntimeError("no models")):
            with self.assertRaises(RuntimeError):
                await ear_server.serve_hearing(ws, ("10.0.0.8", 1), 7, ctx)
        self.assertEqual(ctx.active, set())
        opens, closes = self.flows("link.open"), self.flows("link.close")
        self.assertEqual((len(opens), len(closes)), (1, 1))
        self.assertEqual((closes[0]["extra"]["conn"], closes[0]["extra"]["why"]), ("c7", "error"))

    async def test_a_dropped_hearing_link_logs_its_close_code(self):
        # fix round 2: a Wi-Fi drop ends the socket with an error (1006); link.close says "closed 1006", not "error"
        executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(1)
        ctx = self.hearing_ctx(executor)

        class Dropped(FakeHearingWS):
            async def __anext__(self):
                m = await self.q.get()
                if m is None:
                    self.close_code = 1006
                    raise ConnectionResetError("no close frame")
                return m
        ws = Dropped()
        with mock.patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)):
            task = asyncio.create_task(ear_server.serve_hearing(ws, ("10.0.0.8", 1), 3, ctx))
            await asyncio.sleep(0.02)
            ws.q.put_nowait(None)
            with self.assertRaises(ConnectionResetError):
                await task
        executor.shutdown()
        self.assertEqual(self.flows("link.close")[0]["extra"]["why"], "closed 1006")

    async def test_hearing_reconnect_overlap_keeps_hearing_on(self):
        executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(1)
        ctx = self.hearing_ctx(executor)
        declare = json.dumps({"type": "audio", "format": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60,
                              "source": "stackchan-7c4f"})
        with mock.patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)):
            ws1, ws2 = FakeHearingWS(), FakeHearingWS()
            c1 = asyncio.create_task(ear_server.serve_hearing(ws1, ("10.0.0.8", 1), 1, ctx))
            ws1.q.put_nowait(declare)
            await asyncio.sleep(0.05)
            c2 = asyncio.create_task(ear_server.serve_hearing(ws2, ("10.0.0.8", 2), 2, ctx))
            ws2.q.put_nowait(declare)
            await asyncio.sleep(0.05)
            ws1.q.put_nowait(None)                                     # the old half-open socket finally closes
            await c1
            self.assertEqual([r["extra"]["after"] for r in self.flows("robot.state")], ["on"])
            ws2.q.put_nowait(None)
            await c2
        executor.shutdown()
        state = [(r["extra"]["part"], r["extra"]["device"], r["extra"]["after"]) for r in self.flows("robot.state")]
        self.assertEqual(state, [("hearing", "stackchan-7c4f", "on"), ("hearing", "stackchan-7c4f", "off")])
        self.assertEqual(len(self.flows("link.close")), 2)

    def test_refused_link_name_is_the_routed_link_never_the_path(self):
        self.assertEqual([ear_server.link_of(p) for p in ("/mouth?token=x", "/signal", "/vision", "/", "",
                                                          "/sk-secretpath?token=y")],
                         ["mouth", "signal", "vision", "hearing", "hearing", "hearing"])

    async def test_tingwu_cost(self):
        class Stream:
            def __init__(self, key, app, sentence, final, error):
                self.data_id = "d1"

            def start(self):
                pass

            def close(self):
                pass

            def audio_seconds(self):
                return 60.0
        executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(1)
        models = SimpleNamespace(vad_cfg=None, speakers={}, lock=__import__("threading").Lock(), denoise_cfg=None,
                                 new_denoiser=lambda: None)
        with mock.patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)), \
                mock.patch.object(ear_server.tingwu_rt, "TingwuStream", Stream):
            ear = ear_server.Ear(models, None, executor)
            ear.session = "20261116/120000-stackchan-x-c1"
            ear.tingwu_keys = ("sk-tingwukey0000", "app")
            ear.start_tingwu()
            ear.stop_tingwu()
            await ear.close()
        executor.shutdown()
        recs = self.flows("call.tingwu")
        self.assertEqual([r["extra"]["state"] for r in recs], ["start", "stop"])
        self.assertEqual(recs[0]["trace"], recs[1]["trace"])
        stop = recs[1]
        self.assertEqual(stop["usage"], {"audio_s": 60.0})
        self.assertEqual(stop["cost"]["cur"], "CNY")
        p = prices.TINGWU_CNY_PER_HOUR
        if p is None:
            self.assertIsNone(stop["cost"]["amount"])
        else:
            self.assertAlmostEqual(stop["cost"]["amount"], p / 60)
        self.assertEqual(stop["key"], "sk-ting")
        self.assertNotIn("cost", recs[0])


# ---------------------------------------------------------------- SIGTERM on the ear's own loop

STOP_SCRIPT = textwrap.dedent("""
    import asyncio, os, signal, sys
    sys.path[:0] = [{ear!r}, {common!r}]
    import ear_log, hello_log

    async def main(hang):
        loop = asyncio.get_running_loop()
        stopper = ear_log.Stopper(loop, grace_s=0.5)
        ear_log.start({{}}, {{"port": 1}}, spool_dir={spool!r}, code_version="test")
        ear_log.emit("link.open", extra={{"link": "hearing"}})
        loop.call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)
        try:
            await stopper.wait()
        finally:
            print("cleanup", flush=True)
            if hang:
                await asyncio.sleep(30)        # a stuck shutdown: the grace timer ends it
        await stopper.finish()
        print("finished", flush=True)

    asyncio.run(main(sys.argv[1] == "hang"))
""")


class SigtermTest(unittest.TestCase):
    def run_script(self, mode):
        spool = temp_dir()
        script = Path(tempfile.mkdtemp()) / "stop.py"
        script.write_text(STOP_SCRIPT.format(ear=str(HERE.parent / "server" / "ear"),
                                             common=str(HERE.parent / "server" / "common"), spool=str(spool)))
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-B", str(script), mode], capture_output=True, text=True, timeout=20)
        took = time.monotonic() - t0
        recs = []
        for f in spool.glob("ear-*.jsonl"):
            recs += [json.loads(line)["flow"] for line in f.read_text().splitlines() if line.strip()]
        return p, took, recs

    def test_sigterm_stops_the_ear_cleanly(self):
        p, took, recs = self.run_script("clean")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("cleanup", p.stdout)
        self.assertIn("finished", p.stdout)
        self.assertLess(took, 8)
        kinds = [r["kind"] for r in recs]
        self.assertEqual(kinds[0], "svc.start")
        self.assertIn("link.open", kinds)
        stops = [r for r in recs if r["kind"] == "svc.stop"]
        self.assertEqual([r["extra"]["reason"] for r in stops], ["sigterm"])

    def test_a_stuck_shutdown_still_exits_and_records_the_stop(self):
        p, took, recs = self.run_script("hang")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("cleanup", p.stdout)
        self.assertNotIn("finished", p.stdout)
        self.assertLess(took, 10)
        self.assertEqual([r["extra"]["reason"] for r in recs if r["kind"] == "svc.stop"], ["sigterm"])


if __name__ == "__main__":
    unittest.main()
