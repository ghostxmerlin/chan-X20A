import asyncio
import pathlib
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'server/ear'))
try:
    import numpy as np
    import hearing_protocol as hp
except ImportError:
    hp = None


class EarProtocolTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(hp, 'hearing protocol implementation must be available')

    def test_exact_auth_and_legacy_compatibility(self):
        self.assertTrue(hp.authenticate('/?token=abc', '', 'abc'))
        self.assertTrue(hp.authenticate('/', 'Bearer abc', 'abc'))
        for path in ('/?token=abcdef', '/?x=token=abc', '/?token=abc&token=bad'):
            self.assertFalse(hp.authenticate(path, '', 'abc'))
        self.assertFalse(hp.authenticate('/', 'Bearer abcX', 'abc'))

    def test_framing_rejects_truncation_and_preserves_opus(self):
        data = b'SEA1' + struct.pack('<IQ', 3, 123456) + b'\xf8\xff\xfe'
        self.assertEqual(hp.parse_audio_packet(data, 'sea1'), (b'\xf8\xff\xfe', 3, 123456))
        for invalid in (data[:16], b'BAD!' + data[4:], b''):
            with self.assertRaises(ValueError):
                hp.parse_audio_packet(invalid, 'sea1')

    def test_24k_declaration_and_bad_shapes(self):
        self.assertEqual(hp.audio_format({'format':'opus','sample_rate':24000,'channels':1,'frame_ms':60,'framing':'sea1'}), ('opus',24000,'sea1'))
        for msg in ({'format':'pcm16','sample_rate':24000}, {'channels':2}, {'framing':'sea1'}, {'format':'opus','sample_rate':True}):
            with self.assertRaises(ValueError): hp.audio_format(msg)

    def test_capture_self_overlap_survives_flag_change(self):
        history = hp.SelfHistory()
        history.add(0, 960, {'sounding':True,'moving':False})
        history.add(960, 1920, {'sounding':False,'moving':False})
        self.assertEqual(history.overlap(500,1500), {'sounding':True,'moving':False})
        self.assertFalse(history.overlap(960,1920)['sounding'])

    def test_enrollment_cancel_and_new_generation_reject_old_result(self):
        e = hp.Enrollment()
        e.start({'name':'papa','request_id':'one','count':5}, {}, 0)
        epoch = e.epoch
        e.cancel()
        e.start({'name':'papa','request_id':'two'}, {}, 1)
        self.assertFalse(e.accept(epoch, np.ones(3), 2, True, False, 2))
        self.assertEqual(e.progress()['n'], 0)
        self.assertFalse(e.accept(e.epoch, np.ones(3), 2, True, True, 2))
        self.assertFalse(e.accept(e.epoch, np.ones(3), .5, True, False, 2))
        self.assertFalse(e.accept(e.epoch, np.ones(3), 2, False, False, 2))
        for _ in range(4): self.assertFalse(e.accept(e.epoch, np.ones(3), 2, True, False, 2))
        self.assertTrue(e.accept(e.epoch, np.ones(3), 2, True, False, 2))
        e.cancel()
        self.assertFalse(e.active)

    def test_heard_copy_is_a_playable_opus_file_with_the_same_length(self):
        import recorder, opuslib_next
        with tempfile.TemporaryDirectory() as tmp:
            r = recorder.Recorder(tmp, 1, "peer", {"type": "hello"})
            r.declare({"type": "audio", "format": "pcm16", "source": "stackchan-test"})
            x = (0.1 * np.sin(np.arange(16000 * 2) / 5)).astype(np.float32)
            for i in range(0, len(x), 700):                 # uneven chunks, like the denoiser's output
                r.heard_in(x[i:i + 700])
            r.close()
            data = (pathlib.Path(r.dir) / "heard.opus").read_bytes()
            self.assertTrue(data.startswith(b"OggS"))
            meta = __import__("json").loads((pathlib.Path(r.dir) / "meta.json").read_text())
            self.assertEqual(meta["heard_file"], "heard.opus")
            # last page granule (48 kHz units) = whole 60 ms frames written
            granule = struct.unpack_from("<q", data, data.rfind(b"OggS") + 6)[0]
            self.assertEqual(granule // 3, len(x) // 960 * 960)

    def test_enrollment_says_why_a_sentence_did_not_count(self):
        e = hp.Enrollment(); e.start({'name':'person-a'}, {}, 0)
        emb = np.ones(3)
        for dur, voice, sounding, why in ((0.7, True, False, 'too_short'), (13, True, False, 'too_long'),
                                          (2, False, False, 'not_voice'), (2, True, True, 'self_voice')):
            self.assertFalse(e.accept(e.epoch, emb, dur, voice, sounding, 1)); self.assertEqual(e.rejected, why)
        self.assertFalse(e.accept(e.epoch, np.zeros(3), 2, True, False, 1)); self.assertEqual(e.rejected, 'bad_embedding')
        self.assertFalse(e.accept(e.epoch, emb, 2, True, False, 1)); self.assertEqual(e.rejected, '')
        self.assertEqual(len(e.embs), 1)

    def test_forget_speaker_is_atomic_and_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'speakers.npz'
            speakers = {'person-a':np.array([1.,0.],np.float32), 'person-b':np.array([0.,1.],np.float32)}
            np.savez(path, **speakers)
            before = path.read_bytes()
            with self.assertRaises(KeyError): hp.forget_speaker(path, speakers, 'nobody')
            self.assertEqual(path.read_bytes(), before)
            with patch('hearing_protocol.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError): hp.forget_speaker(path, speakers, 'person-a')
            self.assertEqual(path.read_bytes(), before)
            updated = hp.forget_speaker(path, speakers, 'person-a')
            self.assertEqual(set(updated), {'person-b'}); self.assertEqual(set(speakers), {'person-a','person-b'})
            self.assertEqual(set(np.load(path).files), {'person-b'})
            self.assertTrue(any(p.read_bytes() == before for p in pathlib.Path(tmp).glob('*.bak-*')))

    def test_enrollment_timeout_and_explicit_replace(self):
        e = hp.Enrollment()
        with self.assertRaises(ValueError): e.start({'name':'papa'}, {'papa':1},0)
        e.start({'name':'papa','replace':True}, {'papa':1},0)
        self.assertTrue(e.expired(121))
        for name in ('', '../x', 'a'*49):
            with self.assertRaises(ValueError): e.start({'name':name},{},0)

    def test_atomic_save_keeps_old_on_failure_and_backs_up_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'speakers.npz'
            old = {'papa':np.array([1.,0.],np.float32)}
            np.savez(path, **old)
            before = path.read_bytes()
            with patch('hearing_protocol.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError): hp.save_speaker(path, old,'mama',[np.array([0.,1.])],False)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(set(old), {'papa'})
            with self.assertRaises(ValueError): hp.save_speaker(path,old,'papa',[np.array([0.,1.])],False)
            updated = hp.save_speaker(path,old,'papa',[np.array([0.,1.])],True)
            np.testing.assert_array_equal(updated['papa'], [0.,1.])
            self.assertTrue(any(p.read_bytes() == before for p in pathlib.Path(tmp).glob('*.bak-*')))


if __name__ == '__main__': unittest.main()


class EarLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import ear_server
        self.module = ear_server
        self.executor = ThreadPoolExecutor(1)
        self.vad_patch = patch.object(ear_server.so, 'VoiceActivityDetector', return_value=SimpleNamespace(reset=lambda:None))
        self.vad_patch.start()
        self.models = SimpleNamespace(vad_cfg=None,speakers={},lock=__import__('threading').Lock(),denoise_cfg=None,new_denoiser=lambda: None)
        self.ear = ear_server.Ear(self.models, None, self.executor)

    async def asyncTearDown(self):
        if hasattr(self.ear, 'close'): await self.ear.close()
        self.vad_patch.stop()
        self.executor.shutdown()

    async def test_malformed_control_and_24k_ack(self):
        await self.ear.on_text('[]')
        self.assertEqual(self.ear.outbox[-1]['type'], 'error')
        await self.ear.on_text('{"type":"audio","format":"opus","sample_rate":24000,"channels":1,"frame_ms":60,"framing":"sea1"}')
        self.assertEqual(self.ear.outbox[-1]['type'], 'audio')
        self.assertEqual(self.ear.outbox[-1]['input_sample_rate'], 24000)

    async def test_delayed_inference_cannot_enroll_after_cancel(self):
        await self.ear.on_text('{"type":"enroll","name":"papa","request_id":"first"}')
        cur = self.ear.new_utterance(0)
        self.ear.self_history.add(0,32000,{'sounding':False,'moving':False})
        cur = self.ear.segment_context(cur,0,32000)
        started, finish = asyncio.Event(), asyncio.Event()
        async def delayed(_):
            started.set(); await finish.wait()
            return np.ones(3), SimpleNamespace(text='你好',lang='zh',emotion='HAPPY',event='Speech'), [], (1,1,1,1)
        self.ear.run = delayed
        self.models.kinds = lambda _: ({},.8)
        self.models.voice_p = lambda _: .8
        task = asyncio.create_task(self.ear.end_speech(cur,np.zeros(32000),0,32000,1))
        await started.wait()
        await self.ear.on_text('{"type":"enroll_cancel"}')
        await self.ear.on_text('{"type":"enroll","name":"mama","request_id":"second"}')
        finish.set(); await task
        self.assertEqual(self.ear.enroll.progress()['n'],0)

    async def test_capture_time_self_flags_survive_delayed_result(self):
        self.ear.self_history.add(0,32000,{'sounding':True,'moving':False})
        cur = self.ear.segment_context(self.ear.new_utterance(0),0,32000)
        self.ear.self_flags['sounding'] = False
        async def inference(_):
            return np.ones(3), SimpleNamespace(text='你好',lang='zh',emotion='HAPPY',event='Speech'), [], (1,1,1,1)
        self.ear.run = inference
        self.models.kinds = lambda _: ({},.8)
        self.models.voice_p = lambda _: .8
        await self.ear.end_speech(cur,np.zeros(32000),0,32000,1)
        self.assertTrue(self.ear.outbox[-1]['self']['sounding'])

    async def quiet_window(self, source, tags, lift_db=7.0):
        """One 1 s window lift_db above a -44 dBFS floor, tagged as given; returns posted sound starts."""
        import sound_kinds
        if source is not None:
            await self.ear.on_text('{"type":"audio","format":"pcm16","sample_rate":16000,"channels":1,"source":"%s"}' % source)
        self.models.kind_of = {name: k for k, (_, labels) in sound_kinds.KINDS.items() for name in labels}
        self.models.tags = lambda _: tags
        self.models.kinds = lambda t: self.module.Models.kinds(self.models, t)
        self.ear.floor = lambda: -44.0
        self.ear.ring.write(np.full(16000, 10 ** ((-44.0 + lift_db) / 20), dtype=np.float32))
        before = len(self.ear.outbox)
        await self.ear.tag_window(self.ear.ring.end - 16000, self.ear.ring.end)
        return [e for e in self.ear.outbox[before:] if e.get('type') == 'sound' and e.get('phase') == 'start']

    async def test_robot_mic_reports_quiet_typing(self):
        starts = await self.quiet_window('stackchan-test', [('Typing', 0.34), ('Insect', 0.5)])
        self.assertEqual([e['kind'] for e in starts], ['typing'])  # the hum-like insect keeps the 15 dB gate
        self.assertEqual(self.ear.outbox[0]['type'], 'hello')

    async def test_mac_mic_keeps_the_original_gate(self):
        self.assertEqual(await self.quiet_window('mac-mic', [('Typing', 0.34)]), [])
        self.assertEqual(await self.quiet_window(None, [('Typing', 0.9)]), [])  # still faint at +7 dB

    async def test_robot_quiet_gate_limits(self):
        self.assertEqual(await self.quiet_window('stackchan-test', [('Heart sounds, heartbeat', 0.6)]), [])
        self.assertEqual(await self.quiet_window(None, [('Typing', 0.29)]), [])          # below 0.30
        self.assertEqual(await self.quiet_window(None, [('Typing', 0.5)], lift_db=4.0), [])  # below +5 dB
        self.ear.speaking = True
        self.assertEqual(await self.quiet_window(None, [('Typing', 0.5)]), [])          # not during speech
        self.ear.speaking = False
        self.assertEqual([e['kind'] for e in await self.quiet_window(None, [('Sigh', 0.31)])], ['body'])

    async def test_her_own_voice_is_silenced_before_listening(self):
        """Approach A (2026-11-09): while the robot reports sounding (playback + its 1.2 s tail) the
        ear listens to silence; the recording keeps what the mic really heard."""
        fed = []
        self.ear.vad = SimpleNamespace(reset=lambda: None, accept_waveform=lambda w: fed.append(np.array(w)),
                                       is_speech_detected=lambda: False, empty=lambda: True)
        recorded = []
        self.ear.rec = SimpleNamespace(audio_in=lambda raw, pcm, opus: recorded.append(pcm), declare=lambda m: None, client_msg=lambda m: None)
        self.ear.next_tag = 10 ** 9  # no tagging in this test
        loud = (np.full(1024, 8000, dtype='<i2')).tobytes()
        await self.ear.on_text('{"type":"self","sounding":true,"moving":false}')
        await self.ear.on_audio(loud)
        self.assertEqual(float(np.abs(self.ear.ring.read(0, 1024)).max()), 0.0)
        self.assertLess(float(np.abs(np.concatenate(fed)).max()), 0.01)          # only the VAD dither
        self.assertEqual(recorded[-1], loud)                                       # recording untouched
        self.assertGreater(self.ear.tick_sq, 0)                                    # level meter still real
        await self.ear.on_text('{"type":"self","sounding":false,"moving":false}')
        await self.ear.on_audio(loud)
        self.assertAlmostEqual(float(self.ear.ring.read(1024, 2048).max()), 8000 / 32768, places=4)
        self.assertEqual(self.ear.self_history.overlap(0, 1024)['sounding'], True)

    async def test_robot_audio_is_denoised_before_listening(self):
        """2026-11-10: robot (stackchan*) audio goes through the streaming denoiser before VAD/ASR/tags;
        the level meter and audio.opus keep the raw mic, heard.opus and the gates get the denoised
        audio. Mac sources are untouched."""
        class Halver:                       # stands in for GTCRN: keeps alignment, holds back one hop
            def __init__(self): self.held = np.zeros(0, dtype=np.float32)
            def run(self, x, sr):
                buf = np.concatenate([self.held, np.asarray(x, dtype=np.float32) * 0.5])
                n = len(buf) // 256 * 256
                self.held = buf[n:]
                return SimpleNamespace(samples=buf[:n])
        self.models.new_denoiser = Halver
        fed, heard, recorded = [], [], []
        self.ear.vad = SimpleNamespace(reset=lambda: None, accept_waveform=lambda w: fed.append(np.array(w)),
                                       is_speech_detected=lambda: False, empty=lambda: True)
        self.ear.rec = SimpleNamespace(audio_in=lambda raw, pcm, opus: recorded.append(pcm), declare=lambda m: None,
                                       client_msg=lambda m: None, heard_in=lambda x: heard.append(np.array(x)))
        self.ear.next_tag = 10 ** 9
        await self.ear.on_text('{"type":"audio","format":"pcm16","sample_rate":16000,"channels":1,"source":"stackchan-test"}')
        self.assertEqual(self.ear.outbox[-1]['denoise'], 'gtcrn')
        chunk = np.full(1000, 8000, dtype='<i2').tobytes()
        for _ in range(3):
            await self.ear.on_audio(chunk)
        self.assertEqual(self.ear.ring.end, 2816)                                   # whole hops only, 3000 in
        self.assertAlmostEqual(float(self.ear.ring.read(0, 2816).max()), 0.5 * 8000 / 32768, places=4)
        self.assertEqual(sum(len(h) for h in heard), 2816)                          # heard.opus = what VAD got
        self.assertEqual(recorded[-1], chunk)                                       # audio.opus untouched
        self.assertIsNot(self.ear.tag_ring, self.ear.ring)                          # sound tags read the raw mic
        self.assertEqual(self.ear.tag_ring.end, 3000)
        self.assertAlmostEqual(float(self.ear.tag_ring.read(0, 3000).max()), 8000 / 32768, places=4)
        self.assertAlmostEqual(self.ear.tick_sq / self.ear.tick_n, (8000 / 32768) ** 2, places=6)   # raw meter
        self.assertAlmostEqual(self.ear.heard_sq / self.ear.heard_n, (4000 / 32768) ** 2, places=6)
        # her own voice is still silenced, after the denoiser
        await self.ear.on_text('{"type":"self","sounding":true,"moving":false}')
        await self.ear.on_audio(np.full(1024, 8000, dtype='<i2').tobytes())
        self.assertEqual(float(np.abs(self.ear.ring.read(2816, self.ear.ring.end)).max()), 0.0)

    async def test_mac_audio_is_not_denoised(self):
        self.models.new_denoiser = lambda: self.fail('Mac audio must not be denoised')
        await self.ear.on_text('{"type":"audio","format":"pcm16","sample_rate":16000,"channels":1,"source":"mac-mic"}')
        self.assertIsNone(self.ear.outbox[-1]['denoise'])
        self.ear.next_tag = 10 ** 9
        self.ear.vad = SimpleNamespace(reset=lambda: None, accept_waveform=lambda w: None,
                                       is_speech_detected=lambda: False, empty=lambda: True)
        await self.ear.on_audio(np.full(1000, 8000, dtype='<i2').tobytes())
        self.assertEqual(self.ear.ring.end, 1000)
        self.assertIs(self.ear.tag_ring, self.ear.ring)
        self.assertAlmostEqual(float(self.ear.ring.read(0, 1000).max()), 8000 / 32768, places=4)

    async def test_forget_message_replies_with_the_remaining_speakers(self):
        gone = []
        def forget(name):
            if name != 'person-a': raise KeyError(name)
            gone.append(name); self.models.speakers = {'person-b': 1}
        self.models.speakers = {'person-a': 1, 'person-b': 1}; self.models.forget_speaker = forget
        await self.ear.on_text('{"type":"forget","name":"person-a"}')
        reply = {k: self.ear.outbox[-1][k] for k in ('type','op','name','ok','speakers')}
        self.assertEqual(reply, {'type':'speakers','op':'forget','name':'person-a','ok':True,'speakers':['person-b']})
        await self.ear.on_text('{"type":"forget","name":"nobody"}')
        self.assertFalse(self.ear.outbox[-1]['ok']); self.assertEqual(self.ear.outbox[-1]['error'], 'unknown_speaker')
        await self.ear.on_text('{"type":"forget"}')
        self.assertEqual(self.ear.outbox[-1]['type'], 'error')
        self.assertEqual(gone, ['person-a'])

    async def test_close_prevents_late_results(self):
        await self.ear.close()
        before = len(self.ear.outbox)
        self.ear.post({'type':'speech'})
        self.assertEqual(len(self.ear.outbox),before)

    async def test_voiceprint_does_not_change_acoustic_eligibility(self):
        self.models.voice_p=lambda _: .2
        known=self.ear.voice([],{'band':'known'})
        unknown=self.ear.voice([],{'band':'unknown'})
        self.assertEqual(known.get('acoustic_band'),'unclear')
        self.assertEqual(unknown.get('acoustic_band'),'unclear')

    async def test_gap_drops_queued_speech_and_clamps_pre_roll(self):
        self.ear.ring.write(np.ones(8000,dtype=np.float32))
        self.ear.self_history.add(0,8000,{'sounding':True,'moving':False})
        self.ear.post({'type':'speech','phase':'end','emotion':'happy'})
        self.ear.reset_segment()
        self.assertFalse(any(e['type']=='speech' for e in self.ear.outbox))
        self.ear.ring.write(np.zeros(32000,dtype=np.float32))
        self.ear.self_history.add(8000,40000,{'sounding':False,'moving':False})
        context=self.ear.segment_context(self.ear.new_utterance(8000),4000,40000)
        self.assertFalse(context['self']['sounding'])
        audio=self.ear.segment_audio(8000,40000)
        self.assertEqual(len(audio),32000)
        self.assertEqual(np.count_nonzero(audio),0)
