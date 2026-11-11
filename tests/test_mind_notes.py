import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, tingwu   # noqa: E402
from mind import notes, selfvoice, settings                     # noqa: E402
from mind.records import Records                                # noqa: E402
from mind.store import Store                                    # noqa: E402
from test_mind_fusion import FakeChat                           # noqa: E402
import copy                                                     # noqa: E402
import json                                                     # noqa: E402

S = settings.load(None)
ENV = {"base_url": "http://x", "api_key": "k"}


def notes_json(title="板子地线", text="两人在讨论板子的地线要接到机壳上。", continues="", confidence="mid"):
    return json.dumps({"continues": continues, "title": title, "notes": text, "confidence": confidence}, ensure_ascii=False)

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


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.fake = FakeChat()
        notes.CHAT = self.fake

    def user_text(self, i=0):
        return self.fake.calls[i]["messages"][1]["content"]

    def old_conv(self, cid, start, end, title, text):
        self.db.add("conversations", {"cid": cid, "start_ms": start, "last_ms": end, "end_ms": end, "title": title,
                                      "notes": text, "confidence": "mid", "version": 1, "refs": [], "continues": ""})

    def test_first_update_offers_candidates_and_links_a_continuation(self):
        self.old_conv("cA", T0 - 20 * MIN, T0 - 5 * MIN, "板子地线", "两人讨论地线接机壳。")
        self.old_conv("cB", T0 - 60 * MIN, T0 - 50 * MIN, "午饭", "大家商量吃什么。")
        self.fake.reply_next(notes_json(continues="cA", text="又谈到上午的地线问题：要接到机壳上。"))
        conv = notes.update(ENV, self.store, S, None, [sent(T0, "地线还是要接机壳")], T0 + MIN + 10_000)
        u = self.user_text()
        self.assertIn("cA", u)
        self.assertIn("板子地线", u)
        self.assertIn("地线还是要接机壳", u)
        self.assertEqual((conv["continues"], conv["notes"], conv["version"]), ("cA", "又谈到上午的地线问题：要接到机壳上。", 1))
        self.assertEqual(conv["refs"], [["speech", "S", f"s-{T0}"]])
        self.assertEqual(self.db.count("conversation_versions"), 1)
        run = self.db["mind_runs"].docs[-1]
        self.assertEqual((run["step"], run["model"]), ("notes", "gpt-6-luna"))
        body = self.fake.calls[0]
        self.assertEqual((body.get("reasoning_effort"), body["response_format"]["json_schema"]["strict"]), ("low", True))

    def test_unknown_continues_id_is_ignored(self):
        self.fake.reply_next(notes_json(continues="cZ"))
        conv = notes.update(ENV, self.store, S, None, [sent(T0)], T0 + MIN + 10_000)
        self.assertEqual(conv["continues"], "")

    def test_running_update_sends_current_notes_new_and_recent_sentences(self):
        self.fake.reply_next(notes_json())
        conv = notes.update(ENV, self.store, S, None, [sent(T0, "地线要接机壳")], T0 + MIN + 10_000)
        self.fake.reply_next(notes_json(text="两人讨论地线接机壳，又说第二版要改走线。"))
        conv = notes.update(ENV, self.store, S, conv, [sent(T0 + 70_000, "第二版要改走线")], T0 + 2 * MIN + 10_000,
                            recent=[sent(T0, "地线要接机壳")])
        u = self.user_text(1)
        for piece in ("两人在讨论板子的地线要接到机壳上。", "第二版要改走线", "地线要接机壳"):
            self.assertIn(piece, u)
        self.assertEqual((conv["version"], conv["last_ms"], len(conv["refs"])), (2, T0 + 70_000, 2))

    def test_empty_notes_allowed(self):
        self.fake.reply_next(notes_json(title="", text="", confidence="low"))
        conv = notes.update(ENV, self.store, S, None, [sent(T0, "嗯")], T0 + MIN + 10_000)
        self.assertEqual((conv["title"], conv["notes"]), ("", ""))

    def test_failure_backs_off_and_keeps_going(self):
        self.fake.reply_next("not json")
        self.fake.reply_next("not json")
        now = T0 + MIN + 10_000
        conv = notes.update(ENV, self.store, S, None, [sent(T0)], now)
        self.assertEqual((len(self.fake.calls), conv["notes"], len(conv["refs"])), (2, "", 1))
        self.assertEqual(self.db["notes_state"].find_one({"_id": "live"})["backoff_until"], now + MIN)
        conv = notes.update(ENV, self.store, S, conv, [sent(T0 + 30_000)], now + 20_000)
        self.assertEqual((len(self.fake.calls), len(conv["refs"])), (2, 2))          # backing off: no call

    def test_budget_reached_no_call(self):
        s = copy.deepcopy(S); s["budget_usd_per_day"] = 0.0
        conv = notes.update(ENV, self.store, s, None, [sent(T0)], T0 + MIN + 10_000)
        self.assertEqual((self.fake.calls, len(conv["refs"])), ([], 1))

    def test_environment_and_owner_reach_the_prompt(self):
        s = copy.deepcopy(S); s["environment"] = "公司做指纹识别芯片。"
        self.fake.reply_next(notes_json())
        notes.update(ENV, self.store, s, None, [sent(T0)], T0 + MIN + 10_000)
        self.assertIn("环境：公司做指纹识别芯片。", self.user_text())
        system = self.fake.calls[0]["messages"][0]["content"]
        for phrase in ("约 50 字", "不要硬凑", "continues"):
            self.assertIn(phrase, system)


if __name__ == "__main__":
    unittest.main()
