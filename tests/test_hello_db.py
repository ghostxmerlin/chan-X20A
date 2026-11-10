"""hello database writer, 听悟 message parsing, recording importer and the ear wiring (no network)."""
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server/ear"))
import hello_db                      # noqa: E402
import tingwu                        # noqa: E402
import hello_db_import               # noqa: E402

try:
    import numpy as np               # noqa: F401
    HAVE_EAR = True
except ImportError:
    HAVE_EAR = False


class FakeCollection:
    def __init__(self, store, name): self.store, self.name = store, name
    def replace_one(self, key, doc, upsert=False): self.store.append(("replace", self.name, key, doc))
    def update_one(self, key, update, upsert=False): self.store.append(("update", self.name, key, update["$set"]))


class FakeDB(dict):
    def __init__(self): super().__init__(); self.ops = []
    def __getitem__(self, name): return FakeCollection(self.ops, name)


class HelloDBTest(unittest.TestCase):
    def test_session_key_points_at_the_recording_folder(self):
        self.assertEqual(hello_db.session_key("/home/u/soul-spike/recordings/20261110/154417-stackchan-x-c75/"),
                         "20261110/154417-stackchan-x-c75")

    def test_events_route_to_collections_keyed_by_session_and_id(self):
        fake = FakeDB()
        db = hello_db.HelloDB("unused", client_factory=lambda: fake)
        db.session_open("s1", {"source": "stackchan-x"})
        db.event("s1", {"type": "speech", "phase": "end", "id": "s-1", "text": "你好", "pos": [0, 16000],
                        "speaker": {"band": "known", "name": "person-a", "score": 0.8}})
        db.event("s1", {"type": "speech", "phase": "start", "id": "s-2"})          # not stored
        db.event("s1", {"type": "sound", "phase": "end", "id": "a-1", "kind": "typing"})
        db.event("", {"type": "speech", "phase": "end", "id": "s-3"})              # no session: ignored
        db.tingwu("s1", "sentence", 1, {"text": "你好"})
        db.session_update("s1", {"end_ms": 5})
        db.flush()
        names = [(op, c, k) for op, c, k, _ in fake.ops]
        self.assertEqual(names, [("replace", "sessions", {"session": "s1"}), ("replace", "speech", {"session": "s1", "id": "s-1"}),
                                 ("replace", "sounds", {"session": "s1", "id": "a-1"}),
                                 ("replace", "tingwu", {"session": "s1", "kind": "sentence", "n": 1}),
                                 ("update", "sessions", {"session": "s1"})])
        speech = fake.ops[1][3]
        self.assertEqual(speech["speaker"]["name"], "person-a"); self.assertEqual(speech["engine"], "sensevoice")
        self.assertEqual(fake.ops[3][3]["engine"], "tingwu")
        self.assertEqual(db.written, 5); self.assertEqual(db.dropped, 0)

    def test_only_known_speakers_get_a_name(self):
        row = hello_db.speech_doc("s", {"id": "s-1", "speaker": {"band": "unsure", "name": "person-a", "score": 0.45}})
        self.assertIsNone(row["speaker"]["name"]); self.assertEqual(row["speaker"]["best"], "person-a")

    def test_database_outage_drops_rows_without_blocking(self):
        def broken(): raise ConnectionError("down")
        db = hello_db.HelloDB("unused", client_factory=broken)
        with patch("hello_db.time.sleep"):
            db.speech("s", {"id": "s-1"}); db.flush(10)
        self.assertEqual(db.written, 0); self.assertEqual(db.dropped, 1); self.assertIn("ConnectionError", db.error)


class TingwuParseTest(unittest.TestCase):
    def test_final_and_partial_sentences(self):
        msg = {"payload": {"output": {"action": "recognize-result", "transcription": {
            "sentenceId": 3, "beginTime": 1200, "endTime": 3400, "text": " 今天开会 ", "sentenceEnd": True}}}}
        self.assertEqual(tingwu.sentence_from(msg), {"id": 3, "begin_ms": 1200, "end_ms": 3400, "text": "今天开会", "final": True})
        msg["payload"]["output"]["transcription"]["sentenceEnd"] = False
        self.assertFalse(tingwu.sentence_from(msg)["final"])
        self.assertIsNone(tingwu.sentence_from({"payload": {"output": {"action": "speech-listen"}}}))
        self.assertIsNone(tingwu.sentence_from({"payload": {"output": {"action": "recognize-result", "transcription": {"text": "  "}}}}))

    def test_realtime_times_come_from_words(self):
        msg = {"payload": {"output": {"action": "recognize-result", "transcription": {
            "sentenceId": 0, "sentenceEnd": True, "time": 10120, "text": "步子迈大了",
            "words": [{"beginTime": 30, "endTime": 90, "text": "步"}, {"beginTime": 900, "endTime": 1180, "text": "了"}]}}}}
        s = tingwu.sentence_from(msg)
        self.assertEqual((s["begin_ms"], s["end_ms"]), (30, 1180))

    def test_diarized_paragraphs(self):
        result = {"Transcription": {"Paragraphs": [
            {"SpeakerId": "1", "Words": [{"Start": 0, "End": 500, "Text": "你"}, {"Start": 500, "End": 900, "Text": "好"}]},
            {"SpeakerId": "2", "Words": []}]}}
        self.assertEqual(tingwu.paragraphs_from(result), [{"speaker": "1", "begin_ms": 0, "end_ms": 900, "text": "你好"}])

    def test_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / ".env"
            p.write_text('# c\nA="x"\nB=y=z\n')
            self.assertEqual(tingwu.load_env(p), {"A": "x", "B": "y=z"})
        self.assertEqual(tingwu.load_env("/no/such/file"), {})


class ImportTest(unittest.TestCase):
    def test_reads_meta_and_end_events_only(self):
        with tempfile.TemporaryDirectory() as d:
            folder = pathlib.Path(d) / "20261110" / "090000-stackchan-x-c1"
            folder.mkdir(parents=True)
            (folder / "meta.json").write_text(json.dumps({"source": "stackchan-x", "start_ms": 1, "audio": {"format": "opus"},
                                                          "server_hello": {"models": {"asr": "sv"}}}))
            lines = [{"type": "speech", "phase": "start", "id": "s-1"}, {"type": "speech", "phase": "end", "id": "s-1", "text": "a"},
                     {"type": "sound", "phase": "end", "id": "a-1", "kind": "clap"}, {"type": "hello"}]
            (folder / "events.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
            doc, speech, sounds = hello_db_import.read_session(str(folder))
        self.assertEqual(doc["session"], "20261110/090000-stackchan-x-c1"); self.assertTrue(doc["imported"])
        self.assertEqual([r["id"] for r in speech], ["s-1"]); self.assertEqual([r["id"] for r in sounds], ["a-1"])


@unittest.skipUnless(HAVE_EAR, "needs numpy / sherpa-onnx")
class EarTingwuTest(unittest.IsolatedAsyncioTestCase):
    async def test_robot_session_streams_to_tingwu_and_stores_sentences(self):
        import ear_server
        streams = []

        class FakeStream:
            def __init__(self, key, app, on_sentence, on_final, on_error):
                self.args = (key, app); self.on_sentence, self.on_final, self.on_error = on_sentence, on_final, on_error
                self.fed = bytearray(); self.closed = False; self.data_id = "d-1"; streams.append(self)
            def start(self): pass
            def feed(self, b): self.fed += b
            def close(self): self.closed = True
            def audio_seconds(self): return len(self.fed) / 32000

        rows = []
        db = SimpleNamespace(event=lambda *a: rows.append(("event",) + a), session_open=lambda *a: rows.append(("open",) + a),
                             session_update=lambda *a: rows.append(("update",) + a), tingwu=lambda *a: rows.append(("tingwu",) + a))
        ex = ThreadPoolExecutor(1)
        with patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)), \
                patch.object(ear_server.tingwu_rt, "TingwuStream", FakeStream), tempfile.TemporaryDirectory() as d:
            models = SimpleNamespace(vad_cfg=None, speakers={}, lock=__import__("threading").Lock(), denoise_cfg=None, new_denoiser=lambda: None)
            ear = ear_server.Ear(models, None, ex)
            ear.rec = ear_server.recorder.Recorder(d, 1, "peer", ear.hello)
            ear.db, ear.tingwu_keys = db, ("k", "app")
            ear.vad = SimpleNamespace(reset=lambda: None, accept_waveform=lambda w: None, is_speech_detected=lambda: False, empty=lambda: True)
            ear.next_tag = 10 ** 9
            await ear.on_text('{"type":"audio","format":"pcm16","sample_rate":16000,"channels":1,"source":"stackchan-x"}')
            self.assertEqual(len(streams), 1); self.assertEqual(streams[0].args, ("k", "app"))
            self.assertTrue(ear.session.endswith("-stackchan-x-c1"))
            await ear.on_audio(b"\x01\x00" * 1600)
            self.assertEqual(len(streams[0].fed), 3200)
            streams[0].on_sentence({"id": 1, "begin_ms": 0, "end_ms": 100, "text": "你好", "final": True})
            streams[0].on_sentence({"id": 2, "begin_ms": 0, "end_ms": 50, "text": "你", "final": False})   # partial: ignored
            posted = [e for e in ear.outbox if e.get("type") == "tingwu"]
            self.assertEqual(len(posted), 1); self.assertEqual(posted[0]["text"], "你好"); self.assertEqual(posted[0]["pos"], [0, 1600])
            tw = [r for r in rows if r[0] == "tingwu"]
            self.assertEqual(tw[0][1:4], (ear.session, "sentence", 1))
            await ear.close(); ear.rec.close()
            self.assertTrue(streams[0].closed)
            streams[0].on_final([{"speaker": "1", "text": "你好"}])
            self.assertEqual([r[1:4] for r in rows if r[0] == "tingwu"][-1], (ear.session, "paragraph", 1))
            self.assertTrue(any(r[0] == "open" for r in rows) and any(r[0] == "update" for r in rows))
        ex.shutdown()

    async def test_mac_source_never_starts_tingwu(self):
        import ear_server
        with patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)), \
                patch.object(ear_server.tingwu_rt, "TingwuStream", side_effect=AssertionError("no tingwu for mac")):
            models = SimpleNamespace(vad_cfg=None, speakers={}, lock=__import__("threading").Lock(), denoise_cfg=None, new_denoiser=lambda: None)
            ear = ear_server.Ear(models, None, ThreadPoolExecutor(1))
            ear.tingwu_keys = ("k", "app")
            await ear.on_text('{"type":"audio","format":"pcm16","sample_rate":16000,"channels":1,"source":"mac-mic"}')
            self.assertIsNone(ear.tingwu)


if __name__ == "__main__":
    unittest.main()
