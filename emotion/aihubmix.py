"""AIHubMix native realtime transcription. 24 kHz mono, server-side only."""
import asyncio
import base64
from collections import deque
import json

import numpy as np


class AIHubMixRealtime:
    sample_rate = 24000
    model = "gpt-live-transcribe"

    def __init__(self, config, _pending_file=None):
        self.key = config["api_key"]
        self.ws = self.receiver = None
        self.sent_ms = self.segment_ms = self.quiet_ms = 0
        self.voiced = False
        self.partial = {}
        self.committed = deque(maxlen=16)
        self.bounds = {}

    async def cleanup_pending(self):
        pass  # The gateway releases the realtime session when its socket closes.

    async def open(self, on_text):
        from websockets.asyncio.client import connect
        self.ws = await connect(
            "wss://aihubmix.com/v1/realtime?intent=transcription&model=gpt-live-transcribe",
            additional_headers={"Authorization": "Bearer " + self.key},
            open_timeout=15, close_timeout=2, max_size=128*1024, max_queue=16)
        await self.ws.send(json.dumps({"type": "session.update", "session": {
            "type": "transcription", "audio": {"input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": self.model, "languages": ["zh", "en"],
                                  "prompt": "日常中文对话，偶有英文词。逐字转写，不回答，不补充不存在的内容。"},
                "turn_detection": None, "noise_reduction": {"type": "far_field"}}}}}))
        async with asyncio.timeout(10):
            while True:
                event = json.loads(await self.ws.recv())
                if event.get("type") == "error":
                    raise RuntimeError("Realtime configuration rejected")
                if event.get("type") == "session.updated":
                    break
        self.receiver = asyncio.create_task(self.receive(on_text))

    async def receive(self, on_text):
        async for wire in self.ws:
            event = json.loads(wire)
            kind, item = event.get("type", ""), event.get("item_id", "current")
            if kind == "error" or kind.endswith("transcription.failed"):
                raise RuntimeError("Realtime transcription failed")
            if kind == "input_audio_buffer.committed":
                if self.committed:
                    self.bounds[item] = self.committed.popleft()
            elif kind.endswith("transcription.delta"):
                delta = event.get("delta", "")
                if isinstance(delta, str) and delta:
                    self.partial[item] = (self.partial.get(item, "") + delta)[-1500:]
                    on_text(item, self.partial[item], False, self.bounds.get(item, self.sent_ms))
            elif kind.endswith("transcription.completed"):
                text = event.get("transcript", "")
                if isinstance(text, str) and text.strip():
                    on_text(item, text[:1500], True, self.bounds.get(item, self.sent_ms))
                self.partial.pop(item, None)
                self.bounds.pop(item, None)
            if len(self.partial) > 16 or len(self.bounds) > 16:
                raise RuntimeError("Realtime result backlog")

    async def send(self, pcm):
        if self.receiver and self.receiver.done():
            self.receiver.result()
            raise RuntimeError("Realtime socket ended")
        await asyncio.wait_for(self.ws.send(json.dumps({"type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode("ascii")})), 2)
        duration = len(pcm) / 48  # 24 kHz s16le -> 48 bytes/ms.
        self.sent_ms += duration
        self.segment_ms += duration
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        loud = np.sqrt(np.mean(samples * samples)) >= 0.008
        self.voiced |= loud
        self.quiet_ms = 0 if loud else self.quiet_ms + duration
        if self.segment_ms >= 6000 or (self.voiced and self.quiet_ms >= 500 and self.segment_ms >= 500):
            await self.commit()

    async def commit(self):
        if self.segment_ms >= 100:
            self.committed.append(self.sent_ms)
            await asyncio.wait_for(self.ws.send(json.dumps({"type": "input_audio_buffer.commit"})), 2)
            self.segment_ms = self.quiet_ms = 0
            self.voiced = False

    async def close(self):
        # No final commit on idle shutdown: stop sending and release immediately.
        if self.ws:
            await self.ws.close()
        if self.receiver:
            self.receiver.cancel()
            await asyncio.gather(self.receiver, return_exceptions=True)
        self.ws = self.receiver = None
