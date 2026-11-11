import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, sound, tingwu, vision   # noqa: E402
from mind import package, settings                                            # noqa: E402
from mind.records import Records                                              # noqa: E402

S = settings.load(None)
SR = 16000
SESS_START = T0 - 600_000                      # session "S" started 10 min before the minute


def at_pos(offset_ms, dur_ms=1000):
    """(wall at, pos) for a record offset_ms into the minute, in session S."""
    start = (T0 + offset_ms - SESS_START) * SR // 1000
    return T0 + offset_ms, [start, start + dur_ms * SR // 1000]


def build_minute(docs, sessions=None, minute=T0):
    db = FakeDB()
    db.add("sessions", *(sessions or [session("S", SESS_START), session("V", SESS_START, kind="vision")]))
    for coll, d in docs:
        db.add(coll, d)
    rec = Records(db)
    return package.build(rec.window(minute, minute + 60_000), minute, S, rec.window(minute - 1_800_000, minute + 60_000))


def sp(offset, id, **kw):
    at, pos = at_pos(offset)
    return ("speech", speech("S", id, at, pos=pos, **kw))


class PackageTest(unittest.TestCase):
    def test_speech_bands_and_self_split(self):
        from mind import selfvoice
        old = selfvoice.CLIPS
        selfvoice.set_clips([{"id": "PLAY-001", "texts": ["你是猴子请来的救兵吗？"]}])
        self.addCleanup(lambda: selfvoice.set_clips(old))
        p = build_minute([sp(1000, "s-1", text="a", band="known", name="me"),
                          sp(5000, "s-2", text="b", band="unsure", best="me", score=0.55),
                          sp(9000, "s-3", text="你是猴子请来的救兵吗", self_sounding=True),
                          sp(13000, "s-4", text="是还有2403和2402", self_sounding=True)])
        self.assertEqual([x["who"]["kind"] for x in p["speech"]], ["known", "unsure", "unknown"])
        self.assertEqual(p["speech"][0]["who"]["name"], "me")
        self.assertEqual(p["speech"][1]["who"]["best"], "me")
        self.assertEqual(p["self_speech"][0]["who"]["kind"], "self")
        # SenseVoice hears silence while hello sounds: other words overlapping her sound are people's
        self.assertEqual((p["speech"][2]["text"], p["speech"][2]["hello_sounding"]), ("是还有2403和2402", True))
        self.assertFalse(p["speech"][0].get("hello_sounding"))
        self.assertEqual(sorted(x["code"] for x in p["speech"] + p["self_speech"]), ["s1", "s2", "s3", "s4"])

    def test_hello_clip_heard_by_tingwu_is_dropped(self):
        from mind import selfvoice
        old = selfvoice.CLIPS
        selfvoice.set_clips([{"id": "PLAY-001", "texts": ["你是猴子请来的救兵吗？"]}])
        self.addCleanup(lambda: selfvoice.set_clips(old))
        tw = tingwu("S", 1, T0 + 2000, "你是猴子请来的救兵吗")
        tw["self"] = {"sounding": True, "moving": False}
        p = build_minute([("tingwu", tw), ("tingwu", tingwu("S", 2, T0 + 9000, "地线要接到机壳上"))])
        self.assertEqual([t["text"] for t in p["tingwu_only"]], ["地线要接到机壳上"])
        self.assertEqual(p["builder"], 5)

    def test_tingwu_attaches_by_pos_overlap_else_alone(self):
        at1, pos1 = at_pos(2000, 3000)
        at2, pos2 = at_pos(40000, 2000)
        p = build_minute([sp(2500, "s-1", text="一"),
                          ("tingwu", tingwu("S", 1, at1, "听悟一", pos=pos1)),
                          ("tingwu", tingwu("S", 2, at2, "听悟二", pos=pos2))])
        self.assertEqual(p["speech"][0]["tingwu"][0]["text"], "听悟一")
        self.assertEqual([x["text"] for x in p["tingwu_only"]], ["听悟二"])
        self.assertEqual([p["speech"][0]["tingwu"][0]["code"], p["tingwu_only"][0]["code"]], ["t1", "t2"])

    def test_sounds_are_counted_per_kind(self):
        docs = []
        for i, (off, loud) in enumerate(((1000, 9.0), (2000, 14.0), (3000, 11.0))):
            at, pos = at_pos(off)
            docs.append(("sounds", sound("S", f"a-{i}", at, loud=loud, pos=pos)))
        p = build_minute(docs)
        self.assertEqual(p["sounds"], [{"kind": "typing", "label": "键盘打字", "count": 3, "max_loud_db": 14.0,
                                        "codes": ["a1", "a2", "a3"]}])

    def test_face_interval_named_and_lost(self):
        p = build_minute([("vision", vision("V", "face_found", T0 - 20_000, face=4, gender="m", age_group="adult")),
                          ("vision", vision("V", "face_named", T0 - 19_000, face=4, name="me")),
                          ("vision", vision("V", "face_lost", T0 + 30_000, face=4, name="me"))])
        f = p["faces"][0]
        self.assertEqual((f["code"], f["name"], f["from"], f["to"], f["open"], f["closed_by"]),
                         ("v1", "me", T0 - 20_000, T0 + 30_000, False, "lost"))
        self.assertEqual((f["gender"], f["age_group"]), ("m", "adult"))
        self.assertEqual(p["unknown_faces"], 0)

    def test_face_without_lost_closes_at_m12_offline(self):
        p = build_minute([("vision", vision("V", "face_found", T0 - 20_000, face=4)),
                          ("vision", vision("V", "m12_offline", T0 + 10_000))])
        self.assertEqual((p["faces"][0]["closed_by"], p["faces"][0]["to"]), ("m12_offline", T0 + 10_000))
        self.assertEqual(p["unknown_faces"], 1)
        self.assertEqual(p["events"]["m12_offline"], [T0 + 10_000])

    def test_face_closes_when_its_vision_session_is_replaced(self):
        sessions = [session("S", SESS_START), session("V", SESS_START, kind="vision"),
                    session("V2", T0 - 100_000, kind="vision")]
        p = build_minute([("vision", vision("V", "face_found", T0 - 200_000, face=4))], sessions=sessions)
        self.assertEqual(p["faces"], [])           # ended at T0 − 100 s, before this minute

    def test_face_with_open_session_stays_open(self):
        p = build_minute([("vision", vision("V", "face_found", T0 - 600_000, face=4, name="me"))])
        self.assertTrue(p["faces"][0]["open"])
        self.assertEqual(p["faces"][0]["to"], T0 + 60_000)

    def test_persons_seconds_and_ids(self):
        p = build_minute([("vision", vision("V", "person_found", T0 + 10_000, person=1)),
                          ("vision", vision("V", "person_lost", T0 + 30_000, person=1)),
                          ("vision", vision("V", "person_found", T0 + 20_000, person=2)),
                          ("vision", vision("V", "person_lost", T0 + 40_000, person=2))])
        self.assertEqual(p["persons"], {"seconds": 30, "ids": 2})

    def test_two_sessions_in_one_minute_codes_unique_and_no_duplicates(self):
        sessions = [session("S", SESS_START, T0 + 20_000), session("S2", T0 + 20_000)]
        at2 = T0 + 30_000
        dup = sp(1000, "s-1", text="一")
        p = build_minute([dup, dup, ("speech", speech("S2", "s-1", at2, text="二", pos=[160000, 176000]))],
                         sessions=sessions)
        self.assertEqual([x["code"] for x in p["speech"]], ["s1", "s2"])
        self.assertEqual([x["ref"]["session"] for x in p["speech"]], ["S", "S2"])
        self.assertEqual(len(p["codes"]), 2)

    def test_drift_warning(self):
        at, pos = at_pos(1000)
        p = build_minute([("speech", speech("S", "s-1", at + 9000, pos=pos))])
        self.assertEqual(p["warnings"], [{"kind": "drift", "code": "s1", "seconds": 9.0}])

    def eye_doc(self, version, source="model", activity="主人在桌前"):
        return ("eye_minutes", {"minute": T0, "version": version, "source": source, "frames": [{"seq": 1}, {"seq": 2}],
                                "result": {"people_count": 1, "people": [{"who": "主人", "where": "桌前", "doing": "打字"}],
                                           "activity": activity, "scene": "办公室", "changes": "", "confidence": "high"}})

    def test_eye_result_becomes_e1(self):
        pkg = build_minute([self.eye_doc(1, activity="旧"), self.eye_doc(2, activity="新")])
        self.assertEqual((pkg["eye"]["code"], pkg["eye"]["result"]["activity"], pkg["eye"]["frames"]), ("e1", "新", 2))
        self.assertEqual(pkg["codes"]["e1"], {"c": "eye", "minute": T0, "version": 2})
        self.assertEqual(pkg["builder"], 5)

    def test_failed_eye_is_not_e1(self):
        for src in ("failed", "budget", "backoff"):
            pkg = build_minute([self.eye_doc(1, source=src)])
            self.assertIsNone(pkg["eye"], src)
            self.assertNotIn("e1", pkg["codes"])

    def test_eye_changes_the_fingerprint(self):
        self.assertNotEqual(build_minute([])["fingerprint"], build_minute([self.eye_doc(1)])["fingerprint"])

    def conv_docs(self, start, end, versions):
        docs = [("conversations", {"cid": "cX", "start_ms": start, "last_ms": end or start, "end_ms": end, "title": "x",
                                   "notes": "x", "version": len(versions), "continues": ""})]
        for i, (at, title) in enumerate(versions, 1):
            docs.append(("conversation_versions", {"cid": "cX", "version": i, "title": title, "notes": (title + "的纪要") if title else "",
                                                   "confidence": "mid", "continues": "", "at": at + 8000, "until_ms": at}))
        return docs

    def test_package_carries_the_current_conversation_as_c1(self):
        pkg = build_minute(self.conv_docs(T0 - 3 * 60_000, None, [(T0 - 60_000, "板子地线")]))
        self.assertEqual((pkg["conv"]["code"], pkg["conv"]["title"], pkg["conv"]["notes"]), ("c1", "板子地线", "板子地线的纪要"))
        self.assertEqual(pkg["codes"]["c1"], {"c": "conv", "cid": "cX", "version": 1})
        self.assertEqual(pkg["builder"], 5)

    def test_a_minute_uses_the_notes_written_by_its_end(self):
        # a version covers sentences up to until_ms; the minute uses the newest one covering up to its end
        pkg = build_minute(self.conv_docs(T0 - 3 * 60_000, None, [(T0, "早"), (T0 + 60_000, "末"), (T0 + 2 * 60_000, "以后")]))
        self.assertEqual(pkg["conv"]["title"], "末")

    def test_closed_more_than_5_minutes_ago_is_not_c1(self):
        pkg = build_minute(self.conv_docs(T0 - 20 * 60_000, T0 - 5 * 60_000 + 60_000 - 1, [(T0 - 10 * 60_000, "旧")]))
        self.assertIsNone(pkg["conv"])
        recent = build_minute(self.conv_docs(T0 - 20 * 60_000, T0 - 60_000, [(T0 - 10 * 60_000, "近")]))
        self.assertEqual(recent["conv"]["title"], "近")

    def test_conversation_without_notes_is_not_c1(self):
        self.assertIsNone(build_minute(self.conv_docs(T0 - 60_000, None, [(T0, "")]))["conv"])

    def test_fingerprint_stable_and_sensitive(self):
        recs = [sp(1000, "s-1"), sp(2000, "s-2")]
        self.assertEqual(build_minute(recs)["fingerprint"], build_minute(list(reversed(recs)))["fingerprint"])
        self.assertNotEqual(build_minute(recs)["fingerprint"], build_minute(recs + [sp(3000, "s-3")])["fingerprint"])
        self.assertEqual(len(build_minute(recs)["fingerprint"]), 16)


if __name__ == "__main__":
    unittest.main()
