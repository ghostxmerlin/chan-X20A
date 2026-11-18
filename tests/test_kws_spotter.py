"""Keyword ear core (server/kws/spotter.py) and service (kws_server.py) with a stand-in feature model: matching,
the short-sound guard, the keyword table, the HTTP service. Needs numpy, librosa, soundfile (no ONNX model):

    spikes/kws/.venv/bin/python -B -m unittest discover -s tests -p 'test_kws_spotter.py' -v
"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "kws"))
try:
    import librosa  # noqa: F401
    import soundfile as sf
    import spotter
    import kws_server
    HAVE = True
except ImportError:
    HAVE = False

SR = 16000


class FakeModel:
    """Frame features straight from the waveform: one frame per 320 samples, a one-hot of a 'tone id' coded in
    the sample value, so tests can write a 'word' as a list of tone ids."""

    def feats(self, x):
        x = np.asarray(x)
        n = max(1, len(x) // 320)
        ids = np.round(x[:n * 320].reshape(n, 320).mean(1) * 10).astype(int)
        f = np.full((n, 8), 0.05, np.float32)
        f[np.arange(n), np.clip(ids, 0, 7)] = 1.0
        return f


def word(ids, frames_each=6):
    return np.concatenate([np.full(320 * frames_each, i / 10, np.float32) for i in ids])


@unittest.skipUnless(HAVE, "needs librosa + soundfile")
class SpotterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        kdir = Path(self.tmp.name) / "dynames"
        kdir.mkdir()
        (kdir / "keyword.json").write_text(json.dumps({"label": "Dynames", "threshold": 0.35}))
        sf.write(kdir / "a.wav", word([1, 2, 3, 4]), SR)
        spotter.trim = lambda x: x                      # the fake signal has no silence to trim
        self.sp = spotter.Spotter(FakeModel(), self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_word_is_found_inside_a_longer_segment(self):
        r = self.sp.score(np.concatenate([word([5, 6, 7]), word([1, 2, 3, 4]), word([6, 5])]))["dynames"]
        self.assertTrue(r["hit"])
        self.assertLess(r["score"], 0.1)
        self.assertEqual(r["sample"], "a.wav")

    def test_other_words_do_not_match(self):
        r = self.sp.score(word([5, 6, 7, 5, 6]))["dynames"]
        self.assertFalse(r["hit"])

    def test_a_short_sound_cannot_stand_for_the_whole_word(self):
        # one frame of each tone: the path squeezes 24 sample frames into 4, a cheap but wrong match
        guarded = self.sp.score(word([1, 2, 3, 4], frames_each=1))["dynames"]["score"]
        raw = spotter.sdtw(self.sp.keywords[0].samples[0][1], FakeModel().feats(word([1, 2, 3, 4], 1)), 0.0)
        self.assertGreater(guarded, raw + 0.3)

    def test_a_segment_shorter_than_the_sample_is_not_matched_inside_it(self):
        # librosa swaps a short query into the sample (subseq DTW); half the word must not score like the word
        t = self.sp.keywords[0].samples[0][1]
        half = FakeModel().feats(word([1, 2]))                      # 12 frames of a 24-frame sample
        self.assertGreater(spotter.sdtw(t, half, 0.6), 0.3)
        self.assertFalse(self.sp.score(word([1, 2]))["dynames"]["hit"])

    def test_skip_leaves_a_sample_out(self):
        r = self.sp.score(word([1, 2, 3, 4]), skip={"a.wav"})["dynames"]
        self.assertIsNone(r["sample"])
        self.assertFalse(r["hit"])

    def test_reload_picks_up_new_keywords(self):
        k2 = Path(self.tmp.name) / "stop"
        k2.mkdir()
        (k2 / "keyword.json").write_text("{}")
        sf.write(k2 / "x.wav", word([7, 6]), SR)
        self.assertEqual([k.name for k in self.sp.reload()], ["dynames", "stop"])
        self.assertEqual(self.sp.keywords[1].threshold, spotter.DEFAULTS["threshold"])

    def stream_of(self, parts, chunk=8000):
        """Feed a whole signal in chunks; -> all reports."""
        x = np.concatenate(parts)
        st = spotter.Stream(self.sp)
        out = []
        for i in range(0, len(x), chunk):
            out += st.feed(x[i:i + chunk], 5000 + i)
        return out + st.flush(), st

    def test_stream_finds_the_word_once_without_sentences(self):
        bed = word([5, 6, 7, 6] * 10, frames_each=10) * 0.05 + 0.0     # quiet babble: the floor (tone 0)
        loud = word([1, 2, 3, 4])
        reps, st = self.stream_of([bed, loud, bed])
        hits = [r for r in reps if r["hit"]]
        self.assertEqual(len(hits), 1)                   # several looks saw it: one report
        self.assertLess(hits[0]["start"], 5000 + len(bed) + len(loud))
        self.assertGreater(hits[0]["end"], 5000 + len(bed))

    def test_stream_reports_a_hit_at_once(self):
        bed = word([5, 6, 7, 6] * 10, frames_each=10) * 0.05
        x = np.concatenate([bed, word([1, 2, 3, 4]), bed])
        st = spotter.Stream(self.sp)
        word_end = len(bed) + len(word([1, 2, 3, 4]))
        got_at = None
        for i in range(0, len(x), 2000):              # 125 ms chunks
            if any(r["hit"] for r in st.feed(x[i:i + 2000], i)) and got_at is None:
                got_at = i + 2000
        self.assertIsNotNone(got_at)
        self.assertLessEqual(got_at - word_end, int(0.6 * SR))   # no 1 s hold before a hit

    def test_stream_gate_skips_silence(self):
        reps, st = self.stream_of([np.zeros(SR * 6, np.float32)])
        self.assertEqual((reps, st.looks), ([], 0))

    def test_stream_two_sayings_far_apart_are_two_reports(self):
        bed = word([5, 6, 7, 6] * 10, frames_each=10) * 0.05
        reps, _ = self.stream_of([bed, word([1, 2, 3, 4]), bed, word([1, 2, 3, 4]), bed])
        self.assertEqual(sum(r["hit"] for r in reps), 2)

    def test_stream_gap_starts_over(self):
        st = spotter.Stream(self.sp)
        st.feed(np.full(SR, 0.1, np.float32), 0)
        st.feed(np.full(SR, 0.1, np.float32), 10 * SR)    # not contiguous
        self.assertEqual(st.buf_start, 10 * SR)

    def test_http_service(self):
        svc = kws_server.Service(self.sp)
        srv = kws_server.HTTPServer(("127.0.0.1", 0), kws_server.handler_for(svc))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            pcm = (word([5, 1, 2, 3, 4, 6]) * 32767).astype("<i2").tobytes()
            req = urllib.request.Request(base + "/score?session=s&id=7", data=pcm, method="POST")
            res = json.loads(urllib.request.urlopen(req, timeout=5).read())
            self.assertTrue(res["keywords"]["dynames"]["hit"])
            st = json.loads(urllib.request.urlopen(base + "/status", timeout=5).read())
            self.assertEqual(st["scored"], 1)
            self.assertEqual(st["keywords"][0]["samples"], 1)
            bed = word([5, 6, 7, 6] * 10, frames_each=10) * 0.05
            sig = np.concatenate([bed, word([1, 2, 3, 4]), bed])
            reps = []
            for i in range(0, len(sig), 8000):
                pcm = (sig[i:i + 8000] * 32767).astype("<i2").tobytes()
                req = urllib.request.Request(f"{base}/feed?stream=c1&pos={i}", data=pcm, method="POST")
                reps += json.loads(urllib.request.urlopen(req, timeout=5).read())["reports"]
            self.assertEqual(sum(r["hit"] for r in reps), 1)
            st = json.loads(urllib.request.urlopen(base + "/status", timeout=5).read())
            self.assertIn("c1", st["streams"])
            self.assertGreater(st["fed_s"], 5)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
