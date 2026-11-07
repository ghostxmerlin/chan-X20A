import pathlib
import subprocess
import tempfile
import unittest

from emotion.core import OutputWatchdog, SoundGate, transcript_event, validate_inference


class EmotionTest(unittest.TestCase):
    def test_no_output_disconnect_boundary(self):
        watch = OutputWatchdog(10)
        self.assertFalse(watch.output(200, 1, 1, " "))
        self.assertFalse(watch.expire(609.999))
        self.assertTrue(watch.expire(610))
        self.assertFalse(watch.expire(611))
        self.assertFalse(watch.output(612, 1, 2, "迟到的文本"))
        self.assertFalse(watch.wake(613, False))
        self.assertTrue(watch.wake(614, True))
        self.assertFalse(watch.expire(1213.999))
        self.assertTrue(watch.expire(1214))

    def test_new_nonempty_output_only(self):
        watch = OutputWatchdog(0)
        self.assertTrue(watch.output(550, 1, 1, "你好"))
        self.assertFalse(watch.output(900, 1, 1, "你好"))
        self.assertTrue(watch.output(1000, 1, 2, "你好"))
        self.assertFalse(watch.expire(1599.99))
        self.assertTrue(watch.expire(1600))

    def test_sound_rearm_after_idle(self):
        gate = SoundGate()
        self.assertEqual([gate.feed(0.1) for _ in range(4)], [False, False, False, True])
        gate.disarm()
        self.assertFalse(any(gate.feed(0.1) for _ in range(100)))
        self.assertFalse(any(gate.feed(0.001) for _ in range(16)))
        self.assertTrue(any(gate.feed(0.1) for _ in range(5)))

    def test_tingwu_events(self):
        self.assertIsNone(transcript_event({"header": {"name": "TranscriptionStarted"}}))
        self.assertIsNone(transcript_event({"header": {"name": "SentenceEnd"}, "payload": {"result": ""}}))
        self.assertEqual(transcript_event({"header": {"name": "SentenceEnd"},
            "payload": {"index": 2, "result": "你好", "time": 450}}), (2, "你好", True, 450))

    def test_evidence_is_required(self):
        result = '{"emotion":"happy","certainty":"medium","evidence":"both"}'
        self.assertEqual(validate_inference(result, True, 1), ("happy", "both"))
        for text, count in [(False, 1), (True, 0), (True, 2)]:
            self.assertEqual(validate_inference(result, text, count), ("unknown", "none"))
        self.assertEqual(validate_inference(result.replace('medium','low'),True,1), ("unknown", "none"))
        self.assertEqual(validate_inference('garbage',True,1), ("unknown", "none"))

    def test_firmware_stale_and_reordered_results(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder)/"test"
            subprocess.run(["c++", "-std=c++17", "-I"+str(root/"upstream/StackChan/firmware/main"),
                            str(root/"tests/emotion_state_test.cpp"), "-o", str(target)], check=True)
            subprocess.run([str(target)], check=True)


if __name__ == "__main__":
    unittest.main()
