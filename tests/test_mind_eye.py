import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, vision   # noqa: E402
from mind import eye, eye_runner, llm, settings   # noqa: E402
from mind.store import Store                    # noqa: E402
from test_mind_fusion import FakeChat           # noqa: E402

MIN = 60_000
S = settings.load(None)
ENV = {"base_url": "http://x", "api_key": "k"}


def eye_json():
    return json.dumps({"people_count": 1, "people": [{"who": "主人", "where": "桌前", "doing": "喝水"}],
                       "activity": "主人在桌前喝水", "scene": "办公室", "changes": "拿起杯子", "confidence": "high"},
                      ensure_ascii=False)


class EyeChat(FakeChat):
    """FakeChat whose default reply is a valid eye answer."""
    def __call__(self, base_url, key, body, timeout=90):
        if not self.queue:
            self.queue.append((eye_json(), None))
        return super().__call__(base_url, key, body, timeout)


class EyeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = self.dir.name
        self.db = FakeDB()
        self.store = Store(self.db)
        self.fake = EyeChat()
        eye.CHAT = self.fake

    def frame(self, at, seq, path=None, write=True):
        rel = path or f"frames/20261111/x-v1/{at}-{seq}.jpg"
        if write and path is None:
            full = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(b"\xff\xd8" + str(seq).encode() + b"\xff\xd9")
        self.db.add("frames", {"session": "20261111/x-v1", "seq": seq, "t_us": seq, "at": at, "received": at, "path": rel,
                               "bytes": 10, "w": 320, "h": 240, "diff": 9})

    def run_at(self, minute=T0, now=None, s=S):
        return eye.run_minute(ENV, self.store, s, minute, self.root, now if now is not None else minute + MIN + 20_000)

    def images(self, call):
        return [c for c in call["messages"][1]["content"] if c.get("type") == "image_url"]

    def text(self, call):
        return "".join(c["text"] for c in call["messages"][1]["content"] if c.get("type") == "text")

    def test_no_frames_no_call_no_record(self):
        self.db.add("vision", vision("V", "face_found", T0 + 1000, face=4))
        self.assertIsNone(self.run_at())
        self.assertEqual((self.fake.calls, self.db.count("eye_minutes")), ([], 0))

    def test_frames_call_once_with_images_and_m12_lines(self):
        for i, at in enumerate((T0 + 1000, T0 + 11_000, T0 + 21_000)):
            self.frame(at, i + 1)
        self.db.add("vision", vision("V", "face_found", T0 + 500, face=4), vision("V", "face_named", T0 + 900, face=4, name="me"))
        self.fake.reply_next(eye_json())
        rec = self.run_at()
        self.assertEqual(len(self.fake.calls), 1)
        call = self.fake.calls[0]
        self.assertEqual(len(self.images(call)), 3)
        self.assertTrue(self.images(call)[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("认出 主人", self.text(call))
        self.assertNotIn("远处", self.text(call) + call["messages"][0]["content"])
        self.assertEqual(call["tool_choice"]["function"]["name"], "eye")
        self.assertEqual(call["model"], "claude-haiku-5-5")
        self.assertEqual((rec["source"], rec["result"]["activity"], len(rec["frames"])), ("model", "主人在桌前喝水", 3))
        self.assertNotIn("b64", json.dumps(rec["frames"]))
        self.assertEqual(self.db.count("eye_minutes"), 1)
        run = self.db["mind_runs"].docs[0]
        usage = {"prompt_tokens": 1000, "completion_tokens": 200}
        self.assertEqual((run["step"], run["cost_usd"]), ("eye", llm.cost_usd("claude-haiku-5-5", usage)))

    def test_more_than_six_frames_are_thinned_to_six(self):
        for i in range(9):
            self.frame(T0 + 1000 + i * 6000, i + 1)
        self.fake.reply_next(eye_json())
        rec = self.run_at()
        self.assertEqual(len(self.images(self.fake.calls[0])), 6)
        seqs = [f["seq"] for f in rec["frames"]]
        self.assertEqual((len(seqs), seqs[0], seqs[-1]), (6, 1, 9))

    def test_missing_or_outside_files_are_skipped(self):
        self.frame(T0 + 1000, 1, write=False)
        self.frame(T0 + 2000, 2, path="../x.jpg")
        self.frame(T0 + 3000, 3)
        self.fake.reply_next(eye_json())
        rec = self.run_at()
        self.assertEqual((len(self.images(self.fake.calls[0])), [f["seq"] for f in rec["frames"]]), (1, [3]))
        self.frame(T0 + MIN + 1000, 4, write=False)
        self.frame(T0 + MIN + 2000, 5, path="../y.jpg")
        self.assertIsNone(self.run_at(T0 + MIN))
        self.assertEqual(len(self.fake.calls), 1)

    def test_bad_reply_retries_once_then_fails_and_backs_off(self):
        self.frame(T0 + 1000, 1)
        self.fake.reply_next("not json")
        self.fake.reply_next("not json")
        now = T0 + MIN + 20_000
        rec = self.run_at(T0, now)
        self.assertEqual((len(self.fake.calls), rec["source"]), (2, "failed"))
        state = self.db["eye_state"].find_one({"_id": "live"})
        self.assertEqual(state["backoff_until"], now + MIN)
        self.frame(T0 + MIN + 1000, 2)
        nxt = self.run_at(T0 + MIN, now + 30_000)
        self.assertEqual((nxt["source"], len(self.fake.calls)), ("backoff", 2))

    def test_hollow_answer_is_retried(self):
        self.frame(T0 + 1000, 1)
        hollow = json.dumps({"activity": "", "changes": "", "confidence": "mid", "people": [], "people_count": 1, "scene": "x"})
        self.fake.reply_next(hollow)
        self.fake.reply_next(eye_json())
        rec = self.run_at()
        self.assertEqual((len(self.fake.calls), rec["source"]), (2, "model"))
        self.assertIn("people_count", self.fake.calls[1]["messages"][-1]["content"])

    def test_prompt_says_an_unrecognised_face_at_the_desk_is_likely_the_owner(self):
        self.frame(T0 + 1000, 1)
        self.fake.reply_next(eye_json())
        self.run_at()
        system = self.fake.calls[0]["messages"][0]["content"]
        self.assertIn("很可能是主人", system)

    def test_eye_uses_a_forced_tool_call(self):
        # live 2026-11-11: haiku via aihubmix ignores response_format json_schema (3/3 fenced, thinking on)
        # but a forced tool call returns the exact schema (3/3, ~3 s, no thinking)
        self.frame(T0 + 1000, 1)
        self.run_at()
        body = self.fake.calls[0]
        self.assertNotIn("response_format", body)
        self.assertEqual(body["tool_choice"], {"type": "function", "function": {"name": "eye"}})
        self.assertEqual(body["tools"][0]["function"]["parameters"], eye.EYE_SCHEMA)

    def test_tool_call_arguments_are_parsed(self):
        self.frame(T0 + 1000, 1)
        reply = {"choices": [{"message": {"content": None, "tool_calls": [
            {"type": "function", "function": {"name": "eye", "arguments": eye_json()}}]}}],
                 "usage": {"prompt_tokens": 900, "completion_tokens": 300}}
        eye.CHAT = lambda *a, **k: (reply, 3000, None)
        try:
            rec = self.run_at()
        finally:
            eye.CHAT = self.fake
        self.assertEqual((rec["source"], rec["result"]["activity"]), ("model", "主人在桌前喝水"))

    def test_fenced_json_is_accepted(self):
        # live 2026-11-11 15:20+: haiku via aihubmix wrapped its answer in ```json fences despite strict json_schema
        self.frame(T0 + 1000, 1)
        self.fake.reply_next("```json\n" + eye_json() + "\n```")
        rec = self.run_at()
        self.assertEqual((rec["source"], len(self.fake.calls)), ("model", 1))

    def test_prompt_spells_out_the_fields(self):
        self.frame(T0 + 1000, 1)
        self.run_at()
        system = self.fake.calls[0]["messages"][0]["content"]
        for field in ("people_count", "who", "where", "doing", "activity", "scene", "changes", "confidence"):
            self.assertIn(field, system)

    def test_budget_reached_records_budget_without_calling(self):
        import copy
        s = copy.deepcopy(S)
        s["budget_usd_per_day"] = 0.0
        self.frame(T0 + 1000, 1)
        rec = self.run_at(s=s)
        self.assertEqual((rec["source"], self.fake.calls), ("budget", []))



class EyeRunnerTest(EyeTest):
    def switch(self, on):
        self.db["switches"].update_one({"_id": "eye"}, {"$set": {"on": on}}, upsert=True)

    def tick(self, now):
        return eye_runner.tick(self.db, self.store, S, ENV, self.root, now)

    def test_tick_waits_15_s_after_the_minute(self):
        self.switch(True)
        self.frame(T0 + 1000, 1)
        self.db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": T0 - MIN}}, upsert=True)
        self.assertNotIn(T0, self.tick(T0 + MIN + 14_000))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.tick(T0 + MIN + 15_000), [T0])
        self.assertEqual(len(self.fake.calls), 1)

    def test_tick_processes_each_minute_once(self):
        self.switch(True)
        self.frame(T0 + 1000, 1)
        self.db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": T0 - MIN}}, upsert=True)
        self.tick(T0 + MIN + 20_000)
        self.assertEqual(self.tick(T0 + MIN + 25_000), [])
        self.assertEqual(len(self.fake.calls), 1)

    def test_catch_up_is_capped_at_10_minutes(self):
        self.switch(True)
        self.db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": T0 - 3 * 3600_000}}, upsert=True)
        for k in range(30):
            self.frame(T0 - k * MIN + 1000, 100 + k)
        done = self.tick(T0 + MIN + 20_000)
        self.assertEqual(done, [T0 - k * MIN for k in range(9, -1, -1)])
        self.assertEqual(len(self.fake.calls), 10)

    def test_first_start_without_a_cursor_is_capped_too(self):
        self.switch(True)
        self.assertEqual(len(self.tick(T0 + MIN + 20_000)), 10)

    def test_switch_off_moves_the_cursor_without_calls(self):
        self.switch(False)
        self.frame(T0 + 1000, 1)
        self.assertEqual(self.tick(T0 + MIN + 20_000), [])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.db["eye_state"].find_one({"_id": "live"})["cursor_minute"], T0)
        self.switch(True)
        self.assertEqual(self.tick(T0 + MIN + 25_000), [])      # the minute seen while off is not called later

    def test_switch_off_during_a_catch_up_stops_further_calls(self):
        self.switch(True)
        self.db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": T0 - 10 * MIN}}, upsert=True)
        for k in range(10):
            self.frame(T0 - k * MIN + 1000, 200 + k)
        orig = eye.run_minute

        def run_then_switch_off(*a, **k):
            out = orig(*a, **k)
            self.switch(False)                         # the user presses 关 after the first minute
            return out
        eye_runner.eye.run_minute = run_then_switch_off
        try:
            self.tick(T0 + MIN + 20_000)
        finally:
            eye_runner.eye.run_minute = orig
        self.assertEqual(len(self.fake.calls), 1)

    def test_an_error_after_the_call_is_recorded_and_the_minute_not_repeated(self):
        self.switch(True)
        self.frame(T0 + 1000, 1)
        self.db["eye_state"].update_one({"_id": "live"}, {"$set": {"cursor_minute": T0 - MIN}}, upsert=True)
        real = self.store.put_run
        self.store.put_run = lambda doc: (_ for _ in ()).throw(RuntimeError("mongo blip"))
        try:
            self.tick(T0 + MIN + 20_000)                 # must not raise
        finally:
            self.store.put_run = real
        state = self.db["eye_state"].find_one({"_id": "live"})
        self.assertEqual(state["cursor_minute"], T0)
        self.assertGreater(state["backoff_until"], T0 + MIN)
        self.tick(T0 + MIN + 50_000)
        self.assertEqual(len(self.fake.calls), 1)

    def test_list_content_is_a_failure_not_a_crash(self):
        self.frame(T0 + 1000, 1)
        bad = {"choices": [{"message": {"content": [{"type": "text", "text": "x"}]}}], "usage": {}}
        eye.CHAT = lambda *a, **k: (bad, 10, None)
        try:
            rec = self.run_at()
        finally:
            eye.CHAT = self.fake
        self.assertEqual(rec["source"], "failed")

    def test_status_document(self):
        eye_runner.write_status(self.db, True, False, T0)
        st = self.db["switches"].find_one({"_id": "eye_status"})
        self.assertEqual((st["running"], st["available"], st["at"]), (True, False, T0))

if __name__ == "__main__":
    unittest.main()
