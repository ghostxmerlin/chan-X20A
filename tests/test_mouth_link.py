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
                                         connect=self.connect_override, burst=getattr(self, "burst", None))

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

    async def test_snap_result_goes_to_the_burst_never_to_soul_tts(self):
        got = []

        class Burst:
            def on_snap_result(self, link, m):
                got.append((link, m))
        self.burst = Burst()
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "snap_result", "id": 7, "ok": True, "seq": 12, "bytes": 100, "ms": 150}))
            await ws.send(json.dumps({"type": "snap_result", "id": 8, "ok": False, "why": "busy"}))
            await ws.send(json.dumps({"type": "tts.done", "stream": 3}))         # a report after it still reaches soul-tts
            for _ in range(100):
                if any("tts.done" in t for t in self.upstream_text):
                    break
                await asyncio.sleep(0.02)
        self.assertEqual([m["id"] for _, m in got], [7, 8])
        self.assertIs(got[0][0], self.links[-1])
        self.assertTrue(any("tts.done" in t for t in self.upstream_text))
        self.assertNotIn("snap_result", "".join(self.upstream_text))

    async def test_snap_result_without_a_burst_is_dropped(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "snap_result", "id": 7, "ok": False, "why": "camera_off"}))
            await ws.send(json.dumps({"type": "tts.done", "stream": 3}))
            for _ in range(100):
                if any("tts.done" in t for t in self.upstream_text):
                    break
                await asyncio.sleep(0.02)
        self.assertNotIn("snap_result", "".join(self.upstream_text))

    async def test_a_burst_bug_never_ends_mouth(self):
        class Burst:
            def on_snap_result(self, link, m):
                raise RuntimeError("burst bug")
        self.burst = Burst()
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(json.dumps({"type": "snap_result", "id": 1, "ok": True, "seq": 1}))
            await ws.send(json.dumps({"type": "tts.done", "stream": 3}))
            for _ in range(100):
                if any("tts.done" in t for t in self.upstream_text):
                    break
                await asyncio.sleep(0.02)
        self.assertTrue(any("tts.done" in t for t in self.upstream_text))
        self.assertTrue(any("snap_result: unexpected error" in line for line in self.logs))

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
                                         connect=self.connect_override, conn=self.conn_no, modes=self.hub,
                                         burst=getattr(self, "burst", None), presence=getattr(self, "presence", None))

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
            return getattr(self, "mind_chat_reply", (200, {"tid": "t1", "accepted": True, "skip": None}))
        if (method, path) == ("POST", "/chat/stage"):
            return 200, {"ok": True}
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

    async def test_an_owners_report_is_presence_a_boot_or_timeout_report_is_not(self):
        """The pause (mind/pause.py, 2026-11-17): an ASR word / tap / select / quick page on hello = someone is here."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
        import presence as pr
        self.presence = pr.Presence(clock_ms=lambda: 1000)
        async with self.robot() as ws:
            await self.handshake(ws)
            await ws.send(report(1, "crowd", ["stop_speech"], by="boot", inp="boot"))
            await ws.send(report(2, "talk", [], by="timeout", inp="timeout"))
            await self.wait_mind("/mode/report", n=2)
            self.assertIsNone(self.presence.owner_at)
            await ws.send(report(3, "crowd", [], by="touch", inp="tap"))
            await self.wait_mind("/mode/report", n=3)
        self.assertEqual((self.presence.owner_at, self.presence.owner_what), (1000, "touch:tap"))

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
        # review M1: chat posts have their own pool, mode reports / inputs keep theirs
        self.assertEqual({n.split("_")[0] for n in self.mind_threads}, {"mode-mind", "chat-mind"}, self.mind_threads)
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

    async def test_mind_timeout_frees_the_slot_when_the_call_ends(self):
        self.mind_chat_delay = 0.8
        self.hub.chat_timeout_s = 0.2
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            self.hub.chat_heard(dict(SPEECH), "s")
            await asyncio.sleep(0.4)
            self.assertTrue(any("not taken" in line for line in self.logs))   # gave up waiting after 0.2 s
            self.assertEqual(self.hub.chat_inflight, 1)                         # but its thread still runs
            for _ in range(100):
                if self.hub.chat_inflight == 0:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(self.hub.chat_inflight, 0)

    async def test_a_hung_mind_on_chat_never_delays_mode_inputs(self):
        self.mind_chat_delay = 1.5
        self.hub.chat_timeout_s = 0.1
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            for i in range(6):                                                  # more than the pools have threads
                self.hub.chat_heard({**SPEECH, "id": f"s-{i}"}, "s")
                await asyncio.sleep(0.15)
            t0 = self._time.monotonic()
            await ws.send(json.dumps({"type": "mode_input", "seq": 5, "age_ms": 0, "source": "asr", "input": "digit",
                                      "value": 0}))
            res = await self.recv_type(ws, "mode_result", timeout=1)
            self.assertEqual((res["seq"], res["code"]), (5, "ok"))
            self.assertLess(self._time.monotonic() - t0, 0.3)
            self.assertLessEqual(self.hub.chat_inflight, 2)

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


@unittest.skipUnless(HAVE_WS, "needs websockets")
class ChatStageTest(ChatPushTest):
    """Stage indicator (2026-11-14): the ear tells the robot {"type":"chat_stage","stage":listen|think|skip,"at_ms"}
    on /mouth, only in chat: listen at speech onset (debounced 300 ms, never while hello sounds), think / skip from
    mind's /chat/heard answer, skip for a sentence without text; nothing when mind is slow or down."""

    async def stages(self, ws, n, timeout=1.5):
        got = []
        for _ in range(n):
            m = await self.recv_type(ws, "chat_stage", timeout=timeout)
            got.append(m)
        return got

    async def test_listen_then_think(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            t0 = int(self._time.time() * 1000)
            self.assertTrue(self.hub.chat_listen())
            self.hub.chat_heard(dict(SPEECH), "s")
            got = await self.stages(ws, 2)
        self.assertEqual([m["stage"] for m in got], ["listen", "think"])
        self.assertTrue(all(set(m) == {"type", "stage", "at_ms"} for m in got))
        self.assertTrue(all(t0 - 50 <= m["at_ms"] <= t0 + 3000 for m in got))
        self.assertTrue(all(len(json.dumps(m)) <= 200 for m in got))
        await self.wait_mind("/chat/stage")                                     # mind hears the onset too (page dots)
        self.assertEqual(self.mind_calls_to("/chat/stage")[0]["stage"], "listen")

    async def test_listen_is_debounced(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            self.assertEqual([self.hub.chat_listen(), self.hub.chat_listen()], [True, False])
            await self.stages(ws, 1)
            await self.no_message(ws, "chat_stage", 0.2)
            await asyncio.sleep(0.15)
            self.assertTrue(self.hub.chat_listen())                             # >= 300 ms later
            await self.stages(ws, 1)

    async def test_skip_from_mind_and_for_an_empty_sentence(self):
        self.mind_chat_reply = (200, {"tid": "t1", "accepted": False, "skip": "far_quiet"})
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            self.hub.chat_heard(dict(SPEECH), "s")
            self.assertEqual((await self.stages(ws, 1))[0]["stage"], "skip")
            self.assertTrue(self.hub.chat_nothing())                            # end_speech without text
            self.assertEqual((await self.stages(ws, 1))[0]["stage"], "skip")

    async def test_mind_down_sends_nothing_extra(self):
        self.mind_up = False
        async with self.robot() as ws:
            await self.handshake(ws)
            for _ in range(150):                                                # the mode is known before mind is
                await ws.send(report(1, "chat", ["stop_speech"], inp="play"))
                break
            for _ in range(150):
                if self.hub.robot_mode == "chat":
                    break
                await asyncio.sleep(0.02)
            self.hub.chat_heard(dict(SPEECH), "s")
            await self.wait_mind("/chat/heard")
            await self.no_message(ws, "chat_stage", 0.4)

    async def test_nothing_outside_chat(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "talk")
            self.assertEqual((self.hub.chat_listen(), self.hub.chat_nothing()), (False, False))
            await self.no_message(ws, "chat_stage", 0.3)
        self.assertEqual(self.mind_calls_to("/chat/stage"), [])

    async def test_crowd_shows_dots_from_realtime(self):
        # owner 2026-11-15: 围观 gets chat's dots. listen from the ear's onset (no mind post), think / skip only from
        # realtime (crowd_stage); the ear's own "nothing heard" never sends skip in crowd.
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "crowd")
            self.assertTrue(self.hub.chat_listen())
            self.assertEqual((await self.stages(ws, 1))[0]["stage"], "listen")
            self.assertFalse(self.hub.chat_nothing())
            self.assertTrue(self.hub.crowd_stage("think"))
            self.assertEqual((await self.stages(ws, 1))[0]["stage"], "think")
            self.assertTrue(self.hub.crowd_stage("skip"))
            self.assertEqual((await self.stages(ws, 1))[0]["stage"], "skip")
        self.assertEqual(self.mind_calls_to("/chat/stage"), [])

    async def test_crowd_stage_only_in_crowd(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
            self.assertFalse(self.hub.crowd_stage("think"))
            await self.no_message(ws, "chat_stage", 0.3)

    async def test_the_robot_left_sends_nothing(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.in_mode(ws, "chat")
        await self.wait_result()
        self.assertFalse(self.hub.chat_listen())


for _name in [n for n in dir(ChatPushTest) if n.startswith("test_")]:
    setattr(ChatStageTest, _name, None)          # only the stage tests here: the inherited ones already ran above


@unittest.skipUnless(HAVE_WS, "needs websockets")
class PlaceTest(MouthModeTest):
    """Place (2026-11-15, docs/superpowers/specs/2026-11-15-place-contract.md): a place frame after each handshake and on
    every change; the robot's place_select goes to mind POST /place {source: robot}, never to soul-tts."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub.close()
        self.place_state = {"current": None, "by": None, "at": None, "choices": ["办公室", "家里", "车上", "饭店"], "rev": 0}
        self.hub = self.mouth_link.ModeRelay(self.fake_mind, input_timeout_s=0.5, report_timeout_s=0.5,
                                             report_retry_s=0.1, place_poll_s=0.2, place_timeout_s=0.5)

    def fake_mind(self, method, path, body=None, timeout=5):
        if path != "/place":
            return super().fake_mind(method, path, body, timeout)
        self.mind_calls.append((method, path, json.loads(json.dumps(body))))
        if not self.mind_up:
            return 503, {"error": "URLError"}
        if method == "POST":
            if body["label"] is not None and body["label"] not in self.place_state["choices"]:
                return 400, {"error": "choice: not one of the common places"}
            self.place_state.update(current=body["label"], by=body["source"], at=1794300000000)
            return 200, {"ok": True, **self.place_state}
        return 200, dict(self.place_state)

    def _skip(self, m):
        if isinstance(m, str) and json.loads(m).get("type") in ("mode_result", "place"):
            return
        self.skipped.append(m)

    async def test_handshake_brings_the_place_in_utf8_within_the_limit(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            raw = await asyncio.wait_for(ws.recv(), 2)
            self.assertIn("车上", raw)                                   # UTF-8, not \u escapes
            self.assertLessEqual(len(raw.encode()), 1600)
            frame = json.loads(raw)
            self.assertEqual(frame, {"type": "place", "current": None, "by": None,
                                     "choices": ["办公室", "家里", "车上", "饭店"], "at_ms": None})
            await self.no_message(ws, "place", 0.5)                      # unchanged: not sent again

    async def test_a_change_in_mind_is_pushed(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "place")
            self.place_state.update(current="湖边", by="voice", at=1794300000000)
            frame = await self.recv_type(ws, "place", timeout=2)
            self.assertEqual((frame["current"], frame["by"], frame["at_ms"]), ("湖边", "voice", 1794300000000))

    async def test_select_goes_to_mind_and_every_link_gets_the_new_place(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "place")
            await ws.send(json.dumps({"type": "place_select", "seq": 3, "choice": "车上"}))
            res = await self.recv_type(ws, "place_result")
            self.assertEqual((res["seq"], res["code"], res["toast"]), (3, "ok", "PLACE SET"))
            frame = await self.recv_type(ws, "place")
            self.assertEqual((frame["current"], frame["by"]), ("车上", "robot"))
            self.assertEqual(self.mind_calls_to("/place")[-1], {"label": "车上", "source": "robot"})
            await ws.send(json.dumps({"type": "place_select", "seq": 4, "choice": None}))      # unknown
            self.assertEqual((await self.recv_type(ws, "place_result"))["code"], "ok")
            self.assertIsNone((await self.recv_type(ws, "place"))["current"])
        await asyncio.wait_for(self.up_closed.wait(), 2)
        self.assertNotIn("place_select", "".join(self.upstream_text))

    async def test_bad_choice_and_mind_down(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "place")
            await ws.send(json.dumps({"type": "place_select", "seq": 5, "choice": "火星"}))
            self.assertEqual((await self.recv_type(ws, "place_result"))["code"], "bad_choice")
            await ws.send(json.dumps({"type": "place_select", "seq": 6, "choice": 7}))
            self.assertEqual((await self.recv_type(ws, "place_result"))["code"], "bad_choice")
            self.mind_up = False
            await ws.send(json.dumps({"type": "place_select", "seq": 7, "choice": "家里"}))
            res = await self.recv_type(ws, "place_result")
            self.assertEqual((res["code"], res["toast"]), ("busy", "MIND OFFLINE"))
            await ws.send(json.dumps({"type": "place_select", "choice": "家里"}))           # no seq: dropped
            await self.no_message(ws, "place_result", 0.4)

    async def test_audio_keeps_flowing_while_mind_is_slow(self):
        async with self.robot() as ws:
            await self.handshake(ws)
            await self.recv_type(ws, "place")
            orig = self.fake_mind
            self.fake_mind = lambda *a, **k: (self._time.sleep(1.0), orig(*a, **k))[1]
            self.hub.call = self.fake_mind
            await ws.send(json.dumps({"type": "place_select", "seq": 8, "choice": "家里"}))
            await ws.send(json.dumps({"type": "place_select", "seq": 9, "choice": "家里"}))   # one at a time per link
            self.assertEqual((await self.recv_type(ws, "place_result"))["code"], "busy")
            await self.upstream_send(b"SPK1audio")
            self.assertEqual(await self.recv_binary(ws), b"SPK1audio")

    async def test_no_boot_id_no_place(self):
        async with self.robot() as ws:
            await ws.send(json.dumps({**ROBOT_HELLO, "boot_id": None}))
            await asyncio.wait_for(ws.recv(), 2)
            await self.no_message(ws, "place", 0.5)


for _name in [n for n in dir(MouthModeTest) if n.startswith("test_")]:
    setattr(PlaceTest, _name, None)              # only the place tests here: the inherited ones already ran above


class PlaceFrameTest(unittest.TestCase):
    def test_limits(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
        import mouth_link
        st = {"current": "一个很长很长的地点名字" * 4, "by": "robot", "at": 1,
              "choices": ["一二三四五六七八九", "a\nb", "", 3, "家里", "家里"] + [f"地点{i}" for i in range(12)]}
        text = mouth_link.place_frame(st)
        f = json.loads(text)
        self.assertLessEqual(len(f["current"].encode()), mouth_link.PLACE_CURRENT_BYTES)
        self.assertEqual(f["choices"][:3], ["一二三四五六七八", "ab", "家里"])
        self.assertEqual(len(f["choices"]), mouth_link.PLACE_MAX_CHOICES)
        self.assertTrue(all(len(c.encode()) <= 24 for c in f["choices"]))
        self.assertLessEqual(len(text.encode()), 1600)
        self.assertIsNone(mouth_link.place_frame({"current": "x"}))
        self.assertIsNone(json.loads(mouth_link.place_frame({"choices": [], "by": "mind", "current": 5}))["by"])


class EarHookTest(unittest.TestCase):
    """ear_server's end_speech hands the event to the relay (source check: ear_server needs the models to import)."""
    def test_end_speech_calls_chat_heard_for_robot_sources(self):
        src = (Path(__file__).resolve().parents[1] / "server" / "ear" / "ear_server.py").read_text()
        body = src[src.index("    async def end_speech"):src.index("    # ---------- sound ----------")]
        self.assertIn("self.chat.chat_heard(ev, self.session)", body)
        self.assertIn("self.device_profile and text", body)
        self.assertLess(body.index("self.post(ev)"), body.index("chat_heard"))   # the robot's event first
        self.assertIn("ear.chat = mode_hub", src)

    def test_onset_sends_listen_unless_hello_sounds(self):
        src = (Path(__file__).resolve().parents[1] / "server" / "ear" / "ear_server.py").read_text()
        body = src[src.index("    def open_utterance"):src.index("    # ---------- speech ----------")]
        self.assertIn("self.chat.chat_listen()", body)
        self.assertIn('self.self_flags.get("sounding")', body)
        self.assertIn("self.device_profile", body)
        end = src[src.index("    async def end_speech"):src.index("    # ---------- sound ----------")]
        self.assertIn("self.chat.chat_nothing()", end)

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
