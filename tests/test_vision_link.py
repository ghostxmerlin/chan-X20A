import asyncio
import json
import os
import struct
import sys
import tempfile
import time
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

    def frame(self, doc):
        self.calls.append(("frame", doc))


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





JPEG = b"\xff\xd8" + b"jpegdata" * 100 + b"\xff\xd9"


def frame_msg(seq=5, t_us=1_000_000, now_us=1_500_000, jpeg=JPEG, magic=b"SVF1", head=None):
    h = head if head is not None else json.dumps({"type": "frame", "seq": seq, "t_us": t_us, "now_us": now_us,
                                                  "w": 320, "h": 240, "diff": 12, "bytes": len(jpeg)}).encode()
    return magic + struct.pack("<H", len(h)) + h + jpeg


class VisionFrameTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = FakeDB()
        self.link = vision_link.VisionLink(self.db, 3, "p", recordings_root=self.dir.name)
        self.link.on_text(json.dumps({"type": "hello", "source": "stackchan-x"}))

    def files(self):
        return [os.path.join(d, f) for d, _, fs in os.walk(self.dir.name) for f in fs]

    def test_frame_is_stored_as_a_file_and_indexed(self):
        before = int(time.time() * 1000)
        self.assertIsNone(self.link.on_binary(frame_msg()))
        doc = next(c[1] for c in self.db.calls if c[0] == "frame")
        self.assertEqual((doc["session"], doc["seq"], doc["t_us"], doc["w"], doc["h"], doc["diff"], doc["bytes"]),
                         (self.link.session, 5, 1_000_000, 320, 240, 12, len(JPEG)))
        self.assertFalse(os.path.isabs(doc["path"]))
        self.assertEqual(doc["path"], f"frames/{self.link.session}/1000000-5.jpg")
        with open(os.path.join(self.dir.name, doc["path"]), "rb") as f:
            self.assertEqual(f.read(), JPEG)
        self.assertTrue(before - 500 - 5 <= doc["at"] <= doc["received"] - 500 + 5)     # waited 0.5 s on the robot

    def test_bad_frames_are_rejected_and_nothing_written(self):
        bad = [frame_msg(magic=b"XXXX"), frame_msg(head=b"{" + b" " * 600 + b"}"), frame_msg(head=b"not json"),
               frame_msg(head=json.dumps({"type": "frame", "seq": "../x", "t_us": 1, "now_us": 1}).encode()),
               frame_msg(head=json.dumps({"type": "frame", "seq": 1, "t_us": "1", "now_us": 1}).encode()),
               frame_msg(jpeg=b"GIF89a"), b"SVF1"]
        for data in bad:
            reply = self.link.on_binary(data)
            self.assertEqual(reply["type"], "error", data[:12])
        early = vision_link.VisionLink(self.db, 4, "p", recordings_root=self.dir.name)
        self.assertEqual(early.on_binary(frame_msg())["type"], "error")
        self.assertEqual(self.files(), [])
        self.assertFalse([c for c in self.db.calls if c[0] == "frame"])

    def test_close_records_the_frame_count(self):
        self.link.on_binary(frame_msg(seq=1))
        self.link.on_binary(frame_msg(seq=2, t_us=2_000_000))
        self.link.close()
        self.assertEqual(self.db.calls[-1][2]["frames"], 2)

    def test_without_a_frames_root_frames_are_refused(self):
        link = vision_link.VisionLink(self.db, 5, "p")
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-x"}))
        self.assertEqual(link.on_binary(frame_msg())["type"], "error")

    def test_serve_link_routes_binary_messages(self):
        ws = FakeWS([json.dumps({"type": "hello", "source": "stackchan-x"}), frame_msg()])
        asyncio.run(vision_link.serve_link(ws, self.db, 9, "p", recordings_root=self.dir.name))
        self.assertEqual(len(self.files()), 1)


if __name__ == "__main__":
    unittest.main()
