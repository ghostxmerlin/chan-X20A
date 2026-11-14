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

    def test_direction_is_only_stored_nothing_comes_back(self):
        """Step 1b (owner 2026-11-14): DualKey is for ratings; a joystick push is relayed for mind to store and the
        DualKey gets its ack only, no mode message (no joystick light)."""
        posted = []
        def post(body):
            posted.append(body)
            return 200, {"sid": "g2"}
        hello = {"type": "hello", "device": "dualkey-ae1c", "boot": "b7"}
        sig = {"type": "signal", "seq": 5, "age_ms": 0, "kind": "mode", "value": {"direction": "left"}}
        ws, link = self.run_link([hello, sig, {"type": "ping"}], post)
        self.assertEqual(ws.sent, [{"type": "hello", "ok": True}, {"type": "ack", "seq": 5}])
        self.assertEqual(posted[0]["kind"], "mode")
        self.assertFalse(hasattr(link, "push_mode"))


if __name__ == "__main__":
    unittest.main()
