"""嘴 mode for the ear: hello.switches {_id: "mouth", mode: off|clips|tts}; clips answer only in clips mode."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import mouth_mode  # noqa: E402


class MouthModeTest(unittest.TestCase):
    def test_no_document_keeps_todays_clips(self):
        r = mouth_mode.ModeReader(lambda: None)
        self.assertEqual((r.mode, r.respond), ("clips", True))
        r.update()
        self.assertEqual((r.mode, r.respond), ("clips", True))

    def test_tts_and_off_silence_the_clips(self):
        doc = {"_id": "mouth", "mode": "tts"}
        r = mouth_mode.ModeReader(lambda: doc)
        r.update()
        self.assertEqual((r.mode, r.respond), ("tts", False))
        doc["mode"] = "off"; r.update()
        self.assertEqual((r.mode, r.respond), ("off", False))
        doc["mode"] = "clips"; r.update()
        self.assertTrue(r.respond)

    def test_a_failed_read_keeps_the_last_mode(self):
        state = {"doc": {"mode": "tts"}, "fail": False}

        def read():
            if state["fail"]:
                raise RuntimeError("mongo blip")
            return state["doc"]
        r = mouth_mode.ModeReader(read)
        r.update()
        state["fail"] = True
        r.update()
        self.assertEqual(r.mode, "tts")              # never falls back to clips while we are in TTS

    def test_an_unknown_mode_is_ignored(self):
        r = mouth_mode.ModeReader(lambda: {"mode": "shout"})
        r.update()
        self.assertEqual(r.mode, "clips")


if __name__ == "__main__":
    unittest.main()
