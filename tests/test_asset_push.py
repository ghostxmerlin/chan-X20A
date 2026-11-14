"""ear asset_push.py (collector-0.4.96: SD assets over /mouth). The pusher runs against a fake robot (FakeLink) that
answers like collector/mouth_assets.h; the loopback control + CLI run over real websockets when installed:

    python3 -B -m unittest discover -s tests -p 'test_asset_push.py' -v
    .private/venv-ear/bin/python -B -m unittest discover -s tests -p 'test_asset_push.py' -v   (control tests too)
"""
import asyncio
import hashlib
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import asset_push  # noqa: E402

try:
    from websockets.asyncio.server import serve
    HAVE_WS = True
except ImportError:
    HAVE_WS = False

WINDOW, ACK_EVERY = 32768, 8192


class FakeLink:
    """The robot end of /mouth for one pusher: catalog check, window, acks, results (mouth_assets.h)."""

    def __init__(self, catalog, *, on_sd=(), ack=True, refuse_at=None):
        self.catalog, self.on_sd, self.ack, self.refuse_at = catalog, set(on_sd), ack, refuse_at
        self.speaking = False
        self.sent_text, self.chunks, self.max_in_flight = [], [], 0
        self.cur = None
        self.pusher = asset_push.AssetPusher(self.send, lambda: self.speaking, reply_timeout_s=0.5,
                                             install_timeout_s=0.5, pause_max_s=2.0, poll_s=0.01)
        self.chunks_while_speaking = 0
        self.gone = False

    def reply(self, m):
        asyncio.get_running_loop().call_soon(self.pusher.on_message, m)

    def result(self, ok, reason=None, unchanged=False):
        c = self.cur
        m = {"type": "asset.result", "id": c["id"], "part": c["part"], "ok": ok, "unchanged": unchanged, "bytes": c["bytes"]}
        if reason:
            m["reason"] = reason
        self.reply(m)
        self.cur = None

    async def send(self, message):
        if self.gone:
            raise ConnectionError("robot_gone")
        if isinstance(message, bytes):
            magic, tid, offset = struct.unpack_from("<4sII", message)
            assert magic == b"AST1" and len(message) <= 1600
            if self.speaking:
                self.chunks_while_speaking += 1
            c = self.cur
            if c is None or tid != c["id"]:
                return
            self.chunks.append((offset, len(message) - 12))
            assert offset == c["received"], "chunks in order"
            c["data"] += message[12:]
            c["received"] += len(message) - 12
            self.max_in_flight = max(self.max_in_flight, c["received"] - c["acked"])
            assert c["received"] - c["acked"] <= WINDOW, "window kept"
            if self.refuse_at is not None and c["received"] >= self.refuse_at:
                self.result(False, "timeout")
                return
            if self.ack and (c["received"] - c["acked"] >= ACK_EVERY or c["received"] == c["bytes"]):
                c["acked"] = c["received"]
                self.reply({"type": "asset.ack", "id": tid, "bytes": c["received"], "window": WINDOW})
            return
        m = json.loads(message)
        self.sent_text.append(m)
        if m["type"] == "asset.begin":
            spec = self.catalog.get(m["part"])
            self.cur = {"id": m["id"], "part": m["part"], "bytes": m["bytes"], "received": 0, "acked": 0, "data": b""}
            if spec is None:
                self.result(False, "unknown_part")
            elif m["bytes"] != len(spec) or m["sha256"] != hashlib.sha256(spec).hexdigest():
                self.result(False, "size_mismatch")
            elif m["part"] in self.on_sd:
                self.result(True, unchanged=True)
            else:
                self.reply({"type": "asset.ack", "id": m["id"], "bytes": 0, "window": WINDOW})
        elif m["type"] == "asset.end" and self.cur and m["id"] == self.cur["id"]:
            ok = self.cur["data"] == self.catalog[self.cur["part"]]
            self.result(ok, None if ok else "asset_invalid")
        elif m["type"] == "asset.abort":
            self.cur = None


def blob(n, seed=1):
    return bytes((i * 31 + seed) & 0xFF for i in range(n))


class PusherTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        asset_push.log = lambda *a, **k: None
        self.font = blob(100_000)
        self.lic = blob(4463, 7)
        self.catalog = {"cjk16.0": self.font, "license": self.lic}

    async def test_full_push_window_and_progress(self):
        link = FakeLink(self.catalog)
        seen = []
        res = await link.pusher.push("cjk16.0", self.font, progress=lambda a, t: seen.append((a, t)))
        self.assertTrue(res["ok"] and not res["unchanged"])
        self.assertEqual(b"".join(self.font[o:o + n] for o, n in link.chunks), self.font)
        self.assertTrue(all(n <= asset_push.CHUNK for _, n in link.chunks))
        self.assertLessEqual(link.max_in_flight, WINDOW)
        self.assertEqual(seen[-1], (len(self.font), len(self.font)))
        types = [m["type"] for m in link.sent_text]
        self.assertEqual(types, ["asset.begin", "asset.end"])
        self.assertEqual(link.sent_text[0]["sha256"], hashlib.sha256(self.font).hexdigest())
        self.assertEqual(link.pusher.pushed, 1)

    async def test_unchanged_sends_no_data(self):
        link = FakeLink(self.catalog, on_sd={"license"})
        res = await link.pusher.push("license", self.lic)
        self.assertTrue(res["ok"] and res["unchanged"])
        self.assertEqual(link.chunks, [])
        self.assertEqual([m["type"] for m in link.sent_text], ["asset.begin"])

    async def test_refused_at_begin(self):
        link = FakeLink(self.catalog)
        res = await link.pusher.push("cjk24.0", self.font)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "unknown_part")
        self.assertEqual(link.chunks, [])

    async def test_local_checks(self):
        link = FakeLink(self.catalog)
        self.assertEqual((await link.pusher.push("../x", self.lic))["reason"], "bad_part")
        self.assertEqual((await link.pusher.push("license", b""))["reason"], "bad_size")
        self.assertEqual((await link.pusher.push("license", blob(asset_push.MAX_BYTES + 1)))["reason"], "bad_size")
        self.assertEqual((await link.pusher.push("license", self.lic, "00" * 32))["reason"], "sha256_mismatch_local")
        self.assertEqual(link.sent_text, [])

    async def test_waits_while_hello_speaks_before_begin(self):
        link = FakeLink(self.catalog)
        link.speaking = True
        task = asyncio.create_task(link.pusher.push("license", self.lic))
        await asyncio.sleep(0.1)
        self.assertEqual(link.sent_text, [])               # nothing goes down while hello speaks
        link.speaking = False
        res = await task
        self.assertTrue(res["ok"])

    async def test_pauses_chunks_mid_transfer(self):
        link = FakeLink(self.catalog)
        orig = link.send
        sent = [0]

        async def send(message):
            await orig(message)
            if isinstance(message, bytes):
                sent[0] += 1
                if sent[0] == 5:                             # a tts.start arrives: hello speaks for a while
                    link.speaking = True
                    asyncio.get_running_loop().call_later(0.15, setattr, link, "speaking", False)
        link.pusher.send = send
        res = await link.pusher.push("cjk16.0", self.font)
        self.assertTrue(res["ok"])
        self.assertEqual(link.chunks_while_speaking, 0)     # nothing more goes down until hello is quiet

    async def test_speech_too_long_aborts(self):
        link = FakeLink(self.catalog)
        link.pusher.pause_max_s = 0.1
        link.speaking = True
        res = await link.pusher.push("license", self.lic)
        self.assertEqual(res["reason"], "robot_speaking")
        self.assertEqual(link.sent_text, [])

    async def test_no_ack_aborts(self):
        link = FakeLink(self.catalog, ack=False)
        link.pusher.reply_timeout_s = 0.2
        res = await link.pusher.push("cjk16.0", self.font)
        self.assertEqual(res["reason"], "no_ack")
        self.assertEqual(link.sent_text[-1]["type"], "asset.abort")
        self.assertLessEqual(sum(n for _, n in link.chunks), WINDOW)

    async def test_robot_result_mid_transfer(self):
        link = FakeLink(self.catalog, refuse_at=20000)
        res = await link.pusher.push("cjk16.0", self.font)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "timeout")

    async def test_link_closed_mid_transfer(self):
        link = FakeLink(self.catalog, ack=False)

        async def drop():
            await asyncio.sleep(0.05)
            link.pusher.link_closed()
        asyncio.create_task(drop())
        res = await link.pusher.push("cjk16.0", self.font)
        self.assertEqual(res["reason"], "robot_gone")
        self.assertEqual((await link.pusher.push("license", self.lic))["reason"], "robot_gone")

    async def test_send_fails(self):
        link = FakeLink(self.catalog)
        link.gone = True
        self.assertEqual((await link.pusher.push("license", self.lic))["reason"], "robot_gone")

    async def test_stray_messages_dropped(self):
        link = FakeLink(self.catalog)
        link.pusher.on_message({"type": "asset.result", "id": 123, "ok": True})   # no transfer: dropped
        res = await link.pusher.push("license", self.lic)
        self.assertTrue(res["ok"])
        link.pusher.on_message({"type": "asset.ack", "id": res["id"], "bytes": 5})  # late: dropped
        res2 = await link.pusher.push("license", self.lic)
        self.assertTrue(res2["ok"])
        self.assertNotEqual(res["id"], res2["id"])

    async def test_one_at_a_time(self):
        link = FakeLink(self.catalog)
        a = asyncio.create_task(link.pusher.push("cjk16.0", self.font))
        b = asyncio.create_task(link.pusher.push("license", self.lic))
        ra, rb = await asyncio.gather(a, b)
        self.assertTrue(ra["ok"] and rb["ok"])
        begins = [m for m in link.sent_text if m["type"] in ("asset.begin", "asset.end")]
        self.assertEqual([m["type"] for m in begins], ["asset.begin", "asset.end", "asset.begin", "asset.end"])


class ItemsTest(unittest.TestCase):
    def test_read_items_plain_names_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.bin").write_bytes(b"x" * 10)
            self.assertEqual(asset_push.read_items(d, ["license=a.bin"]), [("license", "a.bin", b"x" * 10)])
            for bad in ["license=../a.bin", "license=/etc/passwd", "Lic=a.bin", "license", "license=.hidden"]:
                with self.assertRaises(SystemExit):
                    asset_push.read_items(d, [bad])

    def test_authorized(self):
        self.assertTrue(asset_push.authorized("Bearer t0k", "t0k"))
        self.assertFalse(asset_push.authorized("Bearer x", "t0k"))
        self.assertFalse(asset_push.authorized("", ""))        # no token on the ear: the control stays shut


class MouthLinkAssetsTest(unittest.IsolatedAsyncioTestCase):
    async def test_hello_flag_and_audio_busy(self):
        import mouth_link

        class Client:
            def __init__(self, hello):
                self.hello, self.out = hello, []

            async def recv(self):
                return json.dumps(self.hello)

            async def send(self, m):
                self.out.append(m)
        link = mouth_link.MouthLink(Client({"type": "hello", "boot_id": "b", "assets": 1}), upstream_url="", token="",
                                    connect=lambda *a, **k: None)
        self.assertIsNone(await link._hello())
        self.assertIsNotNone(link.assets)
        self.assertFalse(link.audio_busy())
        link.tts_active.add(3)
        self.assertTrue(link.audio_busy())
        old = mouth_link.MouthLink(Client({"type": "hello", "boot_id": "b"}), upstream_url="", token="",
                                   connect=lambda *a, **k: None)
        await old._hello()
        self.assertIsNone(old.assets)                          # 0.4.95 and older: never sent asset frames


@unittest.skipUnless(HAVE_WS, "needs websockets")
class ControlTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asset_push.log = lambda *a, **k: None
        self.lic, self.font = blob(4463, 7), blob(60_000)
        self.robot = FakeLink({"license": self.lic, "cjk16.0": self.font})

        class Link:
            conn = 1
        self.link = Link()
        self.link.assets = self.robot.pusher
        self.server = await serve(lambda ws: asset_push.serve_control(ws, lambda: self.link, "secret"), "127.0.0.1", 0,
                                  max_size=asset_push.MAX_BYTES + 4096)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/assets"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_cli_pushes_in_order(self):
        lines = []
        failed = await asset_push.run_cli(self.url, "secret", [("license", "LICENSE-OFL.txt", self.lic),
                                                              ("cjk16.0", "cjk16.0.bin", self.font)], out=lines.append)
        self.assertEqual(failed, 0)
        self.assertIn("1/2 license", lines[0] if not lines[0].startswith(" ") else lines[1])
        self.assertTrue(any(line.startswith("2/2 cjk16.0") and "installed and verified" in line for line in lines))
        parts = [m["part"] for m in self.robot.sent_text if m["type"] == "asset.begin"]
        self.assertEqual(parts, ["license", "cjk16.0"])
        self.assertFalse(any("secret" in line for line in lines))

    async def test_stops_after_a_failure(self):
        lines = []
        failed = await asset_push.run_cli(self.url, "secret", [("nope", "x.bin", b"x"), ("license", "l", self.lic)],
                                          out=lines.append)
        self.assertEqual(failed, 1)
        self.assertEqual([m["part"] for m in self.robot.sent_text if m["type"] == "asset.begin"], ["nope"])

    async def test_bad_token(self):
        from websockets.exceptions import ConnectionClosed
        with self.assertRaises(ConnectionClosed):
            await asset_push.run_cli(self.url, "wrong", [("license", "l", self.lic)], out=lambda s: None)
        self.assertEqual(self.robot.sent_text, [])

    async def test_no_robot_and_old_firmware(self):
        self.link = None
        lines = []
        self.assertEqual(await asset_push.run_cli(self.url, "secret", [("license", "l", self.lic)], out=lines.append), 1)
        self.assertIn("no_robot", lines[-1])

        class Old:
            conn, assets = 2, None
        self.link = Old()
        await asset_push.run_cli(self.url, "secret", [("license", "l", self.lic)], out=lines.append)
        self.assertIn("robot_firmware_has_no_assets", lines[-1])


if __name__ == "__main__":
    unittest.main()
