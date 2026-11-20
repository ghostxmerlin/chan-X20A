"""Body sense (server/ear/body_sense.py, collector-0.5.12): summaries kept and sampled into hello.body, events stored +
logged + pushed to the hub, the 身体感觉 line for 【你的眼睛】, and the /vision link handing body messages over."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "ear"))

import body_sense  # noqa: E402
import vision_link  # noqa: E402

T0 = 1_794_300_000_000


class FakeDb:
    def __init__(self):
        self.rows = []

    def body(self, doc):
        self.rows.append(doc)

    def session_open(self, *a):
        pass

    def body_seen(self, *a):
        pass


class Hub:
    def __init__(self):
        self.n = 0

    def body_changed(self):
        self.n += 1


def summary(**kw):
    return {"type": "body", "seq": 1, "t_us": 5_000_000, "now_us": 5_000_000, "n": 50, "pitch": -2.5, "roll": 1.0,
            "acc_g": 1.0, "dev_g": 0.01, "gyro": 2, "moved": 0.0, "still": True, "held": False, "self": False,
            "prox": 40, "prox_base": 39, "near": False, "lux": 120.0, "mag": [1, 2, 3], **kw}


class BodySenseTest(unittest.TestCase):
    def setUp(self):
        self.now = [T0]
        self.db, self.hub = FakeDb(), Hub()
        self.b = body_sense.BodySense(db=self.db, clock_ms=lambda: self.now[0], hub=self.hub)
        self.logs = []
        p = mock.patch.object(body_sense.ear_log, "emit", lambda kind, **f: self.logs.append((kind, f)))
        p.start()
        self.addCleanup(p.stop)

    def test_summaries_are_kept_and_sampled(self):
        self.b.on_summary(summary(), "s1", "dev")
        self.assertEqual(self.b.latest["prox"], 40)
        self.assertEqual(self.b.latest["mag"], [1, 2, 3])
        self.assertEqual(len(self.db.rows), 1)
        self.now[0] += 1000
        self.b.on_summary(summary(seq=2), "s1", "dev")
        self.assertEqual(len(self.db.rows), 1)                     # same flags, within 10 s: not stored
        self.now[0] += 1000
        self.b.on_summary(summary(seq=3, held=True, still=False), "s1", "dev")
        self.assertEqual(len(self.db.rows), 2)                     # held changed: stored at once
        self.now[0] += 10_000
        self.b.on_summary(summary(seq=4, held=True, still=False), "s1", "dev")
        self.assertEqual(len(self.db.rows), 3)
        self.assertEqual(self.db.rows[-1]["kind"], "sum")
        self.assertEqual(self.logs, [])                            # summaries never go to the run log

    def test_bad_fields_are_dropped(self):
        self.b.on_summary(summary(pitch="x", held="yes", mag=[1, 2]), "s1", "dev")
        self.assertNotIn("pitch", self.b.latest)
        self.assertNotIn("held", self.b.latest)
        self.assertNotIn("mag", self.b.latest)

    def test_events_are_stored_logged_and_pushed(self):
        self.b.on_event({"type": "body_ev", "ev": "put_down", "seq": 7, "held_ms": 4200, "t_us": 1, "now_us": 1},
                        "s1", "dev")
        self.assertEqual(self.db.rows[-1]["kind"], "ev")
        self.assertEqual(self.db.rows[-1]["held_ms"], 4200)
        self.assertEqual(self.logs[0][0], "seen.body")
        self.assertEqual(self.logs[0][1]["sum"], "被放下了（拿了 4 秒）")
        self.assertEqual(self.hub.n, 1)
        self.b.on_event({"type": "body_ev", "ev": "dance"}, "s1", "dev")    # an unknown event: dropped
        self.assertEqual((len(self.db.rows), len(self.logs), self.hub.n), (1, 1, 1))

    def test_the_line(self):
        self.assertIsNone(self.b.line())
        self.b.on_summary(summary(), "s1", "dev")
        self.assertIsNone(self.b.line())                           # sitting still: nothing worth saying
        self.b.on_event({"ev": "picked_up"}, "s1", "dev")
        self.now[0] += 12_000
        self.b.on_summary(summary(held=True, still=False), "s1", "dev")
        self.assertEqual(self.b.line(), "身体感觉：这会儿正被人拿着或挪动；被拿起来了（12 秒前）")
        self.b.on_event({"ev": "put_down", "held_ms": 15_000}, "s1", "dev")
        self.b.on_summary(summary(), "s1", "dev")
        self.assertEqual(self.b.line(), "身体感觉：被放下了（0 秒前，拿了 15 秒）；被拿起来了（12 秒前）")
        self.now[0] += 100_000                                      # past the window, summary stale
        self.assertIsNone(self.b.line())

    def test_near_now_and_stale_summary(self):
        self.b.on_summary(summary(near=True), "s1", "dev")
        self.assertEqual(self.b.line(), "身体感觉：这会儿有东西离身体很近")
        self.now[0] += 6000
        self.assertIsNone(self.b.line())

    def test_hub_failure_never_costs_the_event(self):
        class Bad:
            def body_changed(self):
                raise RuntimeError("x")
        self.b.hub = Bad()
        self.b.on_event({"ev": "shaken", "peak_g": 1.23}, "s1", "dev")
        self.assertEqual(self.logs[0][1]["sum"], "被晃了几下（最猛 1.2 g）")


class RawTest(unittest.TestCase):
    def test_raw_rows_go_to_a_file_not_the_database(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            db = FakeDb()
            b = body_sense.BodySense(db=db, clock_ms=lambda: T0, recordings_root=root)
            with mock.patch.object(body_sense.ear_log, "emit", lambda *a, **k: None):
                b.on_summary(summary(still=False, raw_t0_us=1, raw=[[0, 1, 2, 1000, 5, 6, 7, 0], [20, 1, 2, "x", 5, 6, 7, 0],
                                                                    [40, 1, 2, 990, 5, 6, 7, 1]]), "20261119/1400-x-v1", "dev")
            self.assertNotIn("raw", db.rows[0])
            self.assertNotIn("raw", b.latest)
            with open(os.path.join(root, "body", "20261119", "1400-x-v1.jsonl")) as f:
                line = json.loads(f.readline())
            self.assertEqual(line["raw"], [[0, 1, 2, 1000, 5, 6, 7, 0], [40, 1, 2, 990, 5, 6, 7, 1]])
            self.assertEqual(b.stats["raw_lines"], 1)

    def test_no_root_no_file(self):
        b = body_sense.BodySense(clock_ms=lambda: T0)
        b.on_summary(summary(raw=[[0, 1, 2, 3, 4, 5, 6, 0]]), "s", "d")
        self.assertEqual(b.stats["raw_lines"], 0)


class VisionLinkBodyTest(unittest.TestCase):
    def test_body_messages_go_to_the_hook_after_hello(self):
        got = []
        link = vision_link.VisionLink(FakeDb(), 1, "p", on_body=lambda *a: got.append(a))
        self.assertEqual(link.on_text(json.dumps(summary()))["message"], "hello first")
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-abc", "device": {"boot_id": "b"}}))
        self.assertIsNone(link.on_text(json.dumps(summary())))
        self.assertIsNone(link.on_text(json.dumps({"type": "body_ev", "ev": "near", "prox": 400})))
        self.assertEqual([a[0] for a in got], ["body", "body_ev"])
        self.assertEqual(got[0][2], link.session)
        self.assertEqual(link.body, 2)

    def test_without_a_hook_body_is_accepted_quietly(self):
        link = vision_link.VisionLink(None, 1, "p")
        link.on_text(json.dumps({"type": "hello", "source": "stackchan-abc"}))
        self.assertIsNone(link.on_text(json.dumps(summary())))


if __name__ == "__main__":
    unittest.main()
