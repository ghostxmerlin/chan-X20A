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

MODE_LOOK = {"talk": ("laughing_man", "TALK"), "chat": ("v05", "CHAT"), "idle": ("page_idle", "IDLE"),
             "settings": ("page_settings", "SET")}


@unittest.skipUnless(HAVE_WS, "needs websockets")
class MouthModeTest(MouthLinkTest):
    """Task A5: the same /mouth with mind's modes behind it. Every A0 test above also runs here (a robot that
    ignores mode messages, like 0.4.91, keeps audio and liveness); the tests below are the mode messages."""

    async def asyncSetUp(self):
        import time as _time
        self._time = _time
        self.mind_seq, self.mind_mode, self.mind_up, self.mind_extra = 5, "talk", True, {}
        self.mind_input_delay, self.mind_on_input, self.mind_calls = 0, None, []
        self.conn_no = 0
        await super().asyncSetUp()
        self.hub = self.mouth_link.ModeHub(self.fake_mind, poll_s=0.1, full_s=60, input_timeout_s=0.5,
                                           input_queue=4, hello_retry_s=0.2)
        self.hub_task = asyncio.create_task(self.hub.run())

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.hub_task.cancel()
        await asyncio.gather(self.hub_task, return_exceptions=True)
        await self.hub.drain()

    def make_link(self, ws):
        self.conn_no += 1
        return self.mouth_link.MouthLink(ws, upstream_url=self.up_url, token="device-token", retry_s=0.3,
                                         connect=self.connect_override, conn=self.conn_no, modes=self.hub)

    def _skip(self, m):                       # a robot that ignores mode messages: they are not "unexpected" here
        if isinstance(m, str) and json.loads(m).get("type") in ("mode", "mode_result"):
            return
        self.skipped.append(m)

    # ---- fake mind: one function, records every call by path ----
    def snapshot(self):
        eye, label = MODE_LOOK[self.mind_mode]
        return {"seq": self.mind_seq, "mode": self.mind_mode, "dir": None, "host": None, "return_to": None,
                "billing": True, "since": 0, "by": "page", "look": {"eye": eye, "pool": None, "label": label,
                "color": "E040A0"}, "touch": {"tap": "server", "double": "server"}, **self.mind_extra}

    def fake_mind(self, method, path, body=None, timeout=5):
        self.mind_calls.append((method, path, json.loads(json.dumps(body))))
        if not self.mind_up:
            return 503, {"error": "URLError"}
        if (method, path) == ("GET", "/mode/seq"):
            return 200, {"seq": self.mind_seq}
        if (method, path) == ("GET", "/mode"):
            return 200, {"snapshot": self.snapshot(), "robot": {}, "log": []}
        if (method, path) == ("POST", "/mode/robot"):
            return 200, {"snapshot": self.snapshot()}
        if (method, path) == ("POST", "/mode/input"):
            if self.mind_input_delay:
                self._time.sleep(self.mind_input_delay)
            if self.mind_on_input:
                self.mind_on_input(body)
            return 200, {"snapshot": self.snapshot(), "result": {"code": "ok", "toast": "OK"}, "effects": []}
        return 404, {"error": "not found"}

    def mind_calls_to(self, path, event=None):
        return [b for _, p, b in self.mind_calls if p == path and (event is None or (b or {}).get("event") == event)]

    def mind_set(self, mode=None, seq=None):
        if mode is not None:
            self.mind_mode = mode
        if seq is not None:
            self.mind_seq = seq

    async def wait_mind(self, path, event=None, n=1, timeout=3):
        for _ in range(int(timeout / 0.02)):
            if len(self.mind_calls_to(path, event)) >= n:
                return self.mind_calls_to(path, event)
            await asyncio.sleep(0.02)
        self.fail(f"mind did not get {n} x {path} {event or ''}")

    async def no_message(self, ws, kind, timeout):
        with self.assertRaises(asyncio.TimeoutError):
            await self.recv_type(ws, kind, timeout=timeout)

    # ---- the brief's tests ----
    async def test_pushes_mode_after_hello(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            mode = await self.recv_type(ws, "mode")
            self.assertEqual(mode["mode"], "talk")
        self.assertEqual(self.mind_calls_to("/mode/robot")[0]["event"], "hello")

    async def test_mode_input_goes_to_mind_not_soul_tts(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 4, "age_ms": 50, "source": "asr", "input": "play"}))
            res = await self.recv_type(ws, "mode_result")
            self.assertEqual((res["seq"], res["code"]), (4, "ok"))
        self.assertEqual(self.mind_calls_to("/mode/input")[0]["boot"], "b1")
        self.assertNotIn("mode_input", "".join(self.upstream_text))

    async def test_page_change_reaches_robot_with_upstream_down(self):
        await self.stop_upstream()
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
            self.mind_set(mode="chat", seq=self.mind_seq + 1)               # changed on the page
            self.assertEqual((await self.recv_type(ws, "mode", timeout=3))["mode"], "chat")
            self.assertIsNone(ws.close_code)

    # ---- carried items and the rest of the wire ----
    async def test_mode_message_is_the_snapshot_under_1kb(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            raw = None
            while raw is None:
                m = await asyncio.wait_for(ws.recv(), 2)
                if isinstance(m, str) and json.loads(m).get("type") == "mode":
                    raw = m
            self.assertLessEqual(len(raw.encode()), 1024)
            self.assertEqual({k: v for k, v in json.loads(raw).items() if k != "type"}, self.snapshot())

    async def test_input_body_for_mind(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 7, "age_ms": 40, "source": "touch",
                                      "input": "select", "value": {"mode": "chat"}}))
            await self.recv_type(ws, "mode_result")
        body = self.mind_calls_to("/mode/input")[0]
        self.assertEqual({k: body[k] for k in ("device", "boot", "seq", "source", "input", "value")},
                         {"device": "robot", "boot": "b1", "seq": 7, "source": "touch", "input": "select",
                          "value": {"mode": "chat"}})
        self.assertGreaterEqual(body["age_ms"], 40)                        # plus the time it waited in the ear
        self.assertLess(body["age_ms"], 1000)

    async def test_result_first_then_the_new_snapshot(self):
        self.hub.poll_s = 30                                               # only the input path pushes here
        await asyncio.sleep(0.15)
        self.mind_on_input = lambda body: self.mind_set(mode="settings", seq=self.mind_seq + 1)
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
            await ws.send(json.dumps({"type": "mode_input", "seq": 3, "age_ms": 0, "source": "asr", "input": "open"}))
            got = []
            while len(got) < 2:
                m = json.loads(await asyncio.wait_for(ws.recv(), 2))
                if m.get("type") in ("mode", "mode_result"):
                    got.append(m)
            self.assertEqual((got[0]["type"], got[0]["seq"], got[0]["code"]), ("mode_result", 3, "ok"))
            self.assertEqual((got[1]["type"], got[1]["mode"], got[1]["seq"]), ("mode", "settings", 6))

    async def test_seq_going_backwards_is_pushed(self):
        """mind DB failure + restart can move seq back: any change (!=) pushes the full snapshot."""
        async with self.robot() as ws:
            await self.handshake(ws)
            self.assertEqual((await self.recv_type(ws, "mode"))["seq"], 5)
            self.mind_set(mode="idle", seq=2)
            m = await self.recv_type(ws, "mode", timeout=3)
            self.assertEqual((m["seq"], m["mode"]), (2, "idle"))

    async def test_same_seq_is_pushed_again_only_every_full_period(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            first = await self.recv_type(ws, "mode")
            await self.no_message(ws, "mode", 0.5)                         # unchanged: polled, not pushed
            self.hub.full_s = 0.3
            again = await self.recv_type(ws, "mode", timeout=2)
            self.assertEqual(again, first)

    async def test_hello_and_closed_carry_this_links_conn(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
        await self.wait_mind("/mode/robot", "closed")
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
        closed = await self.wait_mind("/mode/robot", "closed", n=2)
        hellos = self.mind_calls_to("/mode/robot", "hello")
        self.assertEqual([h["conn"] for h in hellos], [1, 2])
        self.assertEqual([c["conn"] for c in closed], [1, 2])
        self.assertEqual({h["boot"] for h in hellos + closed}, {"b1"})

    async def test_mode_applied_goes_to_mind_with_conn(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
            await ws.send(json.dumps({"type": "mode_applied", "seq": 5}))
            applied = (await self.wait_mind("/mode/robot", "applied"))[0]
        self.assertEqual((applied["applied_seq"], applied["conn"], applied["boot"]), (5, self.links[-1].conn, "b1"))
        await asyncio.wait_for(self.up_closed.wait(), 2)
        self.assertNotIn("mode_applied", "".join(self.upstream_text))

    async def test_slow_mind_answers_busy_and_audio_keeps_flowing(self):
        self.mind_input_delay = 1.0                                        # e.g. a soul-tts stop inside a hook
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
            await ws.send(json.dumps({"type": "mode_input", "seq": 9, "age_ms": 0, "source": "asr", "input": "stop"}))
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
            await self.recv_type(ws, "mode")
            for seq in range(1, 11):
                await ws.send(json.dumps({"type": "mode_input", "seq": seq, "age_ms": 0, "source": "asr", "input": "play"}))
            results = {}
            while len(results) < 10:
                r = await self.recv_type(ws, "mode_result", timeout=4)
                results[r["seq"]] = r["code"]
            self.assertEqual(set(results), set(range(1, 11)))
            self.assertEqual(set(results.values()), {"busy"})
            self.assertIsNone(ws.close_code)
        self.assertLessEqual(len(self.mind_calls_to("/mode/input")), 5)    # queue of 4 + the one in flight

    async def test_mind_down_keeps_robot_answers_busy_and_recovers(self):
        self.mind_up = False
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": 2, "age_ms": 0, "source": "asr", "input": "play"}))
            res = await self.recv_type(ws, "mode_result")
            self.assertEqual((res["seq"], res["code"]), (2, "busy"))
            await self.no_message(ws, "mode", 0.5)                         # mind away: nothing pushed
            self.assertIsNone(ws.close_code)
            self.mind_up = True                                            # mind back: hello retried, snapshot sent
            self.assertEqual((await self.recv_type(ws, "mode", timeout=3))["mode"], "talk")
        self.assertGreaterEqual(len(self.mind_calls_to("/mode/robot", "hello")), 2)

    async def test_mind_back_after_away_gets_hello_again(self):
        """mind restarted while the robot stayed: it is told again who is connected (same boot, same conn)."""
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "mode")
            self.mind_up = False
            await asyncio.sleep(0.4)                                       # the poll sees mind away
            self.mind_up = True
            hellos = await self.wait_mind("/mode/robot", "hello", n=2)
            self.assertEqual({(h["boot"], h["conn"]) for h in hellos}, {("b1", self.links[-1].conn)})
            self.assertIsNone(ws.close_code)

    async def test_oversized_snapshot_is_not_sent(self):
        self.mind_extra = {"pad": "x" * 1100}
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.no_message(ws, "mode", 0.6)
            self.assertIsNone(ws.close_code)
        self.assertTrue(any("too large" in m for m in self.logs))

    async def test_bad_mode_input_is_dropped(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "mode_input", "seq": "x", "input": "play"}))
            await ws.send(json.dumps({"type": "mode_input", "seq": 2, "age_ms": 0, "source": "asr", "input": "play"}))
            self.assertEqual((await self.recv_type(ws, "mode_result"))["seq"], 2)
        self.assertEqual([b["seq"] for b in self.mind_calls_to("/mode/input")], [2])

    async def test_no_mind_calls_without_a_hello(self):
        async with self.robot() as ws:
            await ws.send('{"type":"tts.done","stream":1}')
            await asyncio.wait_for(ws.wait_closed(), 2)
        await asyncio.sleep(0.3)
        self.assertEqual(self.mind_calls, [])                              # nobody connected: not even polling


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
