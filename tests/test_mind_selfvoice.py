import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind import selfvoice   # noqa: E402

CLIPS = [{"id": "PLAY-001", "texts": ["你是猴子请来的救兵吗？"]}, {"id": "PLAY-023", "texts": ["Yeah."]},
         {"id": "PLAY-064", "texts": ["没事没事，你这样也会搞笑。"]}]


class SelfVoiceTest(unittest.TestCase):
    def setUp(self):
        self.old = selfvoice.CLIPS
        selfvoice.set_clips(CLIPS)
        self.addCleanup(lambda: selfvoice.set_clips(self.old))

    def test_clip_text_while_hello_sounds_is_hello(self):
        self.assertTrue(selfvoice.is_hello("你是猴子请来的救兵吗", True))
        self.assertTrue(selfvoice.is_hello("Yeah.", True))

    def test_other_words_while_hello_sounds_are_people(self):
        self.assertFalse(selfvoice.is_hello("是还有2403和2402，那我就在好吧", True))

    def test_long_clip_text_without_the_flag_is_an_echo(self):
        self.assertTrue(selfvoice.is_hello("没事没事，你这样也会搞笑", False))

    def test_short_words_without_the_flag_are_people(self):
        self.assertFalse(selfvoice.is_hello("Yeah.", False))

    def test_without_clip_texts_nothing_is_hello(self):
        selfvoice.set_clips([])
        self.assertFalse(selfvoice.is_hello("你是猴子请来的救兵吗", True))

    def test_load_reads_the_json_and_tolerates_a_missing_file(self):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(CLIPS, f, ensure_ascii=False)
        self.assertEqual(selfvoice.load(f.name), 3)
        self.assertEqual(selfvoice.load("/nonexistent/clips.json"), 0)


if __name__ == "__main__":
    unittest.main()
