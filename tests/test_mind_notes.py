import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech, tingwu   # noqa: E402
from mind import notes, notes_runner, selfvoice, settings       # noqa: E402
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



class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.db.add("sessions", session("S", T0 - 3600_000))
        self.rec, self.store = Records(self.db), Store(self.db)
        self.fake = FakeChat()
        notes.CHAT = self.fake
        self.db["switches"].update_one({"_id": "notes"}, {"$set": {"on": True}}, upsert=True)
        self.db["notes_state"].update_one({"_id": "live"}, {"$set": {"cursor_ms": T0}}, upsert=True)

    def say(self, at, id, text="地线要接机壳"):
        self.db.add("speech", speech("S", id, at, text=text))

    def tick(self, now):
        if not self.fake.queue:
            self.fake.reply_next(notes_json())
        return notes_runner.tick(self.db, self.store, self.rec, S, ENV, now)

    def convs(self):
        return sorted(self.db["conversations"].docs, key=lambda c: c["start_ms"])

    def test_tick_waits_5_s_after_the_minute(self):
        self.say(T0 + 5000, "s-1")
        self.assertEqual(notes_runner.tick(self.db, self.store, self.rec, S, ENV, T0 + MIN + 4000), 0)
        self.assertEqual(self.tick(T0 + MIN + 5_000), 1)
        self.assertEqual(len(self.convs()), 1)

    def test_one_call_per_tick_for_a_running_conversation_and_late_rows_join(self):
        self.say(T0 + 5000, "s-1")
        self.tick(T0 + MIN + 10_000)
        self.say(T0 + 40_000, "s-late")                   # written after the first tick
        self.say(T0 + 65_000, "s-2", "第二版要改走线")
        self.assertEqual(self.tick(T0 + 2 * MIN + 10_000), 1)
        c = self.convs()
        self.assertEqual((len(c), len(c[0]["refs"])), (1, 3))
        self.assertEqual(self.tick(T0 + 2 * MIN + 30_000), 0)    # same minute: nothing more

    def test_silence_closes_and_the_next_talk_is_a_new_conversation(self):
        self.say(T0 + 5000, "s-1")
        self.tick(T0 + MIN + 10_000)
        self.tick(T0 + 2 * MIN + 10_000)                   # 65 s of silence: closed without a call
        self.assertIsNotNone(self.convs()[0]["end_ms"])
        self.say(T0 + 3 * MIN + 1000, "s-3", "午饭吃什么")
        self.tick(T0 + 4 * MIN + 10_000)
        self.assertEqual([c["end_ms"] is None for c in self.convs()], [False, True])

    def test_switch_off_no_calls_but_conversations_still_close(self):
        self.say(T0 + 5000, "s-1")
        self.tick(T0 + MIN + 10_000)
        self.db["switches"].update_one({"_id": "notes"}, {"$set": {"on": False}})
        n = len(self.fake.calls)
        self.say(T0 + 70_000, "s-2")
        notes_runner.tick(self.db, self.store, self.rec, S, ENV, T0 + 3 * MIN + 10_000)
        self.assertEqual(len(self.fake.calls), n)
        self.assertIsNotNone(self.convs()[0]["end_ms"])
        self.assertEqual(len(self.convs()), 1)

    # ---- final review fixes ----
    def test_close_waits_a_margin_for_rows_written_after_a_long_utterance(self):
        self.say(T0 + 5000, "s-1")
        self.tick(T0 + MIN + 5_000)
        self.tick(T0 + 2 * MIN + 5_000)                    # last sentence 115 s ago at until=T0+2min: within 60 + 30? no
        self.assertIsNotNone(self.convs()[0]["end_ms"])
        self.db["conversations"].docs[:] = []
        self.db["notes_state"].update_one({"_id": "live"}, {"$set": {"cursor_ms": T0, "seen": []}}, upsert=True)
        self.say(T0 + 50_000, "s-9")
        self.tick(T0 + MIN + 5_000)
        self.tick(T0 + 2 * MIN + 5_000)                    # 70 s after the last start: a long utterance may still land
        self.assertIsNone(self.convs()[0]["end_ms"])

    def test_late_row_of_a_closed_conversation_goes_back_to_it_without_a_call(self):
        self.say(T0 + 5000, "s-1")
        self.tick(T0 + MIN + 5_000)                        # A open
        self.say(T0 + 100_000, "s-B", "午饭吃什么")         # 95 s later: B starts, A closes
        self.tick(T0 + 2 * MIN + 5_000)
        n = len(self.fake.calls)
        self.say(T0 + 50_000, "s-late-A")                  # spoken during A, written late
        self.tick(T0 + 3 * MIN + 5_000)
        a, b = self.convs()
        self.assertIn(["speech", "S", "s-late-A"], a["refs"])
        self.assertNotIn(["speech", "S", "s-late-A"], b["refs"])
        self.assertEqual(len(self.fake.calls), n + 0)

    def test_a_crash_after_an_update_does_not_send_the_sentences_again(self):
        self.say(T0 + 5000, "s-1")
        real = notes_runner._close
        notes_runner._close = lambda db, conv: (_ for _ in ()).throw(RuntimeError("mongo blip"))
        try:
            self.say(T0 + 3 * MIN, "s-2")              # a second group: the first one gets closed (crash)
            with self.assertRaises(RuntimeError):
                self.tick(T0 + 4 * MIN + 5_000)
        finally:
            notes_runner._close = real
        n = len(self.fake.calls)
        self.tick(T0 + 4 * MIN + 5_000)
        refs = [r for c in self.convs() for r in c["refs"]]
        self.assertEqual(len(refs), len(set(map(tuple, refs))))
        self.assertLessEqual(len(self.fake.calls) - n, 1)

    def test_catch_up_is_capped_and_old_conversations_are_only_recorded(self):
        self.db["notes_state"].update_one({"_id": "live"}, {"$set": {"cursor_ms": T0 - 30 * MIN, "seen": []}}, upsert=True)
        for k in range(8):                                 # 8 separate conversations over the last 30 minutes
            self.say(T0 - 30 * MIN + k * 3 * MIN + 1000, f"s-{k}")
        self.say(T0 + 30_000, "s-now")
        self.assertLessEqual(self.tick(T0 + MIN + 5_000), notes_runner.MAX_CALLS_PER_TICK)
        old = [c for c in self.convs() if c["last_ms"] < T0 - 10 * MIN]
        self.assertTrue(old and all(c["version"] == 0 for c in old))

    def test_versions_record_the_minute_they_cover(self):
        self.say(T0 + 5000, "s-1")
        self.tick(T0 + MIN + 5_000)
        v = self.db["conversation_versions"].docs[-1]
        self.assertEqual(v["until_ms"], T0 + MIN)
        self.assertEqual(v['covered_last_ms'],T0+5000)

    def test_status_document(self):
        notes_runner.write_status(self.db, True, True, T0)
        self.assertEqual(self.db["switches"].find_one({"_id": "notes_status"})["running"], True)

if __name__ == "__main__":
    unittest.main()
