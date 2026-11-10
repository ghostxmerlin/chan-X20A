import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, sound, speech, vision   # noqa: E402
from mind import package, settings, strong                            # noqa: E402
from mind.records import Records                                      # noqa: E402

S = settings.load(None)
MIN = 60_000
SRC = "stackchan-dev"


def evaluate(docs, sessions, minute=T0, s=S, now=None):
    db = FakeDB()
    db.add("sessions", *sessions)
    for coll, d in docs:
        db.add(coll, d)
    rec = Records(db)
    window = rec.window(minute, minute + MIN)
    lookback = rec.window(minute - 30 * MIN, minute + MIN)
    pkg = package.build(window, minute, s, lookback)
    return strong.evaluate(pkg, lookback, minute, s, now if now is not None else minute + 10 * MIN)


HEAR = session("H", T0 - 3600_000, source=SRC)            # open hearing session covering everything
VIS = session("V", T0 - 3600_000, source=SRC, kind="vision")


def owner_face(start, end=None, face=4):
    out = [("vision", vision("V", "face_found", start, face=face)), ("vision", vision("V", "face_named", start + 500, face=face, name="me"))]
    if end is not None:
        out.append(("vision", vision("V", "face_lost", end, face=face, name="me")))
    return out


def keep_alive(until):
    """A record every minute so the open hearing session counts as alive."""
    return [("sounds", sound("H", f"k-{i}", t, kind="silence", label="")) for i, t in enumerate(range(T0 - 1800_000, until, MIN))]


class StrongTest(unittest.TestCase):
    def test_newest_open_session_counts_through_a_quiet_room(self):
        # the ear writes nothing while the room is silent: a live session must not read as "sensors off"
        sess = [session("C", T0 - 300_000, source=SRC)]
        docs = [("speech", speech("C", "s-1", T0 - 250_000))]
        st = evaluate(docs, sess, minute=T0 + 10 * MIN, now=T0 + 20 * MIN)
        self.assertEqual(st["coverage"]["hearing_s"], 60)
        self.assertTrue(st["coverage"]["estimated"])
        self.assertNotEqual(st["owner"]["state"], "sensors_off")

    def test_estimated_only_when_an_open_session_overlaps_the_minute(self):
        sess = [session("C", T0 - 3 * 86400_000, T0 - 2 * 86400_000, source=SRC), session("D", T0 - 600_000, T0 + 600_000, source=SRC)]
        self.assertFalse(evaluate([], sess)["coverage"]["estimated"])

    def test_open_session_coverage_stops_at_next_session(self):
        sess = [session("C", T0 - 300_000, source=SRC), session("D", T0 + 20_000, T0 + 100_000, source=SRC)]
        st = evaluate([("speech", speech("C", "s-1", T0 + 5_000))], sess)   # C alive until D replaces it
        self.assertEqual(st["coverage"]["hearing_s"], 60)

    def test_coverage_never_passes_now(self):
        st = evaluate(keep_alive(T0 + 2 * MIN), [HEAR], now=T0 + 30_000)
        self.assertEqual(st["coverage"]["hearing_s"], 30)

    def test_vision_coverage_from_on_off_events(self):
        docs = [("vision", vision("V", "vision_on", T0 - 5 * MIN)), ("vision", vision("V", "vision_off", T0 + 15_000)),
                ("vision", vision("V", "m12_online", T0 - 5 * MIN))]
        st = evaluate(docs, [VIS])
        self.assertEqual((st["coverage"]["vision_s"], st["coverage"]["m12_s"]), (15, 60))

    def test_open_vision_link_counts_as_vision_and_m12_on(self):
        st = evaluate([], [VIS])
        self.assertEqual((st["coverage"]["vision_s"], st["coverage"]["m12_s"]), (60, 60))
        ended = [session("V", T0 - 3600_000, T0 + 20_000, source=SRC, kind="vision")]
        self.assertEqual(evaluate([], ended)["coverage"]["m12_s"], 20)

    def test_m12_follows_vision_toggles_without_its_own_events(self):
        docs = [("vision", vision("V", "vision_on", T0 - 5 * MIN)), ("vision", vision("V", "vision_off", T0 + 15_000))]
        self.assertEqual(evaluate(docs, [VIS])["coverage"]["m12_s"], 15)

    def test_vision_assumed_on_when_only_detections_seen(self):
        st = evaluate([("vision", vision("C4", "person_found", T0 + 1000, person=1))], [HEAR])   # 0.4.73: events in the hearing session
        self.assertEqual(st["coverage"]["vision_s"], 60)
        self.assertTrue(st["coverage"]["estimated"])

    def test_owner_seen_then_just_left_then_unknown(self):
        docs = owner_face(T0 - 20_000, T0 + 30_000) + keep_alive(T0 + 6 * MIN)
        self.assertEqual(evaluate(docs, [HEAR, VIS])["owner"]["state"], "seen")
        self.assertEqual(evaluate(docs, [HEAR, VIS], minute=T0 + MIN)["owner"]["state"], "just_left")
        self.assertEqual(evaluate(docs, [HEAR, VIS], minute=T0 + 4 * MIN)["owner"]["state"], "unknown")

    def test_face_refound_with_same_id_after_vision_toggle_is_seen(self):
        docs = owner_face(T0 - 20 * MIN) + [("vision", vision("V", "vision_off", T0 - 10 * MIN)),
                                            ("vision", vision("V", "vision_on", T0 - 9 * MIN))] + \
            [("vision", vision("V", "face_found", T0 - 8 * MIN, face=4)), ("vision", vision("V", "face_named", T0 - 8 * MIN + 500, face=4, name="me"))]
        st = evaluate(docs + keep_alive(T0 + MIN), [HEAR, VIS])
        self.assertEqual(st["owner"]["state"], "seen")

    def test_owner_face_fields(self):
        st = evaluate(owner_face(T0 - 20_000, T0 + 30_000) + keep_alive(T0 + MIN), [HEAR, VIS])
        self.assertEqual((st["owner_face"]["last_ms"], st["owner_face"]["in_view"], st["owner_face"]["codes"]),
                         (T0 + 30_000, True, ["v1"]))

    def test_unsure_voice_counts_only_with_owner_face_nearby(self):
        voice = [("speech", speech("H", "s-1", T0 + 5_000, band="unsure", best="me", score=0.5))]
        near = owner_face(T0 - 8 * MIN, T0 - 7 * MIN)
        far = owner_face(T0 - 25 * MIN, T0 - 24 * MIN)
        st = evaluate(voice + near + keep_alive(T0 + MIN), [HEAR, VIS])
        self.assertEqual(st["owner"]["state"], "likely_present")
        self.assertEqual(st["owner_voice"]["likely_last_ms"], T0 + 5_000)
        st2 = evaluate(voice + far + keep_alive(T0 + MIN), [HEAR, VIS])
        self.assertEqual(st2["owner_voice"]["likely_last_ms"], None)
        self.assertEqual(st2["owner"]["state"], "likely_absent")      # face 24 min ago, voice not counted

    def test_known_owner_voice_means_likely_present(self):
        st = evaluate([("speech", speech("H", "s-1", T0 - 3 * MIN, band="known", name="me"))] + keep_alive(T0 + MIN), [HEAR])
        self.assertEqual((st["owner"]["state"], st["owner_voice"]["known_last_ms"]), ("likely_present", T0 - 3 * MIN))

    def test_typing_with_recent_owner_face_means_likely_present(self):
        docs = owner_face(T0 - 8 * MIN, T0 - 7 * MIN) + keep_alive(T0 + MIN) + [("sounds", sound("H", "a-1", T0 + 2000))]
        self.assertEqual(evaluate(docs, [HEAR, VIS])["owner"]["state"], "likely_present")

    def test_likely_absent_needs_coverage_and_20_minutes_without_owner(self):
        docs = owner_face(T0 - 26 * MIN, T0 - 25 * MIN)
        self.assertEqual(evaluate(docs + keep_alive(T0 + MIN), [HEAR, VIS])["owner"]["state"], "likely_absent")
        short = [session("C", T0 - 600_000, T0 + 10_000, source=SRC),
                 session("V", T0 - 3600_000, T0 - 20 * MIN, source=SRC, kind="vision")]
        self.assertEqual(evaluate(docs, short)["owner"]["state"], "unknown")   # 10 s of hearing, vision link closed

    def test_sensors_off(self):
        st = evaluate([], [])
        self.assertEqual(st["owner"]["state"], "sensors_off")
        self.assertEqual((st["coverage"]["hearing_s"], st["coverage"]["vision_s"]), (0, 0))

    def test_contradiction_owner_face_with_stranger_voice(self):
        docs = owner_face(T0 - 20_000) + keep_alive(T0 + MIN) + [("speech", speech("H", "s-1", T0 + 3000, band="unknown", best="x", score=0.1))]
        st = evaluate(docs, [HEAR, VIS])
        self.assertEqual(st["contradictions"], [{"kind": "face_owner_voice_stranger", "codes": ["v1", "s1"]}])

    def test_people_mapping_uses_display_names(self):
        s = copy.deepcopy(S)
        s["people"] = [{"display": "同事A", "voice_names": ["a1"], "face_names": []}]
        docs = keep_alive(T0 + MIN) + [("speech", speech("H", "s-1", T0 + 3000, band="known", name="a1")),
                                       ("speech", speech("H", "s-2", T0 + 4000, band="known", name="b1"))]
        st = evaluate(docs, [HEAR], s=s)
        self.assertEqual(st["others"]["known"], ["同事A", "b1"])

    def test_time_and_place(self):
        st = evaluate([], [])
        self.assertEqual(st["time"], {"label": "11-10 16:40", "weekday": 2, "work_hours": True})
        self.assertEqual(st["place"], {"label": None, "source": "no_wifi_data"})


if __name__ == "__main__":
    unittest.main()
