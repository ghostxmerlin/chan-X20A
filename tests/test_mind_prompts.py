import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_build import MIN, S, T0, build, sound, speech, tingwu, vision   # noqa: E402
from mind import package                                                  # noqa: E402
from mind.prompts import RULE_CODES, SCENE_SCHEMA, VERSIONS, render       # noqa: E402

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
        self.assertIn("v1", VERSIONS)

    def test_system_prompt_has_the_discipline(self):
        sys_text = render("v1", S, self.pkg, self.strong, None, [], {})[0]["content"]
        for phrase in ("在证据范围内说出最可能的情况", "几句话不等于几个人", "活动不等于在说什么",
                       "有语言以外的证据时必须引用", "规则算出的主人状态是底稿"):
            self.assertIn(phrase, sys_text)

    def test_user_block_has_codes_rules_and_settings_card(self):
        u = render("v1", S, self.pkg, self.strong, None, [], {})[1]["content"]
        for code in ("rule:owner", "rule:coverage", "rule:time", "rule:place", "rule:self", "s1", "a1", "v1"):
            self.assertIn(code, u)
        self.assertTrue(RULE_CODES >= {"rule:owner", "rule:coverage"})
        self.assertIn(S["place"]["default"], u)
        self.assertIn(S["self_facts"], u)
        self.assertIn("今天下午开会", u)
        self.assertNotIn("voice_names", u)

    def test_speech_text_off_hides_words(self):
        s2 = deep(S, sources={"speech_text": False})
        u = render("v1", s2, self.pkg, self.strong, None, [], {})[1]["content"]
        self.assertNotIn("今天下午开会", u)
        self.assertIn("s1", u)

    def test_previous_entries_marked_as_possibly_wrong_with_age(self):
        prev = {"minute": T0 - MIN, "scene": {"entries": [{"key": "activity.desk", "dimension": "activity", "text": "在桌前打字",
                                                            "confidence": "mid", "basis": "inferred", "last_evidence": T0 - 4 * MIN}]}}
        u = render("v1", S, self.pkg, self.strong, prev, [], {})[1]["content"]
        self.assertIn("上一分钟的判断，可能有错", u)
        self.assertIn("activity.desk", u)
        self.assertIn("4 分钟前", u)

    def test_segments_listed(self):
        u = render("v1", S, self.pkg, self.strong, None, [{"start": T0 - 60 * MIN, "end": T0 - 10 * MIN, "cls": "present", "title": "午后在桌前"}], {})[1]["content"]
        self.assertIn("午后在桌前", u)

    def test_tingwu_only_duplicate_of_previous_minute_is_not_rendered(self):
        # session S starts at T0 − 1 h; pos 57.65 M samples ≈ T0 + 3 s (tingwu is filed by pos, speech by t)
        prev_pkg, _ = build([("speech", speech("S", "s-0", T0 - 30_000, text="上一句", pos=[57_600_000, 57_700_000]))], minute=T0 - MIN)
        pkg, st = build(DOCS + [("tingwu", tingwu("S", 7, T0 + 9000, "重复的听悟句", pos=[57_650_000, 57_720_000]))])
        self.assertTrue(any(x["text"] == "重复的听悟句" for x in pkg["tingwu_only"]))
        self.assertNotIn("重复的听悟句", render("v1", S, pkg, st, None, [], {"prev": prev_pkg})[1]["content"])
        self.assertIn("重复的听悟句", render("v1", S, pkg, st, None, [], {})[1]["content"])

    def test_package_items_carry_pos(self):
        self.assertEqual(self.pkg["speech"][0]["pos"], [16000, 32000])
        self.assertEqual(package.BUILDER_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
