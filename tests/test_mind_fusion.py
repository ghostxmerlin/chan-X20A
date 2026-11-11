import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0   # noqa: E402
from mind import fusion, llm, probe, settings   # noqa: E402
from mind.store import Store   # noqa: E402

MIN = 60_000
T = T0 - 10 * MIN                       # 16:30, a :00/:30 minute
S = settings.load(None)
ENV = {"base_url": "http://x", "api_key": "k"}


def scene_json(summary="在桌前"):
    a = {"text": "x", "confidence": "mid"}
    return json.dumps({"who_am_i": a, "where_am_i": a, "people_doing": a, "owner_view": "present",
                       "entries": [{"key": "people.owner", "dimension": "people", "op": "add", "text": "主人在桌前",
                                    "basis": "inferred", "confidence": "mid", "evidence": ["rule:owner"]}],
                       "changes": [], "summary": summary, "want_to_know": "", "topic_summary": ""}, ensure_ascii=False)


class FakeChat:
    def __init__(self):
        self.calls, self.queue = [], []

    def fail_next(self, n):
        self.queue += [(None, "TimeoutError: timed out")] * n

    def reply_next(self, text):
        self.queue.append((text, None))

    def __call__(self, base_url, key, body, timeout=90):
        self.calls.append(copy.deepcopy(body))
        content, err = self.queue.pop(0) if self.queue else (scene_json(), None)
        if err:
            return None, 90000, err
        return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}, 1200, None


def minute_input(minute, same=False, owner="likely_present", coverage=(60, 0)):
    words = [] if same else [{"code": "s1", "ref": {"c": "speech", "session": "S", "id": f"s-{minute}"}, "at": minute, "text": "话",
                              "who": {"kind": "unknown", "name": None, "best": None, "score": None}, "loud_db": 10, "tingwu": [], "pos": None}]
    pkg = {"minute": minute, "fingerprint": "same" if same else f"fp{minute}", "codes": {"s1": {}}, "speech": words, "tingwu_only": [],
           "self_speech": [], "sounds": [], "faces": [], "persons": {"seconds": 0, "ids": 0}, "unknown_faces": 0, "events": {}, "warnings": []}
    strong = {"owner": {"state": owner, "why": "w", "codes": []}, "contradictions": [],
              "coverage": {"hearing_s": coverage[0], "vision_s": coverage[1], "m12_s": 0, "estimated": False},
              "time": {"label": "11-10 16:30", "weekday": 2, "work_hours": True}, "place": {"label": None}}
    return pkg, strong


from mind_build import build, speech, vision   # noqa: E402


class FingerprintTest(unittest.TestCase):
    def test_content_fp_is_stable_across_quiet_minutes_with_someone_in_view(self):
        face = [("vision", vision("V", "face_found", T0 - 20 * MIN, face=4)), ("vision", vision("V", "face_named", T0 - 20 * MIN + 500, face=4, name="me"))]
        a = build(face, minute=T0)
        b = build(face, minute=T0 + MIN)
        self.assertEqual(fusion.content_fp(*a), fusion.content_fp(*b))

    def test_eye_bin_in_content_fp_only_when_given(self):
        pkg, strong = minute_input(T, same=True)
        before = fusion.content_fp(pkg, strong)
        pkg["eye"] = {"code": "e1", "result": {"people_count": 1, "activity": "喝水"}, "frames": 2}
        self.assertEqual(fusion.content_fp(pkg, strong), before)          # v2 never sees e1: unchanged, same formula as before
        self.assertNotEqual(fusion.content_fp(pkg, strong, eye_bin=1), before)
        self.assertEqual(fusion.content_fp(pkg, strong, eye_bin=1), fusion.content_fp(pkg, strong, eye_bin=1))

    def test_v3_minute_without_eye_keeps_the_last_eye_bin(self):
        s3 = copy.deepcopy(S); s3["fusion"]["prompt"] = "v4"
        self.store = Store(FakeDB())
        fusion.CHAT = FakeChat()
        pkg, strong = minute_input(T, same=True)
        pkg["eye"] = {"code": "e1", "result": {"people_count": 1, "activity": "a"}, "frames": 2}
        first = fusion.run_minute(ENV, self.store, s3, 3, T, pkg, strong, {}, now_ms=T + 2 * MIN)
        pkg2, strong2 = minute_input(T + MIN, same=True)                   # next minute: no new frames, no eye record
        second = fusion.run_minute(ENV, self.store, s3, 3, T + MIN, pkg2, strong2, {}, now_ms=T + 3 * MIN)
        self.assertEqual((first["eye_bin"], second["eye_bin"], second["source"]), (1, 1, "carried"))

    def test_new_speech_changes_the_content_fp(self):
        face = [("vision", vision("V", "face_found", T0 - 20 * MIN, face=4))]
        a = build(face + [("speech", speech("S", "s-1", T0 + 1000))], minute=T0)
        b = build(face + [("speech", speech("S", "s-2", T0 + MIN + 1000))], minute=T0 + MIN)
        self.assertNotEqual(fusion.content_fp(*a), fusion.content_fp(*b))


class FusionTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.fake = FakeChat()
        fusion.CHAT = self.fake

    def run_at(self, minute, s=S, **kw):
        pkg, strong = minute_input(minute, **kw)
        return fusion.run_minute(ENV, self.store, s, 3, minute, pkg, strong, {}, now_ms=minute + 80_000 + MIN)

    def test_first_minute_calls_and_logs_a_run(self):
        rec = self.run_at(T)
        self.assertEqual((rec["source"], rec["reason"], rec["settings_version"]), ("model", "first", 3))
        self.assertEqual(self.db.count("mind_runs"), 1)
        run = self.db["mind_runs"].docs[0]
        self.assertTrue(run["request"]["response_format"]["json_schema"]["strict"])
        self.assertEqual((run["step"], run["model"], run["cost_usd"]), ("fusion", "gpt-6-luna", llm.cost_usd("gpt-6-luna", {"prompt_tokens": 1000, "completion_tokens": 200})))
        self.assertEqual(rec["scene"]["entries"][0]["key"], "people.owner")

    def test_v3_asks_for_low_reasoning_effort(self):
        self.run_at(T)
        self.assertEqual((self.fake.calls[0].get("reasoning_effort"), self.fake.calls[0]["max_tokens"]), ("low", 4000))

    def test_old_prompt_name_in_settings_runs_the_latest(self):
        s_old = copy.deepcopy(S); s_old["fusion"]["prompt"] = "v3"
        rec = self.run_at(T, s=s_old)
        self.assertEqual((rec["source"], rec["prompt"]), ("model", "v4"))

    def test_topic_summary_is_kept_in_the_scene(self):
        reply = json.loads(scene_json())
        reply["topic_summary"] = "旁边两人在讨论板子的地线要接到机壳上，以及第二版板子的改动。"
        self.fake.reply_next(json.dumps(reply, ensure_ascii=False))
        rec = self.run_at(T)
        self.assertEqual(rec["scene"]["topic_summary"], reply["topic_summary"])

    def test_recent_speech_from_the_previous_five_minutes(self):
        for k in range(1, 8):
            pkg, strong = minute_input(T - k * MIN)
            pkg["speech"][0]["text"] = f"第{k}分钟前"
            pkg["builder"] = 3
            self.store.put("live", T - k * MIN, pkg, strong)
        got = fusion.recent_speech(self.store, "live", T)
        self.assertEqual([x["text"] for x in got], [f"第{k}分钟前" for k in range(5, 0, -1)])

    def test_unchanged_minutes_are_carried_until_review(self):
        self.run_at(T, same=True)
        got = [self.run_at(T + k * MIN, same=True) for k in range(1, 7)]
        self.assertEqual([g["source"] for g in got], ["carried"] * 4 + ["model", "carried"])
        self.assertEqual(got[0]["carried_from"], T)
        self.assertEqual(got[4]["reason"], "review")
        self.assertEqual(len(self.fake.calls), 2)

    def test_changed_content_calls(self):
        self.run_at(T)
        self.assertEqual(self.run_at(T + MIN)["reason"], "changed")

    def test_owner_state_change_forces_a_call(self):
        self.run_at(T, same=True)
        self.assertEqual(self.run_at(T + MIN, same=True, owner="likely_absent")["reason"], "owner_state")

    def test_sensors_on_forces_a_call(self):
        self.run_at(T, same=True, coverage=(0, 0))
        self.assertEqual(self.run_at(T + MIN, same=True, coverage=(60, 0))["reason"], "sensors_on")

    def test_new_prompt_version_calls_instead_of_carrying_the_old_scene(self):
        self.run_at(T, same=True)
        self.db["mind_scenes"].docs[-1]["last_call_prompt"] = "v2"      # a scene an older prompt wrote
        rec = self.run_at(T + MIN, same=True)
        self.assertEqual((rec["source"], rec["reason"], rec["prompt"]), ("model", "prompt", "v4"))
        self.assertEqual(self.run_at(T + 2 * MIN, same=True)["source"], "carried")

    def test_sensors_off_minutes_never_call_the_model(self):
        self.run_at(T)
        got = [self.run_at(T + k * MIN, same=True, owner="sensors_off", coverage=(0, 0)) for k in range(1, 45)]
        self.assertEqual(len(self.fake.calls), 1)          # no owner_state call, no 30-min review while both senses are off
        self.assertEqual({(g["source"], g["reason"]) for g in got}, {("rules_only", "sensors_off")})
        self.assertIsNone(got[0]["scene"])

    def test_rerun_of_a_sensors_off_minute_still_calls(self):
        pkg, strong = minute_input(T, owner="sensors_off", coverage=(0, 0))
        rec = fusion.run_minute(ENV, self.store, S, 3, T, pkg, strong, {}, lane="rerun", force_prompt="v4", now_ms=T + 5 * MIN)
        self.assertEqual((rec["source"], len(self.fake.calls)), ("model", 1))

    def test_timeout_retries_once_then_fails_and_backs_off(self):
        self.fake.fail_next(2)
        rec = self.run_at(T)
        self.assertEqual((rec["source"], len(self.fake.calls)), ("rules_only", 2))
        self.assertEqual(self.db["mind_runs"].docs[0]["retries"], 1)
        self.assertEqual(self.run_at(T + MIN)["reason"], "backoff")
        self.assertEqual(self.store.state()["fusion_failures"], 1)

    def test_failure_with_a_previous_scene_carries_it_as_failed(self):
        self.run_at(T)
        self.fake.fail_next(2)
        rec = self.run_at(T + MIN)
        self.assertEqual((rec["source"], rec["carried_from"], rec["scene"]["summary"]), ("failed", T, "在桌前"))

    def test_invalid_json_retries_with_the_error(self):
        self.fake.reply_next("not json")
        rec = self.run_at(T)
        self.assertEqual(rec["source"], "model")
        self.assertIn("JSON", self.fake.calls[1]["messages"][-1]["content"])

    def test_budget_limits_to_every_30_minutes(self):
        s = copy.deepcopy(S); s["budget_usd_per_day"] = 0.001
        self.run_at(T, s=s)                               # spends ~$0.0002
        for _ in range(10):
            self.db["mind_runs"].docs.append({"lane": "live", "at": T + MIN, "cost_usd": 0.001, "step": "fusion"})
        self.assertEqual(self.run_at(T + MIN, s=s)["reason"], "budget")
        self.assertEqual(self.run_at(T + 30 * MIN, s=s)["source"], "model")

    def test_rerun_skips_the_gate_and_writes_its_own_lane(self):
        self.run_at(T, same=True)
        pkg, strong = minute_input(T, same=True)
        rec = fusion.run_minute(ENV, self.store, S, 3, T, pkg, strong, {}, lane="rerun", force_prompt="v4", force_model="gpt-6.1-terra", now_ms=T + 5 * MIN)
        self.assertEqual((rec["lane"], rec["source"], rec["model"]), ("rerun", "model", "gpt-6.1-terra"))
        self.assertEqual([d["lane"] for d in self.db["mind_scenes"].docs], ["live", "rerun"])

    def test_scene_records_the_package_version_it_was_built_from(self):
        pkg, strong = minute_input(T)
        rec = fusion.run_minute(ENV, self.store, S, 3, T, pkg, strong, {}, now_ms=T + 2 * MIN, package_version=4)
        self.assertEqual((rec["package_version"], rec["package_fingerprint"]), (4, pkg["fingerprint"]))
        self.assertEqual(rec["version"], 1)

    def test_probe_uses_llm_module(self):
        self.assertIs(probe.cost_usd, llm.cost_usd)


if __name__ == "__main__":
    unittest.main()
