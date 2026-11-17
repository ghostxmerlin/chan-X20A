"""The ear's side of the pause (server/ear/presence.py, 2026-11-17; mind/pause.py owns the pause): what counts as
someone being there, when the presence report is written, and how a paid path sees its switch while paused."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import presence as pr          # noqa: E402
from tingwu_switch import SwitchReader   # noqa: E402

MIN = 60_000


class Clock:
    def __init__(self, t=1_000_000):
        self.t = t

    def __call__(self):
        return self.t


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.p = pr.Presence(clock_ms=self.clock)

    def test_any_m12_face_is_presence_and_stays_while_in_view(self):
        self.assertIsNone(self.p.last_ms())
        self.p.vision(1, {"event": "face_found", "face": 4})
        self.clock.t += 30 * MIN                       # sat still: no events
        self.assertEqual((self.p.faces(), self.p.last_ms()), (1, self.clock.t))
        self.p.vision(1, {"event": "face_lost", "face": 4})
        self.assertEqual((self.p.faces(), self.p.face_at), (0, self.clock.t))
        self.clock.t += MIN
        self.assertEqual(self.p.last_ms(), self.clock.t - MIN)

    def test_a_closed_vision_link_or_m12_off_clears_its_faces(self):
        self.p.vision(1, {"event": "face_found", "face": 4})
        self.p.vision(2, {"event": "face_named", "face": 7, "name": "x"})
        self.p.vision_closed(1)
        self.assertEqual(self.p.faces(), 1)
        self.p.vision(2, {"event": "m12_offline"})
        self.assertEqual(self.p.faces(), 0)

    def test_a_face_in_view_too_long_without_events_no_longer_counts(self):
        self.p.vision(1, {"event": "face_found", "face": 4})
        self.clock.t += pr.IN_VIEW_MAX_MS
        self.assertEqual(self.p.faces(), 0)

    def test_only_a_known_voice_counts_never_hellos_own(self):
        self.p.heard({"speaker": {"band": "unknown"}, "text": "电视里的话"})
        self.p.heard({"speaker": {"band": "unsure"}})
        self.assertIsNone(self.p.voice_at)
        self.p.heard({"speaker": {"band": "known", "name": "me01"}, "self": {"sounding": True}})
        self.assertIsNone(self.p.voice_at)
        self.p.heard({"speaker": {"band": "known", "name": "me01"}, "self": {"sounding": False}})
        self.assertEqual(self.p.voice_at, self.clock.t)

    def test_the_report_has_no_names(self):
        self.p.vision(1, {"event": "face_named", "face": 4, "name": "owner"})
        self.p.heard({"speaker": {"band": "known", "name": "me01"}})
        self.p.owner("touch:tap")
        doc = self.p.doc()
        self.assertEqual(set(doc), {"face_at", "voice_at", "owner_at", "owner_what", "faces", "at"})
        self.assertNotIn("owner", str(doc.get("face_at")))

    def test_the_writer_writes_on_a_change_and_once_a_minute(self):
        out = []
        w = pr.Writer(self.p, out.append, clock_ms=self.clock)
        self.assertTrue(w.maybe())
        self.assertFalse(w.maybe())
        self.p.owner("asr:forward")
        self.assertTrue(w.maybe())
        self.clock.t += pr.WRITE_EVERY_MS
        self.assertTrue(w.maybe())
        self.assertEqual(len(out), 3)


class PauseGateTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(10 * MIN)
        self.p = pr.Presence(clock_ms=self.clock)
        self.doc = None
        self.pause = pr.Pause(lambda: self.doc, self.p, clock_ms=self.clock)
        self.switch = SwitchReader(lambda: {"on": True})
        self.switch.update()
        self.gated = pr.Gated(self.switch, self.pause)

    def set(self, doc):
        self.doc = doc
        self.pause.update()

    def test_no_document_is_not_paused(self):
        self.set(None)
        self.assertEqual((self.gated.on, self.gated.paused), (True, False))

    def test_an_away_pause_turns_the_paid_path_off_not_the_switch(self):
        self.set({"on": True, "reasons": ["away"], "since": self.clock.t, "away_since": self.clock.t})
        self.assertEqual((self.gated.on, self.gated.paused, self.switch.on), (False, True, True))

    def test_presence_after_the_pause_lifts_it_here_at_once(self):
        self.p.vision(1, {"event": "face_lost", "face": 1})           # before the pause: no lift
        self.set({"on": True, "reasons": ["away"], "since": self.clock.t + 1, "away_since": self.clock.t + 1})
        self.assertFalse(self.gated.on)
        self.clock.t += 5000
        self.p.owner("touch:tap")
        self.assertTrue(self.gated.on)

    def test_a_budget_pause_is_only_minds_to_lift_and_ends_at_midnight(self):
        self.set({"on": True, "reasons": ["budget", "away"], "since": 1, "away_since": 1,
                  "budget_until": self.clock.t + MIN})
        self.p.vision(1, {"event": "face_found", "face": 1})
        self.assertEqual((self.gated.on, self.pause.reasons()), (False, ["budget"]))
        self.clock.t += MIN
        self.assertEqual(self.pause.reasons(), [])
        self.assertTrue(self.gated.on)

    def test_a_failed_read_keeps_the_last_value(self):
        self.set({"on": True, "reasons": ["budget"], "budget_until": self.clock.t + MIN})

        def fail():
            raise RuntimeError("db down")
        self.pause.read = fail
        self.pause.update()
        self.assertFalse(self.gated.on)

    def test_an_owners_switch_off_stays_off(self):
        sw = SwitchReader(lambda: {"on": False})
        sw.update()
        g = pr.Gated(sw, self.pause)
        self.set(None)
        self.assertEqual((g.on, g.paused), (False, False))


if __name__ == "__main__":
    unittest.main()
