"""server/ear/intent.py: rules, the auto head (fake embedder; no model files needed)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import intent  # noqa: E402

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None


class FakeEmbed:
    """bag of characters -> unit vector (deterministic, similar texts share characters)."""
    np = np

    def __call__(self, text):
        v = np.zeros(64, dtype=np.float32)
        for ch in str(text):
            v[hash(ch) % 64] += 1
        return v / (np.linalg.norm(v) or 1)


@unittest.skipUnless(np is not None, "needs numpy")
class IntentTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(intent.rule("十分钟后提醒我喝水"), "daily")
        self.assertEqual(intent.rule("明天北京天气怎么样"), "world")
        self.assertEqual(intent.rule("转过来看着我"), "body")
        self.assertIsNone(intent.rule("哈哈好吧"))

    def test_auto_head_files_and_reload(self):
        d = tempfile.mkdtemp()
        w = [0.0] * 64 + [6.0]                            # only the rule flag matters in this head
        json.dump({"w": w, "b": -3.0, "threshold": 0.5, "version": "v1"}, open(os.path.join(d, "head.json"), "w"))
        json.dump([["明天会下雨吗", "world"], ["点点头", "body"]], open(os.path.join(d, "labelled.json"), "w"), ensure_ascii=False)
        a = intent.Auto(FakeEmbed(), art_dir=d)
        self.assertTrue(a.ready)
        got = a.decide("帮我查一下明天天气")
        self.assertGreater(got["need"], 0.9)
        self.assertEqual(got["skills"][0], "world")
        self.assertLess(a.decide("你好呀")["need"], 0.1)
        self.assertEqual(a.decide("嗯")["via"], "short")
        self.assertFalse(a.reload())                       # same files: nothing to do
        empty = intent.Auto(FakeEmbed(), art_dir=tempfile.mkdtemp())
        self.assertEqual(empty.decide("天气")["via"], "off")


if __name__ == "__main__":
    unittest.main()
