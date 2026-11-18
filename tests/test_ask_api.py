"""The ear's local request API for mind's tools (server/ear/ask_api.py, L1 2026-11-18)."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import ask_api  # noqa: E402


class Switch:
    on = True


class FakeHub:
    def __init__(self):
        self.switch, self.crowd, self.said, self.quiet = Switch(), True, [], False
        self.look_waiters, self.scene = [], None

    def in_crowd(self): return self.crowd
    def quiet_on(self): return self.quiet
    def clock(self): return 100.0
    def say(self, text, trace=None): self.said.append(text); return "sent"
    def stop_speaking(self): return True
    def faces_now(self): return [{"who": "主人", "known": True, "gender": None, "age": None}]
    def scene_now(self): return self.scene
    def set_quiet(self, level, minutes=None, trace=None): return None if level == "off" else 100.0 + (minutes or 240) * 60
    def set_role(self, role, minutes=None, trace=None): return 100.0 + 7200 if role else None
    def set_length(self, mode, trace=None): return None if mode == "normal" else 700.0


class FakeBurst:
    def __init__(self, hub): self.hub, self.ok = hub, True
    def allowed(self): return self.ok
    def ask(self, trigger): return 1.0


class AskApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = FakeHub()
        self.burst = FakeBurst(self.hub)
        self.asks = ask_api.Asks(hub=self.hub, burst=self.burst, searcher=lambda q: f"搜到：{q}",
                                 wall_ms=lambda: 1_000_000)
        self.server = await ask_api.serve(self.asks, "tok", "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def post(self, body, token="tok", path="/ask"):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        data = json.dumps(body, ensure_ascii=False).encode()
        w.write(f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n"
                f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        await w.drain()
        raw = await r.read()
        w.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        return int(head.split()[1]), json.loads(body)

    async def test_auth_and_routes(self):
        self.assertEqual((await self.post({"name": "who_is_here"}, token="bad"))[0], 401)
        self.assertEqual((await self.post({"name": "who_is_here"}, path="/x"))[0], 404)
        st, out = await self.post({"name": "nope"})
        self.assertEqual((st, out["error"]["code"]), (200, "not_available"))

    async def test_who_say_stop_search(self):
        _, out = await self.post({"name": "who_is_here"})
        self.assertEqual(out["data"]["faces"][0]["who"], "主人")
        _, out = await self.post({"name": "say", "args": {"text": "我在呢"}})
        self.assertTrue(out["ok"])
        self.assertEqual(self.hub.said, ["我在呢"])
        self.hub.quiet = True
        _, out = await self.post({"name": "say", "args": {"text": "又来"}})
        self.assertEqual(out["error"]["code"], "refused")
        self.hub.switch.on = False
        _, out = await self.post({"name": "say", "args": {"text": "又来"}})
        self.assertEqual(out["error"]["code"], "switch_off")
        _, out = await self.post({"name": "web_search", "args": {"query": "比特币价格"}})
        self.assertEqual(out["data"]["answer"], "搜到：比特币价格")
        _, out = await self.post({"name": "set_quiet", "args": {"level": "low", "minutes": 30}})
        self.assertEqual(out["data"]["minutes"], 30)

    async def test_look_now_waits_for_the_next_look_or_falls_back(self):
        task = asyncio.create_task(self.post({"name": "look_now", "args": {}}))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if self.hub.look_waiters:
                break
        self.hub.look_waiters[0][1].set_result("桌上有一个红杯子")
        _, out = await task
        self.assertEqual(out["data"], {"answer": "桌上有一个红杯子", "fresh": True, "photo_ago": "刚刚"})
        self.burst.ok = False
        self.hub.scene = (1_000_000 - 30_000, "主人坐在桌前")
        _, out = await self.post({"name": "look_now"})
        self.assertEqual((out["data"]["answer"], out["data"]["fresh"]), ("主人坐在桌前", False))
        self.hub.scene = None
        _, out = await self.post({"name": "look_now"})
        self.assertEqual(out["error"]["code"], "not_available")

    async def test_paused(self):
        self.asks.paused = lambda: ["budget"]
        _, out = await self.post({"name": "web_search", "args": {"query": "x"}})
        self.assertEqual(out["error"]["code"], "paused")


if __name__ == "__main__":
    unittest.main()


class VoiceBookTest(unittest.IsolatedAsyncioTestCase):
    async def test_set_voice_loads_the_matching_persona(self):
        import os
        import tempfile
        d = tempfile.mkdtemp()
        json.dump({"voices": {"ly01": {"persona": "luye.md", "names": ["鹿野"]},
                              "me01": {"persona": "owner.md", "names": ["主人的声音", "我的声音"]}}},
                  open(os.path.join(d, "voices.json"), "w"), ensure_ascii=False)
        open(os.path.join(d, "owner.md"), "w").write("你是 hello，借用主人的声音。")
        open(os.path.join(d, "tts.json"), "w").write(json.dumps({"voices": {"me01": {"voice": "vid-me"}}}))
        got = []

        class Hub(FakeHub):
            def set_voice(self, key, vid, persona, fillers, search, trace=None):
                got.append((key, vid, persona))
        asks = ask_api.Asks(hub=Hub(), voices=ask_api.VoiceBook(d, os.path.join(d, "tts.json"), os.path.join(d, "fillers")))
        out = await asks.set_voice({"voice": "我的声音", "style": "急一点"})
        self.assertEqual((out["voice"], out["persona"]), ("me01", "owner.md"))
        self.assertEqual(got[0][:2], ("me01", "vid-me"))
        self.assertIn("借用主人的声音", got[0][2])
        self.assertIn("【这会儿的语气】急一点", got[0][2])
        with self.assertRaises(ask_api.AskError):
            await asks.set_voice({"voice": "无限"})
