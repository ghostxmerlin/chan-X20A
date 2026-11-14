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
        mouth_link.log = lambda *a, **k: None
        self.seen_headers, self.upstream_text, self.up_closed = [], [], asyncio.Event()
        self.up_hellos, self.up_hello_seen = 0, asyncio.Event()
        self.up_ws, self.up, self.up_port = None, None, 0
        await self.start_upstream()
        self.up_url = f"ws://127.0.0.1:{self.up_port}/mouth"
        self.results, self.links, self.tasks = [], [], []

        async def front(ws):                                   # the ear's /mouth branch, as in ear_server
            link = mouth_link.MouthLink(ws, upstream_url=self.up_url, token="device-token", retry_s=0.3)
            self.links.append(link)
            self.tasks.append(asyncio.current_task())
            self.results.append(await link.run())
        self.front = await serve(front, "127.0.0.1", 0)
        self.front_url = f"ws://127.0.0.1:{self.front.sockets[0].getsockname()[1]}/mouth"

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
                self.skipped.append(m)
        return await asyncio.wait_for(go(), timeout)

    async def recv_binary(self, ws, timeout=2):
        async def go():
            while True:
                m = await ws.recv()
                if isinstance(m, bytes):
                    return m
                self.skipped.append(m)
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


if __name__ == "__main__":
    unittest.main()
