"""Ear /signal relay for the DualKey (2026-11-13): hello first, each signal forwarded to mind, ack after mind accepts."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import signal_link  # noqa: E402
from test_vision_link import FakeWS  # noqa: E402


class SignalLinkTest(unittest.TestCase):
    def run_link(self, messages, post):
        ws = FakeWS([json.dumps(m) for m in messages])
        link = asyncio.run(signal_link.serve_link(ws, post, "1.2.3.4:5"))
        return ws, link

    def test_hello_then_signals_are_forwarded_and_acked(self):
        posted = []
        def post(body):
            posted.append(body)
            return 200, {"sid": "g1"}
        hello = {"type": "hello", "device": "dualkey-ae1c", "fw": "dualkey-0.1.0", "boot": "b7"}
        sig = {"type": "signal", "seq": 3, "age_ms": 120, "kind": "rating", "value": {"polarity": 1, "count": 2, "presses_ms": [0, 150], "capped": False}}
        ws, link = self.run_link([hello, sig, {"type": "ping"}], post)
        self.assertEqual(ws.sent[0], {"type": "hello", "ok": True})
        self.assertEqual(ws.sent[1], {"type": "ack", "seq": 3})
        self.assertEqual(posted[0]["source"], {"device": "dualkey-ae1c", "boot": "b7", "seq": 3, "fw": "dualkey-0.1.0"})
        self.assertEqual((posted[0]["kind"], posted[0]["age_ms"]), ("rating", 120))
        self.assertEqual(link.signals, 1)

    def test_signal_before_hello_is_refused(self):
        ws, _ = self.run_link([{"type": "signal", "seq": 1, "kind": "click", "value": {"type": "single"}}], lambda b: (200, {}))
        self.assertEqual(ws.sent[0]["type"], "error")

    def test_no_ack_when_mind_rejects_or_is_down(self):
        hello = {"type": "hello", "device": "dualkey-ae1c", "boot": "b7"}
        sig = {"type": "signal", "seq": 4, "kind": "mode", "value": {"direction": "up"}}
        ws, _ = self.run_link([hello, sig], lambda b: (503, {"error": "down"}))
        self.assertEqual([m["type"] for m in ws.sent], ["hello", "error"])
        ws, _ = self.run_link([hello, sig], lambda b: (400, {"error": "bad"}))
        self.assertEqual(ws.sent[1], {"type": "error", "seq": 4, "message": "bad"})

    def test_mode_message(self):
        ml = signal_link.SignalLink.mode_message
        self.assertEqual(ml({'mode': 'crowd'}), {'type': 'mode', 'current': 'crowd'})
        self.assertEqual(ml({'mode': 'idle'}), {'type': 'mode', 'current': 'idle'})
        self.assertIsNone(ml({'mode': 5}))
        self.assertIsNone(ml({}))

    def test_push_mode_dedupes_by_seq_and_never_raises(self):
        async def go():
            ws = FakeWS([])
            link = signal_link.SignalLink(lambda b: (200, {}), "p", ws=ws)
            self.assertTrue(link.mode_wants_first is False)       # not said hello yet
            link.device = "dualkey-ae1c"
            self.assertTrue(link.mode_wants_first)
            await link.push_mode({"seq": 3, "mode": "talk"})
            await link.push_mode({"seq": 3, "mode": "talk"})
            await link.push_mode({"seq": 4, "mode": "idle"})
            await link.push_mode({"seq": 3, "mode": "talk"}, force=True)
            self.assertEqual([m["current"] for m in ws.sent], ["talk", "idle", "talk"])
            self.assertFalse(link.mode_wants_first)

            class Dead(FakeWS):
                async def send(self, text):
                    raise ConnectionError("gone")
            dead = signal_link.SignalLink(lambda b: (200, {}), "p", ws=Dead([]))
            dead.device = "dualkey-x"
            await dead.push_mode({"seq": 1, "mode": "talk"})       # swallowed
            class Stuck(FakeWS):
                async def send(self, text):
                    await asyncio.sleep(3600)
            stuck = signal_link.SignalLink(lambda b: (200, {}), "p", ws=Stuck([]))
            stuck.device = "dualkey-x"
            stuck.SEND_TIMEOUT = 0.05
            await asyncio.wait_for(stuck.push_mode({"seq": 1, "mode": "talk"}), 1)
        asyncio.run(go())

    def test_hello_joins_hub_after_reply_and_leaves_at_end(self):
        class Hub:
            def __init__(self):
                self.links, self.events = set(), []
            def join(self, l):
                self.links.add(l); self.events.append("join")
            def leave(self, l):
                self.links.discard(l); self.events.append("leave")
        hub = Hub()
        ws = FakeWS([json.dumps({"type": "hello", "device": "dualkey-ae1c", "boot": "b7"})])
        asyncio.run(signal_link.serve_link(ws, lambda b: (200, {}), "p", hub=hub))
        self.assertEqual(ws.sent[0], {"type": "hello", "ok": True})
        self.assertEqual(hub.events, ["join", "leave"])
        self.assertEqual(hub.links, set())
        # a refused hello never joins
        hub2 = Hub()
        ws = FakeWS([json.dumps({"type": "hello", "device": "other"})])
        asyncio.run(signal_link.serve_link(ws, lambda b: (200, {}), "p", hub=hub2))
        self.assertEqual(hub2.events, [])

    def test_hub_pushes_mode_to_signal_link(self):
        import mouth_link
        async def go():
            calls = []
            def call(method, path, body, timeout):
                calls.append(path)
                return (200, {"seq": 7}) if path == "/mode/seq" else (200, {"snapshot": {"seq": 7, "mode": "crowd"}})
            hub = mouth_link.ModeHub(call)
            ws = FakeWS([])
            link = signal_link.SignalLink(lambda b: (200, {}), "p", ws=ws)
            link.device = "dualkey-ae1c"
            hub.join(link)
            await hub.tick()
            hub.close()
            self.assertEqual(ws.sent, [{"type": "mode", "current": "crowd"}])
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
