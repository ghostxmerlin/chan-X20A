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

    def body_seen(self, b, part, links, device=None):
        self.calls.append(("body", b["id"], part, links))


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

    def test_a_snap_frame_goes_to_the_frame_hook_and_keeps_its_id(self):
        got = []
        link = vision_link.VisionLink(self.db, 5, "p", recordings_root=self.dir.name,
                                      on_frame=lambda head, jpeg, path, session: got.append((head, jpeg, path, session)))
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-x"}))
        head = json.dumps({"type": "frame", "seq": 9, "t_us": 2_000_000, "now_us": 2_100_000, "w": 320, "h": 240,
                           "diff": 0, "bytes": len(JPEG), "snap": 17}).encode()
        self.assertIsNone(link.on_binary(frame_msg(head=head)))
        self.assertIsNone(link.on_binary(frame_msg(seq=10)))                     # an ordinary frame: hook too, no snap
        self.assertEqual([(h.get("snap"), h["seq"]) for h, _, _, _ in got], [(17, 9), (None, 10)])
        self.assertEqual(got[0][1], JPEG)
        self.assertEqual(got[0][2], f"frames/{link.session}/2000000-9.jpg")
        self.assertEqual(got[0][3], link.session)
        docs = [c[1] for c in self.db.calls if c[0] == "frame"]
        self.assertEqual(docs[0]["snap"], 17)
        self.assertNotIn("snap", docs[1])

    def test_a_failing_frame_hook_never_costs_the_frame(self):
        def boom(*a):
            raise RuntimeError("x")
        link = vision_link.VisionLink(self.db, 6, "p", recordings_root=self.dir.name, on_frame=boom)
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-x"}))
        self.assertIsNone(link.on_binary(frame_msg()))
        self.assertEqual(link.frames, 1)

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

    def test_disk_error_answers_an_error_and_keeps_the_link(self):
        blocker = os.path.join(self.dir.name, "frames")
        with open(blocker, "w") as f:                  # a file where the frames folder should be
            f.write("x")
        reply = self.link.on_binary(frame_msg())
        self.assertEqual(reply["type"], "error")
        self.assertFalse([c for c in self.db.calls if c[0] == "frame"])
        self.assertFalse([p for p in self.files() if p.endswith(".part")])
        ws = FakeWS([json.dumps({"type": "hello", "source": "stackchan-x"}), frame_msg(), json.dumps({"type": "vision", "event": "face_found", "seq": 1})])
        link = asyncio.run(vision_link.serve_link(ws, self.db, 10, "p", recordings_root=self.dir.name))
        self.assertEqual(link.events, 1)               # the event after the failed frame still arrived

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


class NameCarryTest(unittest.TestCase):
    """Owner 2026-11-15: a new face id at the same place within 2 s keeps the name; never a wrong name."""
    def setUp(self):
        self.c = vision_link.NameCarry()

    def ev(self, event, face, t, box=(100, 100, 100, 120), **kw):
        return self.c.feed({"type": "vision", "event": event, "face": face, "t_us": t, "box": list(box), **kw})

    def test_new_id_at_the_same_place_keeps_the_name(self):
        self.ev("face_found", 1, 0)
        self.ev("face_named", 1, 400_000, name="owner")
        self.ev("face_lost", 1, 5_000_000)
        out = self.ev("face_found", 2, 5_800_000, box=(120, 95, 110, 125), gender="m")
        self.assertEqual((out["event"], out["name"], out["face"], out["carried"], out["from_face"], out["gender"]),
                         ("face_carried", "owner", 2, True, 1, "m"))
        self.ev("face_lost", 2, 9_000_000)                      # and it carries on again after that
        self.assertEqual(self.ev("face_found", 3, 9_500_000)["name"], "owner")

    def test_never_guesses(self):
        self.ev("face_named", 1, 0, name="owner")
        self.ev("face_lost", 1, 1_000_000)
        self.assertIsNone(self.ev("face_found", 2, 3_100_000))                   # too late
        self.ev("face_named", 3, 4_000_000, name="owner")
        self.ev("face_lost", 3, 5_000_000)
        self.assertIsNone(self.ev("face_found", 4, 5_500_000, box=(400, 100, 100, 120)))   # elsewhere
        self.assertIsNone(self.ev("face_found", 5, 5_600_000, box=(100, 100, 40, 50)))     # much smaller
        # Two named faces lost side by side: a new face fitting both gets nothing.
        c = self.c = vision_link.NameCarry()
        self.ev("face_named", 1, 0, name="a", box=(100, 100, 100, 120))
        self.ev("face_named", 2, 0, name="b", box=(130, 100, 100, 120))
        self.ev("face_lost", 1, 1_000_000, box=(100, 100, 100, 120))
        self.ev("face_lost", 2, 1_000_000, box=(130, 100, 100, 120))
        self.assertIsNone(self.ev("face_found", 3, 1_200_000, box=(115, 100, 100, 120)))
        # The name is still on a face in view: no second copy.
        c = self.c = vision_link.NameCarry()
        self.ev("face_named", 1, 0, name="a")
        self.ev("face_named", 2, 0, name="a", box=(300, 100, 100, 120))
        self.ev("face_lost", 1, 1_000_000)
        self.assertIsNone(self.ev("face_found", 3, 1_100_000))
        # Two new faces compete for one lost name: only the first could have it, the second never.
        c = self.c = vision_link.NameCarry()
        self.ev("face_named", 1, 0, name="a")
        self.ev("face_lost", 1, 1_000_000)
        self.ev("face_found", 7, 1_050_000, box=(400, 300, 100, 120))           # unrelated, elsewhere
        self.assertEqual(self.ev("face_found", 8, 1_100_000)["name"], "a")
        self.ev("vision_off", None, 1_200_000)
        self.ev("face_named", 1, 2_000_000, name="a")
        self.ev("face_lost", 1, 3_000_000)
        self.ev("face_found", 9, 3_100_000, box=(110, 100, 100, 120))
        self.ev("face_lost", 9, 3_150_000)
        # face 9 took it (carried) and was lost again: carrying from 9 is allowed, it is the same chain

    def test_link_stores_the_carried_event_too(self):
        db = FakeDB()
        link = vision_link.VisionLink(db, 6, "p")
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-x"}))
        for m in ({"event": "face_named", "face": 1, "t_us": 0, "box": [100, 100, 100, 120], "name": "a"},
                  {"event": "face_lost", "face": 1, "t_us": 1_000_000, "box": [100, 100, 100, 120]},
                  {"event": "face_found", "face": 2, "t_us": 1_500_000, "box": [105, 100, 100, 120]}):
            link.on_text(json.dumps({"type": "vision", **m}))
        stored = [c[2] for c in db.calls if c[0] == "vision"]
        self.assertEqual([(e.get("event"), e.get("carried")) for e in stored][-2:], [("face_found", None), ("face_carried", True)])


class M12EventTest(unittest.TestCase):
    """collector-0.5.02 (m12d 0.2.0 events, owner 2026-11-17 先呈现事件出来): m12_ev is stored, logged, reacts to nothing."""
    def link(self, **kw):
        db = FakeDB()
        link = vision_link.VisionLink(db, 3, "p", **kw)
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-x"}))
        return db, link

    def test_events_are_stored_and_logged_one_row_each(self):
        from unittest.mock import patch
        db, link = self.link()
        lines = [{"ev": "nod", "seq": 5, "t_us": 9_000_000, "now_us": 9_100_000, "f": 8542, "face": 3, "n": 2},
                 {"ev": "wake", "seq": 6, "t_us": 9_500_000, "f": 9000, "area": [100, 50, 200, 300], "amt": 0.12, "cd": 30,
                  "faces": [{"box": [300, 100, 80, 100], "name": "me", "sim": 0.71}], "persons": [[250, 60, 200, 400]]},
                 {"ev": "near", "seq": 7, "t_us": 9_600_000, "face": 3, "d": [1.2, 0.7]},
                 {"ev": "zzz_new", "seq": 8, "t_us": 9_700_000}]
        with patch.object(vision_link.ear_log, "emit") as emit, patch.object(vision_link.ear_log, "robot_state") as state:
            for m in lines:
                self.assertIsNone(link.on_text(json.dumps({"type": "vision", "event": "m12_ev", **m})))
        stored = [c[2] for c in db.calls if c[0] == "vision"]
        self.assertEqual([e["ev"] for e in stored], ["nod", "wake", "near", "zzz_new"])
        self.assertEqual(link.events, 4)
        state.assert_not_called()
        self.assertEqual([c.args[0] for c in emit.call_args_list], ["seen.m12"] * 4)
        sums = [c.kwargs["sum"] for c in emit.call_args_list]
        self.assertEqual(sums[0], "点头 脸#3 2 下")
        self.assertEqual(sums[1], "待机唤醒 脸 1 人形 1（me）")
        self.assertEqual(sums[2], "靠近 脸#3 1.2→0.7 米")
        self.assertEqual(sums[3], "M12 zzz_new")
        first = emit.call_args_list[0].kwargs
        self.assertEqual(first["by"], "m12"); self.assertEqual(first["ref"], {"session": link.session})
        self.assertEqual(first["extra"], {"device": "stackchan-x", "ev": "nod", "f": 8542, "face": 3, "n": 2})
        self.assertNotIn("now_us", emit.call_args_list[1].kwargs["extra"])
        self.assertEqual(emit.call_args_list[1].kwargs["extra"]["faces"][0]["name"], "me")

    def test_no_hook_reacts_to_them(self):
        seen = []
        _, link = self.link(on_event=seen.append)
        link.on_text(json.dumps({"type": "vision", "event": "m12_ev", "ev": "look", "t_us": 1, "face": 1}))
        self.assertEqual(len(seen), 1)                              # handed on like any vision event ...
        self.assertIsNone(link.carry.feed(seen[0]))                 # ... the name carry-over ignores it

    def test_sums_for_every_kind_of_field(self):
        s = vision_link.m12_ev_sum
        self.assertEqual(s({"ev": "light", "on": False, "lum": [120, 20]}), "关灯")
        self.assertEqual(s({"ev": "light", "on": True}), "开灯")
        self.assertEqual(s({"ev": "uncover", "dur": 3.4, "lum": [8, 139]}), "拿开遮挡 3.4 秒")
        self.assertEqual(s({"ev": "tilt", "face": 3, "side": "l"}), "歪头 脸#3 向画面左")
        self.assertEqual(s({"ev": "leave", "person": 18, "dur": 12.5}), "有人走了 人形#18 12.5 秒")
        self.assertEqual(s({"ev": "wake", "pan": True, "hot": True, "cd": 60}), "待机唤醒 转动 过热未分析")
        self.assertEqual(s({"ev": "wake", "faces": [{"box": [1, 1, 9, 9]}] * 3, "faces_seen": 5, "persons": []}),
                         "待机唤醒 脸 5 人形 0")
        self.assertEqual(s({}), "M12 事件")
        self.assertEqual(s({"ev": "nod", "n": True, "face": "x"}), "点头")         # wrong types are left out
