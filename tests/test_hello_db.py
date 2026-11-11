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
    def find_one(self, flt): return self.store.docs.get((self.name, flt.get("_id")))


class Ops(list):
    def __init__(self): super().__init__(); self.docs = {}


class FakeDB(dict):
    def __init__(self): super().__init__(); self.ops = Ops()
    def __getitem__(self, name): return FakeCollection(self.ops, name)


class HelloDBTest(unittest.TestCase):
    def test_read_switch_reads_the_switches_collection(self):
        fake = FakeDB()
        db = hello_db.HelloDB("unused", client_factory=lambda: fake)
        self.assertIsNone(db.read_switch("tingwu"))
        fake.ops.docs[("switches", "tingwu")] = {"_id": "tingwu", "on": True}
        self.assertEqual(db.read_switch("tingwu"), {"_id": "tingwu", "on": True})

    def test_switch_status_is_queued_as_an_update(self):
        fake = FakeDB()
        db = hello_db.HelloDB("unused", client_factory=lambda: fake)
        db.switch_status("tingwu", {"running": ["s1"], "available": True, "at": 5})
        db.flush()
        self.assertIn(("update", "switches", {"_id": "tingwu_status"}, {"running": ["s1"], "available": True, "at": 5}), fake.ops)

    def test_frame_is_upserted_by_session_and_seq(self):
        fake = FakeDB()
        db = hello_db.HelloDB("unused", client_factory=lambda: fake)
        db.frame({"session": "s1", "seq": 4, "path": "frames/s1/1-4.jpg"})
        db.flush()
        self.assertIn(("replace", "frames", {"session": "s1", "seq": 4}, {"session": "s1", "seq": 4, "path": "frames/s1/1-4.jpg"}), fake.ops)

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

    def test_vision_events_get_their_wall_time_and_a_stable_key(self):
        fake = FakeDB()
        db = hello_db.HelloDB("unused", client_factory=lambda: fake)
        db.vision("s1", {"type": "vision", "event": "face_named", "t_us": 5_000_000, "now_us": 7_000_000, "face": 4,
                         "name": "person-a", "box": [1, 2, 3, 4], "junk": 1}, received_ms=100_000)
        db.vision("s1", {"type": "vision", "event": "vision_on", "t_us": 1}, received_ms=50)
        db.flush()
        (op, coll, key, doc), (_, _, key2, doc2) = fake.ops
        self.assertEqual((op, coll), ("replace", "vision"))
        self.assertEqual(key, {"session": "s1", "t_us": 5_000_000, "event": "face_named", "face": 4, "person": None})
        self.assertEqual(doc["at"], 98_000); self.assertEqual(doc["received"], 100_000)   # waited 2 s on the robot
        self.assertEqual(doc["name"], "person-a"); self.assertNotIn("junk", doc); self.assertNotIn("type", doc)
        self.assertEqual(doc2["at"], 50)

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
            ear.tingwu_switch = SimpleNamespace(on=True)
            ear.tingwu_tick()
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

    # ---- 听悟 follows the server switch (plan 2026-11-11-hearing-tiers, Task 3) ----
    def switched_ear(self, ear_server, d, switch_on):
        self.streams, self.rows = [], []
        streams = self.streams

        class FakeStream:
            def __init__(self, key, app, on_sentence, on_final, on_error):
                self.on_sentence, self.on_final, self.on_error = on_sentence, on_final, on_error
                self.fed = bytearray(); self.closed = False; self.data_id = f"d-{len(streams) + 1}"; streams.append(self)
            def start(self): pass
            def feed(self, b): self.fed += b
            def close(self): self.closed = True
            def audio_seconds(self): return len(self.fed) / 32000

        rows = self.rows
        db = SimpleNamespace(event=lambda *a: None, session_open=lambda *a: None,
                             session_update=lambda *a: rows.append(("update",) + a), tingwu=lambda *a: rows.append(("tingwu",) + a))
        self.patches = [patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)),
                        patch.object(ear_server.tingwu_rt, "TingwuStream", FakeStream)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        models = SimpleNamespace(vad_cfg=None, speakers={}, lock=__import__("threading").Lock(), denoise_cfg=None, new_denoiser=lambda: None)
        ear = ear_server.Ear(models, None, ThreadPoolExecutor(1))
        ear.rec = ear_server.recorder.Recorder(d, 1, "peer", ear.hello)
        ear.db, ear.tingwu_keys = db, ("k", "app")
        ear.vad = SimpleNamespace(reset=lambda: None, accept_waveform=lambda w: None, is_speech_detected=lambda: False, empty=lambda: True)
        ear.next_tag = 10 ** 9
        ear.tingwu_switch = SimpleNamespace(on=switch_on)
        return ear

    async def audio_hello(self, ear):
        await ear.on_text('{"type":"audio","format":"pcm16","sample_rate":16000,"channels":1,"source":"stackchan-x"}')

    async def test_tingwu_waits_for_the_switch(self):
        import ear_server
        with tempfile.TemporaryDirectory() as d:
            ear = self.switched_ear(ear_server, d, False)
            await self.audio_hello(ear)
            ear.tingwu_tick()
            self.assertEqual(self.streams, [])
            ear.tingwu_switch.on = True
            ear.tingwu_tick()
            self.assertEqual(len(self.streams), 1)
            await ear.close(); ear.rec.close()

    async def test_reconnect_with_the_switch_on_starts_at_once(self):
        import ear_server
        with tempfile.TemporaryDirectory() as d:
            ear = self.switched_ear(ear_server, d, True)
            ear.tingwu_tick()
            self.assertEqual(self.streams, [])          # no audio message yet
            await self.audio_hello(ear)
            ear.tingwu_tick()
            self.assertEqual(len(self.streams), 1)
            await ear.close(); ear.rec.close()

    async def test_switch_off_closes_the_stream_mid_session(self):
        import ear_server
        with tempfile.TemporaryDirectory() as d:
            ear = self.switched_ear(ear_server, d, True)
            await self.audio_hello(ear)
            ear.tingwu_tick()
            ear.tingwu_switch.on = False
            ear.tingwu_tick()
            self.assertTrue(self.streams[0].closed)
            self.assertIsNone(ear.tingwu)
            await ear.on_audio(b"\x01\x00" * 1600)
            self.assertEqual(len(self.streams[0].fed), 0)
            await ear.close(); ear.rec.close()

    async def test_second_stream_positions_and_paragraphs_continue(self):
        import ear_server
        with tempfile.TemporaryDirectory() as d:
            ear = self.switched_ear(ear_server, d, True)
            await self.audio_hello(ear)
            ear.tingwu_tick()
            first = self.streams[0]
            ear.tingwu_switch.on = False
            ear.tingwu_tick()
            for _ in range(3):
                await ear.on_audio(b"\x01\x00" * 16000)       # 3 s = 48000 samples before the second stream
            self.assertEqual(ear.ring.end, 48000)
            ear.tingwu_switch.on = True
            ear.tingwu_tick()
            second = self.streams[1]
            second.on_sentence({"id": 1, "begin_ms": 1000, "end_ms": 2000, "text": "第二段", "final": True})
            posted = [e for e in ear.outbox if e.get("type") == "tingwu"]
            self.assertEqual(posted[-1]["pos"], [48000 + 16000, 48000 + 32000])
            first.on_final([{"speaker": "1", "text": "a"}, {"speaker": "1", "text": "b"}])
            await ear.close()
            second.on_final([{"speaker": "1", "text": "c"}])
            ear.rec.close()
            paragraphs = [r[3] for r in self.rows if r[0] == "tingwu" and r[2] == "paragraph"]
            self.assertEqual(paragraphs, [1, 2, 3])
            keys = {k for r in self.rows if r[0] == "update" for k in r[2]}
            self.assertTrue({"tingwu_streams.1", "tingwu_streams.2", "tingwu"} <= keys)
            s2 = next(r[2]["tingwu_streams.2"] for r in self.rows if r[0] == "update" and "tingwu_streams.2" in r[2])
            self.assertEqual((s2["offset_samples"], s2["paragraphs"], s2["sentences"]), (48000, 1, 1))

    async def test_start_error_backs_off_60_s(self):
        import ear_server
        with tempfile.TemporaryDirectory() as d:
            ear = self.switched_ear(ear_server, d, True)
            await self.audio_hello(ear)
            clock = [1000.0]
            with patch.object(ear_server.time, "monotonic", lambda: clock[0]):
                ear.tingwu_tick()
                self.streams[0].on_error("createTask failed")
                self.assertIsNone(ear.tingwu)
                for dt in (0.1, 30, 59.9):
                    clock[0] = 1000.0 + dt
                    ear.tingwu_tick()
                self.assertEqual(len(self.streams), 1)
                clock[0] = 1060.5
                ear.tingwu_tick()
                self.assertEqual(len(self.streams), 2)
            await ear.close(); ear.rec.close()

    async def test_rapid_toggle_keeps_one_stream(self):
        import ear_server
        with tempfile.TemporaryDirectory() as d:
            ear = self.switched_ear(ear_server, d, True)
            await self.audio_hello(ear)
            for on in (True, False, True, False, True):
                ear.tingwu_switch.on = on
                ear.tingwu_tick()
                self.assertLessEqual(sum(not s.closed for s in self.streams), 1)
            self.assertEqual(len(self.streams), 3)
            self.assertEqual([s.closed for s in self.streams], [True, True, False])
            await ear.close(); ear.rec.close()
            self.assertTrue(all(s.closed for s in self.streams))

    async def test_vision_message_is_stored_not_answered(self):
        import ear_server
        rows = []
        with patch.object(ear_server.so, "VoiceActivityDetector", return_value=SimpleNamespace(reset=lambda: None)):
            models = SimpleNamespace(vad_cfg=None, speakers={}, lock=__import__("threading").Lock(), denoise_cfg=None, new_denoiser=lambda: None)
            ear = ear_server.Ear(models, None, ThreadPoolExecutor(1))
            ear.db = SimpleNamespace(vision=lambda *a: rows.append(a), event=lambda *a: None)
            ear.session = "20261110/x-c1"
            before = len(ear.outbox)
            await ear.on_text('{"type":"vision","event":"face_found","t_us":1,"face":3}')
            self.assertEqual(rows[0][0], "20261110/x-c1"); self.assertEqual(rows[0][1]["face"], 3)
            self.assertEqual(len(ear.outbox), before)
            await ear.on_text('{"type":"vision"}')
            self.assertEqual(ear.outbox[-1]["type"], "error")

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
