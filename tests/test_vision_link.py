import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import vision_link  # noqa: E402


class FakeDB:
    def __init__(self):
        self.calls = []

    def session_open(self, session, meta):
        self.calls.append(("open", session, meta))

    def session_update(self, session, fields):
        self.calls.append(("update", session, fields))

    def vision(self, session, msg, received_ms):
        self.calls.append(("vision", session, msg))


class FakeWS:
    def __init__(self, messages):
        self.inbox, self.sent, self.closed = list(messages), [], None

    async def recv(self):
        if not self.inbox:
            raise ConnectionError("closed")
        item = self.inbox.pop(0)
        if item is None:
            await asyncio.sleep(3600)
        return item

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class VisionLinkTest(unittest.TestCase):
    def test_hello_opens_a_vision_session_and_events_are_stored(self):
        db = FakeDB()
        ws = FakeWS([json.dumps({"type": "hello", "source": "stackchan-7c../x", "device": {"firmware": "f"}}),
                     json.dumps({"type": "vision", "event": "face_found", "seq": 1, "face": 3}),
                     json.dumps({"type": "ping"}),
                     json.dumps({"type": "vision", "event": "face_named", "seq": 2, "face": 3, "name": "me"})])
        link = asyncio.run(vision_link.serve_link(ws, db, 7, "1.2.3.4:5"))
        opened = db.calls[0]
        self.assertEqual(opened[0], "open")
        self.assertRegex(opened[1], r"^\d{8}/\d{6}-stackchan-7cx-v7$")          # source sanitised
        self.assertEqual(opened[2]["kind"], "vision")
        self.assertEqual([c[2]["event"] for c in db.calls if c[0] == "vision"], ["face_found", "face_named"])
        self.assertEqual(db.calls[-1][0], "update"); self.assertEqual(db.calls[-1][2]["vision_events"], 2)
        self.assertEqual(link.events, 2); self.assertEqual(ws.sent, [])

    def test_errors_are_answered_and_events_before_hello_rejected(self):
        db = FakeDB()
        ws = FakeWS(["not json", json.dumps({"type": "vision", "event": "face_found"}),
                     json.dumps({"type": "hello", "source": "s"}), json.dumps({"type": "vision"}),
                     json.dumps({"type": "bogus"})])
        asyncio.run(vision_link.serve_link(ws, db, 1, "p"))
        self.assertEqual([m["message"] for m in ws.sent],
                         ["bad json", "hello first", "vision needs an event name", "unknown type bogus"])
        self.assertFalse([c for c in db.calls if c[0] == "vision"])

    def test_silent_link_is_closed(self):
        old = vision_link.IDLE_TIMEOUT
        vision_link.IDLE_TIMEOUT = 0.05
        try:
            ws = FakeWS([json.dumps({"type": "hello", "source": "s"}), None])
            asyncio.run(vision_link.serve_link(ws, None, 1, "p"))
            self.assertEqual(ws.closed, (1000, "idle"))
        finally:
            vision_link.IDLE_TIMEOUT = old


if __name__ == "__main__":
    unittest.main()
