"""ear's /mouth link: the ear terminates the robot's /mouth itself and keeps a link to soul-tts's mouth
(127.0.0.1:52751) behind it (Task A0, 2026-11-14). Needs websockets (run in .private/venv-ear):

    .private/venv-ear/bin/python -B -m unittest discover -s tests -p 'test_mouth_link.py' -v
"""
import asyncio
import contextlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
try:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosed
    HAVE_WS = True
except ImportError:
    HAVE_WS = False

ROBOT_HELLO = {"type": "hello", "device_id": "d", "boot_id": "b1", "codecs": ["opus"], "sample_rate": 24000}


@unittest.skipUnless(HAVE_WS, "needs websockets")
class MouthLinkTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import mouth_link
        self.mouth_link = mouth_link
        self.logs = []
        mouth_link.log = lambda *a, **k: self.logs.append(" ".join(str(x) for x in a))
        self.connect_override = None
        self.seen_headers, self.upstream_text, self.up_closed = [], [], asyncio.Event()
        self.up_hellos, self.up_hello_seen = 0, asyncio.Event()
        self.up_ws, self.up, self.up_port = None, None, 0
        await self.start_upstream()
        self.up_url = f"ws://127.0.0.1:{self.up_port}/mouth"
        self.results, self.links, self.tasks = [], [], []

        async def front(ws):                                   # the ear's /mouth branch, as in ear_server
            link = self.make_link(ws)
            self.links.append(link)
            self.tasks.append(asyncio.current_task())
            self.results.append(await link.run())
        self.front = await serve(front, "127.0.0.1", 0)
        self.front_url = f"ws://127.0.0.1:{self.front.sockets[0].getsockname()[1]}/mouth"

    def make_link(self, ws):
        return self.mouth_link.MouthLink(ws, upstream_url=self.up_url, token="device-token", retry_s=0.3,
                                         connect=self.connect_override)

    def _skip(self, m):
        self.skipped.append(m)

    async def asyncTearDown(self):
        self.front.close()
        await self.front.wait_closed()
        await self.stop_upstream()

    # ---- fake soul-tts: answers the robot's hello like soul_tts/mouth.py, records what it gets ----
    async def _upstream(self, ws):
        self.seen_headers.append(ws.request.headers.get("Authorization"))
        self.up_ws = ws
        try:
            async for m in ws:                                  # (the 4000 test closes it from here)
                if isinstance(m, str):
                    self.upstream_text.append(m)
                    if json.loads(m).get("type") == "hello":
                        self.up_hellos += 1
                        self.up_hello_seen.set()
                        await ws.send(json.dumps({"type": "hello", "codec": "opus", "sample_rate": 24000, "frame_ms": 60}))
        except ConnectionClosed:
            pass
        finally:
            if self.up_ws is ws:
                self.up_ws = None
            self.up_closed.set()

    async def start_upstream(self):
        self.up_hello_seen.clear()
        self.up = await serve(self._upstream, "127.0.0.1", self.up_port)   # same port after a restart
        self.up_port = self.up.sockets[0].getsockname()[1]

    async def stop_upstream(self):
        if self.up is not None:
            self.up.close()                                     # closes live links too, like a soul-tts restart
            await self.up.wait_closed()
            self.up = None
            await asyncio.sleep(0.05)

    async def wait_upstream_hello(self, timeout=3):
        await asyncio.wait_for(self.up_hello_seen.wait(), timeout)
        for _ in range(100):                                   # the link marks itself up right after hello
            if self.links and self.links[-1].upstream_up:
                return
            await asyncio.sleep(0.01)

    async def upstream_send(self, message):
        for _ in range(100):
            if self.up_ws is not None:
                break
            await asyncio.sleep(0.02)
        await self.up_ws.send(message)

    # ---- the robot ----
    @contextlib.asynccontextmanager
    async def robot(self):
        self.skipped = []
        async with connect(self.front_url) as ws:
            yield ws

    async def handshake(self, ws):
        await ws.send(json.dumps(ROBOT_HELLO))
        first = json.loads(await asyncio.wait_for(ws.recv(), 2))
        self.assertEqual(first["type"], "hello")
        if self.up is not None:
            await self.wait_upstream_hello()

    async def recv_type(self, ws, kind, timeout=2):
        async def go():
            while True:
                m = await ws.recv()
                if isinstance(m, str) and json.loads(m).get("type") == kind:
                    return json.loads(m)
                self._skip(m)
        return await asyncio.wait_for(go(), timeout)

    async def recv_binary(self, ws, timeout=2):
        async def go():
            while True:
                m = await ws.recv()
                if isinstance(m, bytes):
                    return m
                self._skip(m)
        return await asyncio.wait_for(go(), timeout)

    async def wait_result(self, timeout=2):
        for _ in range(int(timeout / 0.02)):
            if self.results:
                return self.results[-1]
            await asyncio.sleep(0.02)
        self.fail("link did not finish")

    # ---- tests ----
    async def test_ear_answers_hello_itself(self):
        async with self.robot() as ws:
            await ws.send(json.dumps({"type": "hello", "device_id": "d", "boot_id": "b1", "codecs": ["opus"], "sample_rate": 24000}))
            first = json.loads(await ws.recv())
            self.assertEqual((first["type"], first["codec"], first["frame_ms"], first["sample_rate"]), ("hello", "opus", 60, 24000))
            await self.wait_upstream_hello()
            self.assertEqual(self.links[-1].boot, "b1")
            self.assertTrue(self.links[-1].upstream_up)
        self.assertEqual(json.loads(self.upstream_text[0])["boot_id"], "b1")      # the robot's hello reached soul-tts
        self.assertEqual(self.upstream_text[0], json.dumps(ROBOT_HELLO))          # verbatim
        self.assertEqual(self.seen_headers, ["Bearer device-token"])

    async def test_ear_answers_hello_with_soul_tts_down(self):
        await self.stop_upstream()
        async with self.robot() as ws:
            await ws.send(json.dumps(ROBOT_HELLO))
            first = json.loads(await asyncio.wait_for(ws.recv(), 1))
            self.assertEqual((first["codec"], first["frame_ms"], first["sample_rate"]), ("opus", 60, 24000))

    async def test_soul_tts_hello_is_swallowed_frames_pass_both_ways(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 1, "codec": "opus", "frame_ms": 60}))
            frame = b"SPK1" + b"\x01\x00\x00\x00" + b"\x00" * 8 + b"\x02" * 30
            await self.upstream_send(frame)
            await self.upstream_send(json.dumps({"type": "tts.end", "stream": 1, "frames": 1}))
            self.assertEqual((await self.recv_type(ws, "tts.start"))["stream"], 1)
            self.assertEqual(await self.recv_binary(ws), frame)
            await self.recv_type(ws, "tts.end")
            self.assertEqual(self.skipped, [])                                  # no second hello reached the robot
            await ws.send(json.dumps({"type": "tts.progress", "stream": 1, "played_ms": 60}))
        await asyncio.wait_for(self.up_closed.wait(), 2)
        self.assertIn('"tts.progress"', "".join(self.upstream_text))

    async def test_upstream_restart_mid_stream(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 7}))
            await self.recv_type(ws, "tts.start")
            await self.stop_upstream()
            stop = await self.recv_type(ws, "tts.stop", timeout=2)
            self.assertEqual(stop["stream"], 7)
            self.assertFalse(self.links[-1].upstream_up)
            await self.start_upstream()
            await self.wait_upstream_hello(timeout=8)                              # hello replayed after reconnect
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 8}))
            await self.upstream_send(b"\x08\x00\x00\x00" + b"\x01" * 40)           # one binary audio frame
            self.assertEqual((await self.recv_type(ws, "tts.start"))["stream"], 8)
            self.assertIsInstance(await self.recv_binary(ws), bytes)
            self.assertIsNone(ws.close_code)
            self.assertEqual(self.up_hellos, 2)
            self.assertEqual(json.loads(self.upstream_text[-1])["boot_id"], "b1")
            self.assertNotIn("hello", "".join(m for m in self.skipped if isinstance(m, str)))
            await ws.send(json.dumps({"type": "tts.done", "stream": 8}))         # reports reach the new upstream
            for _ in range(100):
                if '"tts.done"' in "".join(self.upstream_text):
                    break
                await asyncio.sleep(0.02)
            self.assertIn('"tts.done"', "".join(self.upstream_text))

    async def test_no_stop_for_a_finished_stream(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 4}))
            await self.upstream_send(json.dumps({"type": "tts.end", "stream": 4}))   # robot drains it on its own
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 5}))
            await self.recv_type(ws, "tts.start")
            await self.recv_type(ws, "tts.start")
            await ws.send(json.dumps({"type": "tts.done", "stream": 5}))           # robot finished stream 5
            await asyncio.sleep(0.1)
            await self.stop_upstream()
            with self.assertRaises(asyncio.TimeoutError):
                await self.recv_type(ws, "tts.stop", timeout=0.6)
            self.assertIsNone(ws.close_code)

    async def test_no_upstream_keeps_robot(self):
        await self.stop_upstream()
        async with self.robot() as ws:
            await self.handshake(ws)
            await asyncio.sleep(1.5)
            self.assertIsNone(ws.close_code)
            self.assertFalse(self.links[-1].upstream_up)
            await self.start_upstream()                                        # soul-tts comes up later
            await self.wait_upstream_hello(timeout=3)
            self.assertTrue(self.links[-1].upstream_up)

    async def test_robot_close_closes_upstream(self):
        async with self.robot() as ws:
            await self.handshake(ws)
        await asyncio.wait_for(self.up_closed.wait(), 2)
        self.assertEqual(await self.wait_result(), "device_closed")

    async def test_robot_close_during_retry_stops_retrying(self):
        await self.stop_upstream()
        async with self.robot() as ws:
            await self.handshake(ws)
        self.assertEqual(await self.wait_result(), "device_closed")
        await self.start_upstream()
        await asyncio.sleep(0.8)                                               # > two retry periods
        self.assertEqual(self.seen_headers, [])                                # nothing dialled soul-tts

    async def test_reports_still_relayed(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "tts.done", "stream": 3}))
        await asyncio.wait_for(self.up_closed.wait(), 2)
        self.assertIn('"tts.done"', "".join(self.upstream_text))

    async def test_bad_first_message_closes_the_robot(self):
        async with self.robot() as ws:
            await ws.send('{"type":"tts.done","stream":1}')
            await asyncio.wait_for(ws.wait_closed(), 2)
            self.assertEqual(ws.close_code, 4400)
        self.assertEqual(await self.wait_result(), "bad_hello")
        self.assertEqual(self.seen_headers, [])

    async def test_replaced_upstream_closes_the_stale_robot_side(self):
        """soul-tts keeps one device and closes the older link with 4000: that robot side is stale."""
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.up_ws.close(4000, "replaced by new connection")
            await asyncio.wait_for(ws.wait_closed(), 2)
        self.assertEqual(await self.wait_result(), "upstream_replaced")

    async def test_cancelled_run_closes_upstream_and_leaves_no_task_errors(self):
        """The ear shutting down cancels the handler: both inner tasks end, soul-tts is let go."""
        import gc
        complaints = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: complaints.append(ctx.get("message")))
        async with self.robot() as ws:
            await self.handshake(ws)
            self.tasks[-1].cancel()                            # the handler task, as on ear shutdown
            await asyncio.wait_for(self.up_closed.wait(), 2)
        gc.collect(); await asyncio.sleep(0.05); gc.collect()
        self.assertEqual([m for m in complaints if "never retrieved" in (m or "")], [])

    async def test_a_dropped_device_leaves_no_unretrieved_task_errors(self):
        import gc
        complaints = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: complaints.append(ctx.get("message")))
        c = await connect(self.front_url)
        await self.handshake(c)
        c.transport.abort()                                    # no close frame
        self.assertEqual(await self.wait_result(), "device_closed")
        await asyncio.wait_for(self.up_closed.wait(), 2)
        gc.collect(); await asyncio.sleep(0.05); gc.collect()
        self.assertEqual([m for m in complaints if "never retrieved" in (m or "")], [])


    async def test_unexpected_upstream_error_is_logged_with_traceback(self):
        """A0 review: an unexpected exception on the soul-tts side was swallowed silently; now it is logged."""
        class Broken:
            close_code = None
            async def send(self, m):
                pass
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise RuntimeError("boom")
            async def close(self):
                pass
        async def fake_connect(*a, **k):
            return Broken()
        self.connect_override = fake_connect
        async with self.robot() as ws:
            await ws.send(json.dumps(ROBOT_HELLO))
            await asyncio.wait_for(ws.recv(), 2)
            for _ in range(100):
                if any("boom" in m for m in self.logs):
                    break
                await asyncio.sleep(0.02)
            self.assertIsNone(ws.close_code)
        text = "\n".join(self.logs)
        self.assertIn("RuntimeError: boom", text)
        self.assertIn("Traceback", text)

def report(seq, mode="talk", effects=(), by="asr", inp="forward", **st):
    return json.dumps({"type": "mode_report", "seq": seq, "mode": mode, "dir": st.get("dir"), "host": None,
                       "return_to": st.get("return_to"), "by": by, "input": inp, "effects": list(effects)})


@unittest.skipUnless(HAVE_WS, "needs websockets")
class MouthModeTest(MouthLinkTest):
    """Step 1b: hello owns the mode; the ear relays its mode_report and mode_input (digits 0 / 9) to mind and pushes
    nothing down. Every A0 test above also runs here (a robot that sends no mode messages, like 0.4.91, keeps audio
    and liveness)."""

    async def asyncSetUp(self):
        import time as _time
        self._time = _time
        self.mind_up, self.mind_input_delay, self.mind_report_status = True, 0, 200
        self.mind_calls, self.mind_threads = [], set()
        self.conn_no = 0
        await super().asyncSetUp()
        self.hub = self.mouth_link.ModeRelay(self.fake_mind, input_timeout_s=0.5, input_queue=4, report_queue=3,
                                             report_timeout_s=0.5, report_retry_s=0.1)

    async def asyncTearDown(self):
        await super().asyncTearDown()
        await self.hub.drain()
        self.hub.close()

    def make_link(self, ws):
        self.conn_no += 1
        return self.mouth_link.MouthLink(ws, upstream_url=self.up_url, token="device-token", retry_s=0.3,
                                         connect=self.connect_override, conn=self.conn_no, modes=self.hub)

    def _skip(self, m):                       # mode results are expected here, not "unexpected"
        if isinstance(m, str) and json.loads(m).get("type") == "mode_result":
            return
        self.skipped.append(m)

    # ---- fake mind: one function, records every call by path ----
    def fake_mind(self, method, path, body=None, timeout=5):
        import threading
        self.mind_calls.append((method, path, json.loads(json.dumps(body))))
        self.mind_threads.add(threading.current_thread().name)
        if not self.mind_up:
            return 503, {"error": "URLError"}
        if (method, path) == ("POST", "/mode/report"):
            st = self.mind_report_status
            return (st, {"snapshot": {}, "result": {"code": "ok", "toast": "OK"}, "effects": body["effects"]}) \
                if st == 200 else (st, {"error": "bad report"})
        if (method, path) == ("POST", "/mode/robot"):
            return 200, {"snapshot": {}}
        if (method, path) == ("POST", "/chat/heard"):
            if getattr(self, "mind_chat_delay", 0):
                self._time.sleep(self.mind_chat_delay)
            return 200, {"tid": "t1", "accepted": True, "skip": None}
        if (method, path) == ("POST", "/mode/input"):
            if self.mind_input_delay:
                self._time.sleep(self.mind_input_delay)
            return 200, {"snapshot": {}, "result": {"code": "ok", "toast": "PRESET 0"}, "effects": []}
        return 404, {"error": "not found"}

    def mind_calls_to(self, path):
        return [b for _, p, b in self.mind_calls if p == path]

    async def wait_mind(self, path, n=1, timeout=3):
        for _ in range(int(timeout / 0.02)):
            if len(self.mind_calls_to(path)) >= n:
                return self.mind_calls_to(path)
            await asyncio.sleep(0.02)
        self.fail(f"mind did not get {n} x {path}")

    async def no_message(self, ws, kind, timeout):
        with self.assertRaises(asyncio.TimeoutError):
            await self.recv_type(ws, kind, timeout=timeout)

    # ---- reports ----
    async def test_report_goes_to_mind_not_soul_tts(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(4, "crowd", ["stop_speech", "talk_leave"], by="touch", inp="select"))
            body = (await self.wait_mind("/mode/report"))[0]
            await self.no_message(ws, "mode", 0.2)                          # nothing comes back down
        self.assertEqual({k: body[k] for k in ("boot", "conn", "seq", "mode", "dir", "host", "return_to", "by", "input",
                                                "effects")},
                         {"boot": "b1", "conn": 1, "seq": 4, "mode": "crowd", "dir": None, "host": None,
                          "return_to": None, "by": "touch", "input": "select", "effects": ["stop_speech", "talk_leave"]})
        self.assertTrue(0 <= body["age_ms"] < 1000)
        self.assertNotIn("mode_report", "".join(self.upstream_text))

    async def test_reports_keep_their_order(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            for seq, mode in ((1, "idle"), (2, "crowd"), (3, "talk")):
                await ws.send(report(seq, mode))
            got = await self.wait_mind("/mode/report", n=3)
        self.assertEqual([(b["seq"], b["mode"]) for b in got], [(1, "idle"), (2, "crowd"), (3, "talk")])

    async def test_mind_away_retries_the_same_report_then_the_next(self):
        self.mind_up = False
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(1, "idle"))
            await ws.send(report(2, "crowd"))
            await self.wait_mind("/mode/report", n=3)
            self.mind_up = True
            for _ in range(150):
                ok = [b["seq"] for m, p, b in self.mind_calls if p == "/mode/report"]
                if ok and ok[-1] == 2:
                    break
                await asyncio.sleep(0.02)
            self.assertIsNone(ws.close_code)
        seqs = [b["seq"] for b in self.mind_calls_to("/mode/report")]
        self.assertEqual(seqs[-2:], [1, 2])                                # 1 again until mind took it, then 2
        self.assertTrue(all(s == 1 for s in seqs[:-1]))
        later = [b["age_ms"] for b in self.mind_calls_to("/mode/report") if b["seq"] == 1]
        self.assertGreater(later[-1], later[0])                            # mind sees how late it is

    async def test_full_report_queue_drops_the_oldest(self):
        self.mind_up = False
        async with self.robot() as ws:
            await self.handshake(ws)
            for seq in range(1, 7):
                await ws.send(report(seq, "talk"))
            await asyncio.sleep(0.3)
            self.mind_up = True
            await asyncio.sleep(0.6)
        seqs = [b["seq"] for b in self.mind_calls_to("/mode/report")]
        self.assertEqual(seqs[-3:], [4, 5, 6])                             # the newest three (queue of 3)
        self.assertTrue(any("dropped" in m for m in self.logs))

    async def test_refused_report_is_dropped_not_retried(self):
        self.mind_report_status = 400
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(1, "fly"))
            await ws.send(report(2, "talk"))
            await self.wait_mind("/mode/report", n=2)
            await asyncio.sleep(0.3)
        self.assertEqual([b["seq"] for b in self.mind_calls_to("/mode/report")], [1, 2])
        self.assertTrue(any("refused" in m for m in self.logs))

    async def test_closed_goes_to_mind_with_this_links_conn_after_a_report(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(1))
            await self.wait_mind("/mode/report")
        closed = await self.wait_mind("/mode/robot")
        self.assertEqual(closed, [{"boot": "b1", "conn": 1, "event": "closed"}])
        async with self.robot() as ws:                                     # no report on this link: no close
            await self.handshake(ws)
        await self.wait_result()
        await asyncio.sleep(0.2)
        await self.hub.drain()
        self.assertEqual(len(self.mind_calls_to("/mode/robot")), 1)

    async def test_mode_applied_is_dropped(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_applied", "seq": 5}))
            await ws.send(report(1))
            await self.wait_mind("/mode/report")
        await asyncio.wait_for(self.up_closed.wait(), 2)
        self.assertNotIn("mode_applied", "".join(self.upstream_text))
        self.assertEqual({p for _, p, _ in self.mind_calls} - {"/mode/robot"}, {"/mode/report"})

    async def test_no_polling_and_nothing_pushed(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.no_message(ws, "mode", 0.5)
        self.assertEqual(self.mind_calls, [])

    # ---- digit inputs (unchanged from A5, minus the snapshot push) ----
    async def test_mode_input_goes_to_mind_and_back(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 7, "age_ms": 40, "source": "asr", "input": "digit",
                                      "value": 0}))
            res = await self.recv_type(ws, "mode_result")
            self.assertEqual((res["seq"], res["code"], res["toast"]), (7, "ok", "PRESET 0"))
        body = self.mind_calls_to("/mode/input")[0]
        self.assertEqual({k: body[k] for k in ("device", "boot", "seq", "source", "input", "value")},
                         {"device": "robot", "boot": "b1", "seq": 7, "source": "asr", "input": "digit", "value": 0})
        self.assertGreaterEqual(body["age_ms"], 40)                        # plus the time it waited in the ear
        self.assertLess(body["age_ms"], 1000)
        self.assertNotIn("mode_input", "".join(self.upstream_text))

    async def test_slow_mind_answers_busy_and_audio_keeps_flowing(self):
        self.mind_input_delay = 1.0                                        # e.g. a soul-tts stop inside a hook
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 9, "age_ms": 0, "source": "asr", "input": "digit",
                                      "value": 9}))
            await asyncio.sleep(0.05)
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 3}))
            await self.upstream_send(b"\x03\x00\x00\x00" + b"\x01" * 40)
            self.assertEqual((await self.recv_type(ws, "tts.start", timeout=0.3))["stream"], 3)
            self.assertIsInstance(await self.recv_binary(ws, timeout=0.3), bytes)
            res = await self.recv_type(ws, "mode_result", timeout=1.5)
            self.assertEqual((res["seq"], res["code"]), (9, "busy"))
            self.assertTrue(res["toast"].isascii() and len(res["toast"]) <= 32)
            self.assertIsNone(ws.close_code)

    async def test_full_input_queue_answers_busy_at_once(self):
        self.mind_input_delay = 1.0
        async with self.robot() as ws:
            await self.handshake(ws)
            for seq in range(1, 11):
                await ws.send(json.dumps({"type": "mode_input", "seq": seq, "age_ms": 0, "source": "asr",
                                          "input": "digit", "value": 0}))
            results = {}
            while len(results) < 10:
                r = await self.recv_type(ws, "mode_result", timeout=4)
                results[r["seq"]] = r["code"]
            self.assertEqual(set(results), set(range(1, 11)))
            self.assertEqual(set(results.values()), {"busy"})
            self.assertIsNone(ws.close_code)
        self.assertLessEqual(len(self.mind_calls_to("/mode/input")), 5)    # queue of 4 + the one in flight

    async def test_mind_down_answers_busy_and_keeps_the_robot(self):
        self.mind_up = False
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 2, "age_ms": 0, "source": "asr", "input": "digit",
                                      "value": 0}))
            res = await self.recv_type(ws, "mode_result")
            self.assertEqual((res["seq"], res["code"], res["toast"]), (2, "busy", "MIND OFFLINE"))
            self.assertIsNone(ws.close_code)

    async def test_mode_bug_in_the_reader_is_logged_and_audio_keeps_flowing(self):
        """A5 review I1: an unexpected error in the mode path must not end /mouth, and must be logged."""
        async def broken(link_self, m):
            raise RuntimeError("mode bug")
        real = self.mouth_link.MouthLink._mode_message
        self.mouth_link.MouthLink._mode_message = broken
        self.addCleanup(setattr, self.mouth_link.MouthLink, "_mode_message", real)
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(1))
            await asyncio.sleep(0.1)
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 2}))
            self.assertEqual((await self.recv_type(ws, "tts.start"))["stream"], 2)
            await ws.send(json.dumps({"type": "tts.done", "stream": 2}))
            for _ in range(100):
                if '"tts.done"' in "".join(self.upstream_text):
                    break
                await asyncio.sleep(0.02)
            self.assertIn('"tts.done"', "".join(self.upstream_text))
            self.assertIsNone(ws.close_code)
        text = "\n".join(self.logs)
        self.assertIn("RuntimeError: mode bug", text)
        self.assertIn("Traceback", text)

    async def test_a_reader_that_dies_is_logged_with_traceback(self):
        """A5 review I1: run() no longer swallows a task's unexpected exception behind "device_closed"."""
        real = self.mouth_link._parse
        def exploding(message):
            if message == '{"type":"explode"}':
                raise RuntimeError("reader bug")
            return real(message)
        self.mouth_link._parse = exploding
        self.addCleanup(setattr, self.mouth_link, "_parse", real)
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send('{"type":"explode"}')
            await asyncio.wait_for(ws.wait_closed(), 2)
        self.assertEqual(await self.wait_result(), "device_closed")
        text = "\n".join(self.logs)
        self.assertIn("RuntimeError: reader bug", text)
        self.assertIn("Traceback", text)

    async def test_relay_uses_its_own_threads(self):
        """A5 review M5: mind calls never take the default executor (tingwu start/stop and switches use it)."""
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(1))
            await self.wait_mind("/mode/report")
        self.assertTrue(self.mind_threads)
        self.assertTrue(all(n.startswith("mode-mind") for n in self.mind_threads), self.mind_threads)

    async def test_no_boot_id_no_modes(self):
        async with self.robot() as ws:
            await ws.send(json.dumps({k: v for k, v in ROBOT_HELLO.items() if k != "boot_id"}))
            await asyncio.wait_for(ws.recv(), 2)
            await ws.send(report(1))
            await ws.send(json.dumps({"type": "mode_input", "seq": 1, "age_ms": 0, "source": "asr", "input": "digit",
                                      "value": 0}))
            await self.no_message(ws, "mode_result", 0.3)
            self.assertIsNone(ws.close_code)
        self.assertEqual(self.mind_calls, [])
        self.assertNotIn("mode_", "".join(self.upstream_text))

    async def test_reconnects_leave_no_tasks_behind(self):
        for _ in range(3):
            async with self.robot() as ws:
                await self.handshake(ws)
                await ws.send(report(1))
                await self.wait_mind("/mode/report", n=len(self.mind_calls_to("/mode/report")) + 1)
            await self.wait_result()
        await self.wait_mind("/mode/robot", n=3)
        await self.hub.drain()
        self.assertEqual(self.hub._bg, set())
        self.assertEqual([b["conn"] for b in self.mind_calls_to("/mode/robot")], [1, 2, 3])

    async def test_bad_mode_messages_are_dropped(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": "x", "input": "digit"}))
            await ws.send(json.dumps({"type": "mode_report", "mode": "talk"}))          # no seq
            await ws.send(json.dumps({"type": "mode_report", "seq": 3, "mode": "talk", "effects": "x"}))
            await ws.send(json.dumps({"type": "mode_input", "seq": 2, "age_ms": 0, "source": "asr", "input": "digit",
                                      "value": 0}))
            self.assertEqual((await self.recv_type(ws, "mode_result"))["seq"], 2)
            await self.wait_mind("/mode/report")
        self.assertEqual([b["seq"] for b in self.mind_calls_to("/mode/input")], [2])
        self.assertEqual([(b["seq"], b["effects"]) for b in self.mind_calls_to("/mode/report")], [(3, [])])


SPEECH = {"type": "speech", "phase": "end", "id": "s-3", "pos": [16000, 32000], "t": 1794300000000, "dur": 1.0,
          "speaker": {"band": "known", "name": "me", "score": 0.7, "runner_up": None}, "text": "今天几号",
          "lang": "zh", "emotion": "neutral", "emotion_reliable": False, "event": "speech",
          "voice": {"p": 0.3, "band": "unclear", "acoustic_band": "unclear", "loud_db": 18.5, "rate": 4.0},
          "tags": {"speech_p": 0.9, "kinds": []}, "self": {"sounding": False, "moving": False}, "respond": True,
          "debug": {"vad_wait_ms": 400, "queue_ms": 1, "asr_ms": 150, "server_ms": 520}}


@unittest.skipUnless(HAVE_WS, "needs websockets")
class ChatPushTest(MouthModeTest):
    """Chat v0: the relay remembers hello's reported mode; in chat a speech end goes to mind /chat/heard at once,
    on the relay's pool, bounded, never blocking the loop."""

    async def in_mode(self, ws, mode, seq=1):
        await ws.send(report(seq, mode, ["stop_speech"], inp="play" if mode == "chat" else "pause"))
        for _ in range(150):
            if self.hub.robot_mode == mode:
                return
            await asyncio.sleep(0.02)
        self.fail(f"relay did not see {mode}")

    async def test_in_chat_a_sentence_goes_to_mind(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            self.assertTrue(self.hub.chat_heard(dict(SPEECH), "20261114/120000-stackchan-c1"))
            body = (await self.wait_mind("/chat/heard"))[0]
        self.assertEqual({k: body[k] for k in ("session", "id", "pos", "text", "lang", "emotion", "loud_db", "self")},
                         {"session": "20261114/120000-stackchan-c1", "id": "s-3", "pos": [16000, 32000],
                          "text": "今天几号", "lang": "zh", "emotion": "neutral", "loud_db": 18.5,
                          "self": {"sounding": False, "moving": False}})
        self.assertEqual(body["speaker"], {"band": "known", "name": "me", "score": 0.7})
        self.assertEqual(body["voice"], {"p": 0.3, "band": "unclear", "acoustic_band": "unclear", "loud_db": 18.5})
        self.assertEqual(body["debug"]["server_ms"], 520)
        self.assertIsInstance(body["sent_at"], int)
        self.assertTrue(all(n.startswith("mode-mind") for n in self.mind_threads), self.mind_threads)
        self.assertFalse(any("今天几号" in line for line in self.logs))

    async def test_outside_chat_nothing_is_sent(self):
        self.assertFalse(self.hub.chat_heard(dict(SPEECH), "s"))                 # no mode reported yet
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat", seq=1)
            await self.in_mode(ws, "idle", seq=2)
            self.assertFalse(self.hub.chat_heard(dict(SPEECH), "s"))
            await self.in_mode(ws, "chat", seq=3)
            self.assertFalse(self.hub.chat_heard({**SPEECH, "text": "  "}, "s"))    # no text: nothing to answer
            await asyncio.sleep(0.1)
        self.assertEqual(self.mind_calls_to("/chat/heard"), [])

    async def test_settings_over_chat_is_not_chat(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat", seq=1)
            await ws.send(report(2, "settings", ["stop_speech"], inp="open", return_to="chat"))
            for _ in range(100):
                if self.hub.robot_mode == "settings":
                    break
                await asyncio.sleep(0.02)
            self.assertFalse(self.hub.chat_heard(dict(SPEECH), "s"))

    async def test_slow_mind_never_blocks_and_extra_sentences_are_dropped(self):
        self.mind_chat_delay = 0.8
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            t0 = self._time.monotonic()
            sent = [self.hub.chat_heard({**SPEECH, "id": f"s-{i}"}, "s") for i in range(4)]
            self.assertLess(self._time.monotonic() - t0, 0.05)                    # the loop never waited
            self.assertEqual(sent, [True, True, False, False])                     # at most 2 in flight
            await self.upstream_send(json.dumps({"type": "tts.start", "stream": 5}))   # audio keeps flowing meanwhile
            self.assertEqual((await self.recv_type(ws, "tts.start", timeout=0.3))["stream"], 5)
            await ws.send(report(2, "chat", [], inp="tap"))                        # and mode reports still reach mind
            await self.wait_mind("/mode/report", n=2, timeout=0.6)
            await self.wait_mind("/chat/heard", n=2)
            for _ in range(100):
                if self.hub.chat_inflight == 0:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(self.hub.chat_inflight, 0)
            self.assertTrue(self.hub.chat_heard({**SPEECH, "id": "s-9"}, "s"))   # room again
        self.assertTrue(any("dropped" in line for line in self.logs))

    async def test_mind_timeout_frees_the_slot(self):
        self.mind_chat_delay = 1.5
        self.hub.chat_timeout_s = 0.2
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            self.hub.chat_heard(dict(SPEECH), "s")
            await asyncio.sleep(0.4)
            self.assertEqual(self.hub.chat_inflight, 0)
        self.assertTrue(any("not taken" in line for line in self.logs))

    async def test_the_link_closing_forgets_its_mode(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
        await self.wait_result()
        self.assertIsNone(self.hub.robot_mode)
        self.assertFalse(self.hub.chat_heard(dict(SPEECH), "s"))

    async def test_an_old_link_closing_keeps_the_new_links_mode(self):
        self.hub.note_report({"mode": "chat"}, 7)
        self.hub.link_closed(6)
        self.assertTrue(self.hub.in_chat())
        self.hub.link_closed(7)
        self.assertFalse(self.hub.in_chat())


class EarHookTest(unittest.TestCase):
    """ear_server's end_speech hands the event to the relay (source check: ear_server needs the models to import)."""
    def test_end_speech_calls_chat_heard_for_robot_sources(self):
        src = (Path(__file__).resolve().parents[1] / "server" / "ear" / "ear_server.py").read_text()
        body = src[src.index("    async def end_speech"):src.index("    # ---------- sound ----------")]
        self.assertIn("self.chat.chat_heard(ev, self.session)", body)
        self.assertIn("self.device_profile and text", body)
        self.assertLess(body.index("self.post(ev)"), body.index("chat_heard"))   # the robot's event first
        self.assertIn("ear.chat = mode_hub", src)

class MindClientTest(unittest.TestCase):
    def test_never_raises_when_mind_is_down(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
        import mouth_link
        call = mouth_link.mind_client("t", base="http://127.0.0.1:9")
        status, reply = call("GET", "/mode/seq", timeout=1)
        self.assertEqual(status, 503)

    def test_get_and_post_with_token(self):
        import mouth_link
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                seen.append(("GET", self.path, self.headers.get("Authorization"), None))
                self._reply(200, {"seq": 3})
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                seen.append(("POST", self.path, self.headers.get("Authorization"), self.headers.get("Content-Type")))
                self._reply(400, {"error": "bad", "echo": json.loads(self.rfile.read(n))})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            call = mouth_link.mind_client("tok", base=f"http://127.0.0.1:{srv.server_address[1]}")
            self.assertEqual(call("GET", "/mode/seq"), (200, {"seq": 3}))
            self.assertEqual(call("POST", "/mode/input", {"a": 1}), (400, {"error": "bad", "echo": {"a": 1}}))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(seen, [("GET", "/mode/seq", "Bearer tok", None),
                                ("POST", "/mode/input", "Bearer tok", "application/json")])


if __name__ == "__main__":
    unittest.main()
