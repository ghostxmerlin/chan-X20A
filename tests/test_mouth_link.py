"""ear's /mouth relay: device <-> soul-tts mouth (127.0.0.1:52751). Needs websockets (run in .private/venv-ear)."""
import asyncio
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


@unittest.skipUnless(HAVE_WS, "needs websockets")
class MouthLinkTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import mouth_link
        self.mouth_link = mouth_link
        self.seen_headers, self.up_closed = [], asyncio.Event()

        async def upstream(ws):
            self.seen_headers.append(ws.request.headers.get("Authorization"))
            try:
                async for m in ws:
                    await ws.send(m)                      # echo text and binary
            finally:
                self.up_closed.set()
        self.up = await serve(upstream, "127.0.0.1", 0)
        self.up_url = f"ws://127.0.0.1:{self.up.sockets[0].getsockname()[1]}/mouth"
        self.results = []

        async def front(ws):
            self.results.append(await mouth_link.relay(ws, self.up_url, "device-token"))
        self.front = await serve(front, "127.0.0.1", 0)
        self.front_url = f"ws://127.0.0.1:{self.front.sockets[0].getsockname()[1]}/mouth"

    async def asyncTearDown(self):
        self.front.close(); self.up.close()
        await self.front.wait_closed(); await self.up.wait_closed()

    async def test_frames_flow_both_ways_with_the_token(self):
        async with connect(self.front_url) as c:
            await c.send('{"type":"hello"}')
            self.assertEqual(await c.recv(), '{"type":"hello"}')
            await c.send(b"SPK1\x00\x01")
            self.assertEqual(await c.recv(), b"SPK1\x00\x01")
        self.assertEqual(self.seen_headers, ["Bearer device-token"])

    async def test_device_close_closes_upstream(self):
        async with connect(self.front_url) as c:
            await c.send("x"); await c.recv()
        await asyncio.wait_for(self.up_closed.wait(), 3)

    async def test_a_dropped_device_leaves_no_unretrieved_task_errors(self):
        import gc
        complaints = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: complaints.append(ctx.get("message")))
        c = await connect(self.front_url)
        await c.send("x"); await c.recv()
        c.transport.abort()                                    # no close frame: the pump fails
        for _ in range(100):
            if self.results:
                break
            await asyncio.sleep(0.02)
        gc.collect(); await asyncio.sleep(0.05); gc.collect()
        self.assertEqual(self.results, ["device_closed"])
        self.assertEqual([m for m in complaints if "never retrieved" in (m or "")], [])

    async def test_upstream_down_closes_the_device_side(self):
        self.up.close(); await self.up.wait_closed()
        async with connect(self.front_url) as c:
            with self.assertRaises(ConnectionClosed) as cm:
                await asyncio.wait_for(c.recv(), 5)
        self.assertEqual(cm.exception.rcvd.code, 1011)


if __name__ == "__main__":
    unittest.main()
