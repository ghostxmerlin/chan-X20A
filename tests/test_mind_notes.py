import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, tingwu   # noqa: E402
from mind import notes, selfvoice                               # noqa: E402
from mind.records import Records                                # noqa: E402

MIN = 60_000


def sent(at, text="话", key=None):
    return {"ref": ("speech", "S", key or f"s-{at}"), "at": at, "text": text, "who": {"kind": "unknown"}}


class SentencesTest(unittest.TestCase):
    def setUp(self):
        self.old = selfvoice.CLIPS
        selfvoice.set_clips([{"id": "PLAY-001", "texts": ["你是猴子请来的救兵吗？"]}])
        self.addCleanup(lambda: selfvoice.set_clips(self.old))
        self.db = FakeDB()
        self.db.add("sessions", session("S", T0 - 3600_000))
        self.rec = Records(self.db)

    def test_sentences_exclude_hello_and_seen(self):
        self.db.add("speech", speech("S", "s-1", T0 + 1000, text="地线要接到机壳上"),
                    speech("S", "s-2", T0 + 2000, text="你是猴子请来的救兵吗", self_sounding=True),
                    speech("S", "s-3", T0 + 3000, text="已经处理过的"))
        tw = tingwu("S", 7, T0 + 4000, "第二版板子改走线")
        self.db.add("tingwu", tw)
        got = notes.new_sentences(self.db, self.rec, T0, T0 + MIN, {("speech", "S", "s-3")})
        self.assertEqual([g["text"] for g in got], ["地线要接到机壳上", "第二版板子改走线"])
        self.assertEqual(got[1]["ref"], ("tingwu", "S", 7))
        self.assertEqual(got[0]["at"], T0 + 1000)


class SegmentTest(unittest.TestCase):
    def conv(self, last):
        return {"cid": "c1", "start_ms": last - MIN, "last_ms": last, "end_ms": None}

    def test_late_sentence_joins_the_open_conversation(self):
        r = notes.segment(self.conv(T0 + 50_000), [sent(T0 + 100_000)], T0 + 2 * MIN)
        self.assertEqual((r["close"], len(r["append"]), r["new"]), (False, 1, []))

    def test_90_s_gap_starts_a_new_conversation(self):
        r = notes.segment(self.conv(T0), [sent(T0 + 90_000)], T0 + 2 * MIN)
        self.assertEqual((r["close"], r["append"], [len(g) for g in r["new"]]), (True, [], [1]))

    def test_gaps_inside_a_batch_make_separate_conversations(self):
        r = notes.segment(None, [sent(T0), sent(T0 + 30_000), sent(T0 + 200_000)], T0 + 5 * MIN)
        self.assertEqual((r["close"], [len(g) for g in r["new"]]), (False, [2, 1]))

    def test_conversation_closes_60_s_after_its_last_sentence(self):
        self.assertFalse(notes.segment(self.conv(T0), [], T0 + 59_000)["close"])
        self.assertTrue(notes.segment(self.conv(T0), [], T0 + 61_000)["close"])

    def test_only_hello_clips_open_nothing(self):
        r = notes.segment(None, [], T0 + MIN)
        self.assertEqual((r["close"], r["append"], r["new"]), (False, [], []))


if __name__ == "__main__":
    unittest.main()
