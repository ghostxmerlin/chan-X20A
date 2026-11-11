import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_build import MIN, S, T0, build, sound, speech, tingwu, vision   # noqa: E402
from mind import package                                                  # noqa: E402
from mind.prompts import MAX_TOKENS, OFFERED, REASONING_EFFORT, RULE_CODES, SCENE_SCHEMA, VERSIONS, render   # noqa: E402
from mind import settings                                                 # noqa: E402

DOCS = [("speech", speech("S", "s-1", T0 + 1000, text="今天下午开会", band="known", name="me", pos=[16000, 32000])),
        ("sounds", sound("S", "a-1", T0 + 2000)),
        ("vision", vision("V", "face_found", T0 + 500, face=4)), ("vision", vision("V", "face_named", T0 + 900, face=4, name="me"))]


def deep(base, **over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = {**out[k], **v} if isinstance(v, dict) else v
    return out


class PromptsTest(unittest.TestCase):
    def setUp(self):
        self.pkg, self.strong = build(DOCS)

    def test_schema_is_strict_and_flat(self):
        self.assertFalse(SCENE_SCHEMA["additionalProperties"])
        item = SCENE_SCHEMA["properties"]["entries"]["items"]
        self.assertEqual(item["properties"]["op"]["enum"], ["keep", "update", "add", "end"])
        self.assertEqual(item["properties"]["dimension"]["enum"],
                         ["people", "activity", "sound_scene", "vision", "self", "time_place", "topic", "contradiction"])
        self.assertEqual(set(SCENE_SCHEMA["required"]), set(SCENE_SCHEMA["properties"]))
        self.assertEqual(list(VERSIONS), ["v3"])

    def test_v3_asks_for_positive_likely_statements(self):
        sys_text = render("v3", S, self.pkg, self.strong, None, [], {})[0]["content"]
        for phrase in ("用正向的话说出最可能的情况", "大概在桌前工作", "附近大概没人", "不要写否定式", "总共不超过 6 条",
                       "规则算出的主人状态是底稿", "有语言以外的证据时必须引用",
                       "who_am_i 写 hello 自己", "有人说话时写 topic"):
            self.assertIn(phrase, sys_text)

    def test_user_block_has_codes_rules_and_settings_card(self):
        u = render("v3", S, self.pkg, self.strong, None, [], {})[1]["content"]
        for code in ("rule:owner", "rule:coverage", "rule:time", "rule:place", "rule:self", "s1", "a1", "v1"):
            self.assertIn(code, u)
        self.assertTrue(RULE_CODES >= {"rule:owner", "rule:coverage"})
        self.assertIn(S["place"]["default"], u)
        self.assertIn(S["self_facts"], u)
        self.assertIn("今天下午开会", u)
        self.assertNotIn("voice_names", u)

    def test_owner_alias_used_in_the_prompt(self):
        s2 = copy.deepcopy(S); s2["owner"]["aliases"] = ["Dave"]
        u = render("v3", s2, self.pkg, self.strong, None, [], {})[1]["content"]
        self.assertIn("主人：Dave", u)
        self.assertIn("主人（Dave）", u)

    def test_reported_place_in_the_prompt(self):
        st = copy.deepcopy(self.strong); st["place"] = {"label": "办公室", "source": "owner_report", "at": T0 - 3600_000}
        u = render("v3", S, self.pkg, st, None, [], {})[1]["content"]
        self.assertIn("rule:place 地点：办公室（主人报告", u)

    def test_speech_text_off_hides_words(self):
        s2 = deep(S, sources={"speech_text": False})
        u = render("v3", s2, self.pkg, self.strong, None, [], {})[1]["content"]
        self.assertNotIn("今天下午开会", u)
        self.assertIn("s1", u)

    def test_previous_entries_marked_as_possibly_wrong_with_age(self):
        prev = {"minute": T0 - MIN, "scene": {"entries": [{"key": "activity.desk", "dimension": "activity", "text": "在桌前打字",
                                                            "confidence": "mid", "basis": "inferred", "last_evidence": T0 - 4 * MIN}]}}
        u = render("v3", S, self.pkg, self.strong, prev, [], {})[1]["content"]
        self.assertIn("上一分钟的判断，可能有错", u)
        self.assertIn("activity.desk", u)
        self.assertIn("4 分钟前", u)

    def test_segments_listed(self):
        u = render("v3", S, self.pkg, self.strong, None, [{"start": T0 - 60 * MIN, "end": T0 - 10 * MIN, "cls": "present", "title": "午后在桌前"}], {})[1]["content"]
        self.assertIn("午后在桌前", u)

    def test_tingwu_only_duplicate_of_previous_minute_is_not_rendered(self):
        # session S starts at T0 − 1 h; pos 57.65 M samples ≈ T0 + 3 s (tingwu is filed by pos, speech by t)
        prev_pkg, _ = build([("speech", speech("S", "s-0", T0 - 30_000, text="上一句", pos=[57_600_000, 57_700_000]))], minute=T0 - MIN)
        pkg, st = build(DOCS + [("tingwu", tingwu("S", 7, T0 + 9000, "重复的听悟句", pos=[57_650_000, 57_720_000]))])
        self.assertTrue(any(x["text"] == "重复的听悟句" for x in pkg["tingwu_only"]))
        self.assertNotIn("重复的听悟句", render("v3", S, pkg, st, None, [], {"prev": prev_pkg})[1]["content"])
        self.assertIn("重复的听悟句", render("v3", S, pkg, st, None, [], {})[1]["content"])

    def with_eye(self):
        import copy
        pkg = copy.deepcopy(self.pkg)
        pkg["eye"] = {"code": "e1", "ref": {"c": "eye"}, "frames": 3,
                      "result": {"people_count": 1, "people": [{"who": "很可能是主人（Dave）", "where": "桌前", "doing": "喝水"}],
                                 "activity": "主人在桌前喝水", "scene": "办公室白墙", "changes": "拿起杯子", "confidence": "mid"}}
        pkg["codes"]["e1"] = {"c": "eye"}
        return pkg

    def test_v3_uses_e1_and_drops_face_lines(self):
        u = render("v3", S, self.with_eye(), self.strong, None, [], {})[1]["content"]
        self.assertIn("e1 视觉识别（3 张画面）：1 人", u)
        self.assertIn("很可能是主人（Dave）·桌前·喝水", u)
        self.assertIn("活动：主人在桌前喝水", u)
        self.assertNotRegex(u, r"\nv\d+ 脸：")
        self.assertIn("M12：", u)

    def test_without_eye_the_faces_are_listed(self):
        self.assertIsNone(self.pkg.get("eye"))
        u = render("v3", S, self.pkg, self.strong, None, [], {})[1]["content"]
        self.assertRegex(u, r"\nv1 脸：")
        self.assertNotIn("e1 ", u)

    def test_only_v3_is_offered_and_default(self):
        self.assertEqual(OFFERED, ["v3"])
        self.assertEqual(settings.load(None)["fusion"]["prompt"], "v3")

    def test_v3_asks_for_short_output_4000_tokens_low_effort(self):
        sys_text = render("v3", S, self.with_eye(), self.strong, None, [], {})[0]["content"]
        self.assertIn("e1", sys_text)
        self.assertIn("不超过 6 条", sys_text)
        # luna spends about half its output tokens reasoning (live 2026-11-11: 940 of 1739), so a 2000 cap
        # truncated v3; v3 keeps 4000 and asks for low reasoning effort instead
        self.assertEqual((MAX_TOKENS["v3"], REASONING_EFFORT["v3"]), (4000, "low"))

    def test_package_items_carry_pos(self):
        self.assertEqual(self.pkg["speech"][0]["pos"], [16000, 32000])
        self.assertEqual(package.BUILDER_VERSION, 3)          # 3: the eye result as e1


if __name__ == "__main__":
    unittest.main()
