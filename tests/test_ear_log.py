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

    def test_tingwu_cost_follows_the_price_table(self):
        c = ear_log.tingwu_cost(60)
        self.assertEqual((c["cur"], c["est"]), ("CNY", True))
        self.assertAlmostEqual(c["amount"], prices.TINGWU_CNY_PER_HOUR / 60)
        self.assertIn("2026-11-16", c["basis"])
        with mock.patch.object(prices, "TINGWU_CNY_PER_HOUR", None):
            self.assertIsNone(ear_log.tingwu_cost(60)["amount"])

    def test_prices_have_a_basis_each(self):
        for name in ("TINGWU_CNY_PER_HOUR", "REALTIME_SEARCH_CNY_PER_CALL", "TTS_CNY_PER_10K_CHARS"):
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
        self.assertEqual(r["trace"], t)
        self.assertEqual(r["model"], rl.MODEL)
        self.assertEqual(r["usage"], usage)
        self.assertEqual(r["ref"]["tid"], self.recs.turns[-1]["tid"])
        self.assertEqual(r["extra"]["state"], "done")
        b = bodies(self.flows, r)
        self.assertIn("你好呀，你在干嘛", b["input"])                   # the ear's own words
        self.assertEqual(b["output"], "你好呀")
        gates = self.flows("gate.realtime")
        self.assertEqual([(g["extra"]["verdict"], g["trace"]) for g in gates], [("play", t)])

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
        self.assertEqual((rescue[0]["extra"]["outcome"], rescue[0]["trace"]), ("asked", t))
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
