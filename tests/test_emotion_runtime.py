"""Run with the emotion venv; no robot, credentials or external network needed."""
import asyncio
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import numpy as np
    import soxr
    from emotion.app import App
    from emotion.tingwu import Tingwu
    from emotion.aihubmix import AIHubMixRealtime
    from emotion.robot import decode_header
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "Run using .tools/emotion-venv/bin/python")
class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_aihubmix_manual_segments_and_partial_results(self):
        class Socket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.sent = []
                self.closed = False
            async def send(self, wire):
                message = json.loads(wire)
                self.sent.append(message)
                if message['type'] == 'session.update':
                    await self.messages.put(json.dumps({'type':'session.updated'}))
            async def recv(self): return await self.messages.get()
            def __aiter__(self): return self
            async def __anext__(self): return await self.recv()
            async def close(self): self.closed = True
        socket, seen = Socket(), []
        async def connect(url, **kwargs):
            self.assertTrue(url.startswith('wss://aihubmix.com/v1/realtime?'))
            self.assertEqual(kwargs['additional_headers'], {'Authorization':'Bearer test-key'})
            return socket
        client = AIHubMixRealtime({'api_key':'test-key'})
        with patch('websockets.asyncio.client.connect', connect):
            await client.open(lambda *values: seen.append(values))
            config = socket.sent[0]['session']['audio']['input']
            self.assertEqual(config['format'], {'type':'audio/pcm','rate':24000})
            self.assertIsNone(config['turn_detection'])
            await client.commit()  # No empty commits.
            self.assertEqual(len(socket.sent), 1)
            loud = np.full(2400, 1500, dtype='<i2').tobytes()
            quiet = bytes(4800)
            await client.send(loud)
            for _ in range(5): await client.send(quiet)
            self.assertEqual(socket.sent[-1]['type'], 'input_audio_buffer.commit')
            self.assertEqual(base64.b64decode(socket.sent[1]['audio']), loud)
            self.assertEqual(list(client.committed), [600])
            for event in [
                {'type':'input_audio_buffer.committed','item_id':'a'},
                {'type':'conversation.item.input_audio_transcription.delta','item_id':'a','delta':'我很'},
                {'type':'conversation.item.input_audio_transcription.delta','item_id':'a','delta':'开心'},
                {'type':'conversation.item.input_audio_transcription.completed','item_id':'a','transcript':'我很开心。'}]:
                await socket.messages.put(json.dumps(event))
            await asyncio.sleep(0.01)
            self.assertEqual(seen, [('a','我很',False,600),('a','我很开心',False,600),('a','我很开心。',True,600)])
            self.assertFalse(client.partial or client.bounds)
            count = len(socket.sent)
            await client.close()
            self.assertTrue(socket.closed)
            self.assertEqual(len(socket.sent), count)  # Closing sends no trailing audio/commit.

    async def test_idle_closes_and_sound_opens_new_task(self):
        events = []
        class Cloud:
            def __init__(self, *args): pass
            async def open(self, callback): events.append("open")
            async def close(self): events.append("close")
            async def send(self, pcm): pass
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"token").write_text("test-token")
            (root/"address").write_text("http://127.0.0.1:8080")
            (root/"keys").write_text(json.dumps(dict(access_key_id="test",access_key_secret="test",app_key="test")))
            app = App(dict(token_file=str(root/"token"), address_file=str(root/"address"),
                           device_id="test-device", state_dir=folder, tingwu_credentials_file=str(root/"keys")))
            app.cloud_type = Cloud
            app.audio_at = time.monotonic()
            with patch("emotion.app.Tingwu", Cloud):
                task = asyncio.create_task(app.stt_loop())
                await asyncio.sleep(0.05)
                self.assertEqual(events, ["open"])
                app.watchdog.last_output = time.monotonic() - 600
                await asyncio.sleep(0.25)
                self.assertEqual(events, ["open", "close"])
                self.assertEqual(app.stt_state,"sleeping")
                await asyncio.sleep(0.15)
                self.assertEqual(events, ["open", "close"])
                app.sound_event = True
                await asyncio.sleep(0.15)
                self.assertEqual(events, ["open", "close", "open"])
                app.stop.set()
                await asyncio.wait_for(task, 1)
                self.assertEqual(events[-1], "close")
            app.model_pool.shutdown()
            app.instance_lock.close()

    async def test_wss_start_results_stop_and_pending_recovery(self):
        class Socket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.names = []
                self.closed = False
            async def send(self, message):
                name = json.loads(message)["header"]["name"]
                self.names.append(name)
                reply = "TranscriptionStarted" if name == "StartTranscription" else "TranscriptionCompleted"
                await self.messages.put(json.dumps({"header": {"name": reply}}))
            async def recv(self): return await self.messages.get()
            def __aiter__(self): return self
            async def __anext__(self): return await self.recv()
            async def close(self): self.closed = True
        socket = Socket()
        async def connect(*args, **kwargs): return socket
        calls = []
        def api(body, stop=False):
            calls.append((body, stop))
            return {"TaskId":"fake-task", "MeetingJoinUrl":"wss://tingwu-realtime-cn-beijing.aliyuncs.com/test"}
        with tempfile.TemporaryDirectory() as folder:
            pending = Path(folder)/"pending.json"
            client = Tingwu(dict(access_key_id="test",access_key_secret="test",app_key="test"), pending)
            client.api = api
            seen = []
            with patch("websockets.asyncio.client.connect", connect):
                await client.open(lambda *values: seen.append(values))
                self.assertTrue(pending.exists())
                await socket.messages.put(json.dumps({"header":{"name":"SentenceEnd"},
                    "payload":{"index":1,"result":"测试文本","time":100}}))
                await asyncio.sleep(0.01)
                self.assertEqual(seen,[(1,"测试文本",True,100)])
                await client.close()
            self.assertTrue(socket.closed)
            self.assertEqual(socket.names,["StartTranscription","StopTranscription"])
            self.assertEqual(calls[-1][0]["Input"], {"TaskId":"fake-task"})
            self.assertTrue(calls[-1][1])
            self.assertFalse(pending.exists())
            pending.write_text(json.dumps({"task_id":"orphan"}))
            await client.cleanup_pending()
            self.assertEqual(calls[-1][0]["Input"], {"TaskId":"orphan"})

    def test_resampling_continuity_and_antialias(self):
        # Full and 20 ms incremental conversion must agree, including filter state.
        samples = (np.sin(2*np.pi*1000*np.arange(24000)/24000)*15000).astype(np.int16)
        stream = soxr.ResampleStream(24000,16000,1,dtype="int16",quality="HQ")
        blocks = [stream.resample_chunk(samples[i:i+480],last=(i==23520)) for i in range(0,24000,480)]
        output = np.concatenate(blocks)
        self.assertEqual(len(output),16000)
        self.assertLess(np.max(np.abs(output.astype(float)-soxr.resample(samples,24000,16000).astype(float))),3)
        alias = (np.sin(2*np.pi*10000*np.arange(24000)/24000)*15000).astype(np.int16)
        filtered = soxr.resample(alias,24000,16000)[500:-500]
        self.assertLess(np.sqrt(np.mean(filtered.astype(float)**2)),20)
