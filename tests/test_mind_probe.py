import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mind_fixtures  # noqa: E402,F401  (puts server/ on the path)
from mind import probe  # noqa: E402


def reply(content=None, tool_args=None):
    msg = {"role": "assistant", "content": content}
    if tool_args is not None:
        msg["tool_calls"] = [{"type": "function", "function": {"name": "scene", "arguments": tool_args}}]
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


GOOD = '{"owner_present":"yes","activity":"工作","people_count":1}'


class ProbeTest(unittest.TestCase):
    def test_request_body_modes(self):
        b = probe.request_body("m", "json_schema", "p")
        self.assertEqual(b["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(b["response_format"]["json_schema"]["schema"]["additionalProperties"], False)
        self.assertEqual(probe.request_body("m", "tools", "p")["tool_choice"]["function"]["name"], "scene")
        self.assertEqual(probe.request_body("m", "json_object", "p")["response_format"], {"type": "json_object"})
        self.assertNotIn("response_format", probe.request_body("m", "plain", "p"))
        self.assertIn("<json>", probe.request_body("m", "plain", "p")["messages"][-1]["content"])
        self.assertEqual(probe.MODES, ["json_schema", "tools", "json_object", "plain"])

    def test_image_is_data_uri(self):
        content = probe.request_body("m", "plain", "p", image_b64="QUJD")["messages"][-1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64,QUJD")

    def test_parse_each_mode(self):
        self.assertEqual(probe.parse("json_schema", reply(GOOD))["activity"], "工作")
        self.assertEqual(probe.parse("tools", reply(None, GOOD))["people_count"], 1)
        self.assertEqual(probe.parse("plain", reply("说明 <json>" + GOOD + "</json>"))["people_count"], 1)
        self.assertEqual(probe.parse("json_object", reply("```json\n" + GOOD + "\n```"))["owner_present"], "yes")
        self.assertIsNone(probe.parse("json_object", reply('{"owner_present":"maybe","activity":"x","people_count":1}')))
        self.assertIsNone(probe.parse("json_object", reply('{"owner_present":"yes","activity":"x","people_count":true}')))
        self.assertIsNone(probe.parse("plain", reply("no json here")))
        self.assertIsNone(probe.parse("tools", reply("text only")))

    def test_cost_estimate(self):
        self.assertAlmostEqual(probe.cost_usd("gpt-6-luna", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}), 0.6)
        self.assertIsNone(probe.cost_usd("other", {"prompt_tokens": 1}))

    def test_prompt_from_package_mentions_speech_and_faces(self):
        pkg = {"speech": [{"text": "你好", "who": {"kind": "known", "name": "me"}}], "faces": [{"name": "me"}],
               "sounds": [{"label": "键盘打字", "count": 2}]}
        text = probe.prompt_from(pkg, {"owner": {"state": "seen"}, "time": {"label": "11-10 16:40"}})
        self.assertIn("你好", text); self.assertIn("me", text); self.assertIn("键盘打字", text)


if __name__ == "__main__":
    unittest.main()
