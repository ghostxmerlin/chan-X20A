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
        self.models = SimpleNamespace(vad_cfg=None,speakers={},lock=__import__('threading').Lock())
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

    async def test_close_prevents_late_results(self):
        await self.ear.close()
        before = len(self.ear.outbox)
        self.ear.post({'type':'speech'})
        self.assertEqual(len(self.ear.outbox),before)
