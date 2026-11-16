"""mind's speech joins the run log's chain (2026-11-16): the soul-tts client sends the current hello_log trace in
POST /v1/say's "trace" (one-shot and streaming), so soul-tts's call.tts lands on the same trace as the model call
that wrote the line (docs/hello-log.md, tts side)."""
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0  # noqa: E402
from mind import runlog, settings, speak  # noqa: E402
from mind.store import Store  # noqa: E402
from test_mind_say_round import ENV, NoShuffle, RoutedChat, ctx  # noqa: E402
from mind import say_round as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
hello_log = runlog.hello_log


class TracingTts:
    """Records the run-log trace that is current when mind hands a line over."""
    def __init__(self):
        self.said = []

    def health(self):
        return {"mouth": {"device": {"device_id": "x"}}}

    def say(self, text, **kw):
        self.said.append((text, kw, runlog.current_trace()))
        return f"u{len(self.said)}"

    def status(self, uid, wait=0):
        return {"state": "done", "attempts": []}


class ClientTest(unittest.TestCase):
    def client(self):
        t = speak.TtsHttp("http://127.0.0.1:1", "tok")
        t.sent = []
        t._call = lambda method, path, body=None, timeout=None: t.sent.append((method, path, body)) or {"id": "u1"}
        return t

    def test_say_and_stream_carry_the_current_trace(self):
        t = self.client()
        with hello_log.scope(trace="abc123def456"):
            t.say("你好", voice="v")
            t.begin(voice="v", interrupt=True)
        self.assertEqual([b["trace"] for _, _, b in t.sent], ["abc123def456", "abc123def456"])
        self.assertEqual(t.sent[1][2]["stream"], True)

    def test_no_scope_no_trace_and_explicit_trace_wins(self):
        t = self.client()
        t.say("你好")
        self.assertNotIn("trace", t.sent[0][2])
        with hello_log.scope(trace="outer"):
            t.say("你好", trace="mine")
        self.assertEqual(t.sent[1][2]["trace"], "mine")

    def test_current_trace_never_raises(self):
        self.assertIsNone(runlog.current_trace())
        with hello_log.scope(trace="t1"):
            self.assertEqual(runlog.current_trace(), "t1")


class SpeechPathTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.roles = speak.load_roles(ROOT / "config" / "roles")
        self.seen = []
        saved = speak.CHAT
        self.addCleanup(setattr, speak, "CHAT", saved)

    def chat(self, reply):
        def fn(base_url, key, body, timeout=90):
            self.seen.append(runlog.current_trace())
            return {"choices": [{"message": {"content": reply}}], "usage": {}}, 10, None
        return fn

    def test_say_v1_speaks_on_the_writing_call_s_trace(self):
        s = settings.load(None)
        s["speech"]["pipeline"] = "v1"
        speak.CHAT = self.chat(json.dumps({"text": "这板子焊得，比我的发型还有个性。"}, ensure_ascii=False))
        tts = TracingTts()
        speak.say(ENV, self.store, s, self.roles, "roast", tts, int(time.time() * 1000), wait=True)
        self.assertTrue(self.seen[0])
        self.assertEqual(tts.said[0][2], self.seen[0])

    def test_round_line_speaks_on_the_round_s_trace(self):
        s = settings.load(None)
        with hello_log.scope(trace="round0trace1"):
            r = R.write_round(ENV, self.store, s, self.roles, ctx(), T0, chat=RoutedChat(), rng=NoShuffle())
        self.assertEqual(r["trace"], "round0trace1")
        self.assertEqual(self.db["say_rounds"].find_one({"rid": r["rid"]})["trace"], "round0trace1")
        tts = TracingTts()
        speak._speak_line(self.store, tts, r, R.ranked_keys(r)[0], T0, "page")          # later, outside any scope
        self.assertEqual(tts.said[0][2], "round0trace1")
        old = {k: v for k, v in r.items() if k != "trace"}                              # rounds written before the log
        speak._speak_line(self.store, tts, old, R.ranked_keys(r)[0], T0, "page")
        self.assertIsNone(tts.said[1][2])


if __name__ == "__main__":
    unittest.main()
