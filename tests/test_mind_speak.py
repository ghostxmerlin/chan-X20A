import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, session, speech   # noqa: E402
from mind import selfvoice, settings, speak          # noqa: E402
from mind.store import Store                         # noqa: E402
from test_mind_fusion import FakeChat                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
S = settings.load(None)
ENV = {"base_url": "http://x", "api_key": "k"}
MIN = 60_000


class FakeTts:
    def __init__(self, connected=True):
        self.connected, self.said = connected, []

    def health(self):
        return {"mouth": {"device": {"device_id": "x"} if self.connected else None}}

    def say(self, text, **kw):
        self.said.append((text, kw))
        return f"u{len(self.said)}"

    def stop(self):
        return []

    def status(self, uid, wait=0):
        return {"state": "done", "attempts": [{"usage": {"input_tokens": 40, "output_tokens": 300}}], "timings_s": {"first_accepted": 1.1}}


class SpeakTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.fake = FakeChat()
        speak.CHAT = self.fake
        self.roles = speak.load_roles(ROOT / "config" / "roles")
        self.now = int(time.time() * 1000)

    def test_roles_load_and_validate(self):
        self.assertEqual(sorted(self.roles), ["comfort", "roast"])
        self.assertEqual((self.roles["roast"]["voice"], self.roles["comfort"]["voice"]), ("tang", "anhuan"))
        with tempfile.TemporaryDirectory() as d:
            Path(d, "bad.json").write_text(json.dumps({"id": "bad", "button": "x"}), encoding="utf-8")
            self.assertEqual(speak.load_roles(d), {})

    def test_context_uses_only_fresh_scene_eye_and_conversation(self):
        now = self.now
        self.db.add("mind_scenes", {"lane": "live", "minute": now - 2 * MIN, "version": 1, "source": "model",
                                    "scene": {"people_doing": {"text": "主人在调试板子"}, "summary": "调试中"}})
        self.db.add("eye_minutes", {"minute": now - 20 * MIN, "version": 1, "source": "model", "result": {"activity": "旧的画面"}})
        self.db.add("conversations", {"cid": "c1", "start_ms": now - 3 * MIN, "last_ms": now - MIN, "end_ms": None,
                                      "title": "地线", "notes": "两人在谈地线", "refs": [], "version": 1})
        ctx = speak.context(self.db, self.store, S, now)
        self.assertEqual(ctx["doing"], "主人在调试板子")
        self.assertIsNone(ctx["eye"])
        self.assertEqual(ctx["conversation"], "地线：两人在谈地线")

    def test_clean_strips_quotes_brackets_and_cuts_at_punctuation(self):
        self.assertEqual(speak.clean("“（扶额）这代码，比我的电线还乱。”"), "这代码，比我的电线还乱。")
        long = "这块板子啊，焊了又拆拆了又焊，比我上辈子谈的恋爱还反复，真不容易啊"
        out = speak.clean(long)
        self.assertEqual(out, "这块板子啊，焊了又拆拆了又焊，比我上辈子谈的恋爱还反复。")
        self.assertEqual(speak.clean("没有标点的一句话" * 6)[-1:], "。")
        self.assertLessEqual(len(speak.clean("没有标点的一句话" * 6)), 31)

    def test_say_refuses_without_a_device_and_calls_nothing(self):
        tts = FakeTts(connected=False)
        with self.assertRaises(speak.NotConnected):
            speak.say(ENV, self.store, S, self.roles, "roast", tts, self.now)
        self.assertEqual((self.fake.calls, tts.said), ([], []))

    def test_say_calls_luna_then_tts_and_logs(self):
        self.fake.reply_next(json.dumps({"text": "这板子焊得，比我的发型还有个性。"}, ensure_ascii=False))
        tts = FakeTts()
        rec = speak.say(ENV, self.store, S, self.roles, "roast", tts, self.now, wait=True)
        self.assertEqual(tts.said[0][0], "这板子焊得，比我的发型还有个性。")
        self.assertEqual((tts.said[0][1]["voice"], tts.said[0][1]["interrupt"]), ("tang", True))
        body = self.fake.calls[0]
        self.assertIn("毒舌", body["messages"][0]["content"])
        self.assertEqual(body.get("reasoning_effort"), "low")
        u = self.db["utterances"].docs[-1]
        self.assertEqual((u["role"], u["text"], u["tts_id"], u["state"]), ("roast", rec["text"], "u1", "done"))
        self.assertAlmostEqual(u["tts_cost_cny"], (40 * 1.5 + 300 * 12) / 1e6)
        self.assertEqual(self.db["mind_runs"].docs[-1]["step"], "say")

    def test_unknown_role_is_refused(self):
        with self.assertRaises(KeyError):
            speak.say(ENV, self.store, S, self.roles, "nope", FakeTts(), self.now)

    def test_spoken_text_is_recognised_as_hello(self):
        self.db.add("utterances", {"text": "这板子焊得，比我的发型还有个性。", "at": self.now - 5 * MIN})
        self.db.add("utterances", {"text": "很久以前说的一句话呀", "at": self.now - 2 * 3600_000})
        selfvoice.refresh_spoken(self.db, self.now)
        self.addCleanup(lambda: selfvoice.set_spoken([]))
        self.assertTrue(selfvoice.is_hello("这板子焊得比我的发型还有个性", False))
        self.assertFalse(selfvoice.is_hello("很久以前说的一句话呀", False))


if __name__ == "__main__":
    unittest.main()
