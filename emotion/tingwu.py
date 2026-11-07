"""Tingwu 2023-09-30 CreateTask + authenticated WSS, no transcript logging."""
import asyncio
import json
import uuid
from urllib.parse import urlsplit

from .core import transcript_event


class Tingwu:
    def __init__(self, config, pending_file):
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkcore.auth.credentials import AccessKeyCredential
        self.config = config
        self.pending_file = pending_file
        self.client = AcsClient(region_id="cn-beijing", credential=AccessKeyCredential(
            config["access_key_id"], config["access_key_secret"]),
            auto_retry=False, connect_timeout=5, timeout=8)
        self.task_id = None
        self.ws = None
        self.receiver = None
        self.completed = asyncio.Event()

    def api(self, body, stop=False):
        from aliyunsdkcore.request import CommonRequest
        req = CommonRequest()
        req.set_accept_format("json")
        req.set_domain("tingwu.cn-beijing.aliyuncs.com")
        req.set_version("2023-09-30")
        req.set_protocol_type("https")
        req.set_method("PUT")
        req.set_uri_pattern("/openapi/tingwu/v2/tasks")
        req.add_header("Content-Type", "application/json")
        req.add_query_param("type", "realtime")
        if stop:
            req.add_query_param("operation", "stop")
        req.set_content(json.dumps(body).encode())
        result = json.loads(self.client.do_action_with_exception(req))
        if str(result.get("Code")) != "0":
            raise RuntimeError("Tingwu API rejected task")
        return result.get("Data", {})

    async def cleanup_pending(self):
        if self.pending_file.exists():
            task = json.loads(self.pending_file.read_text())["task_id"]
            await asyncio.to_thread(self.api, {"AppKey": self.config["app_key"], "Input": {"TaskId": task}}, True)
            self.pending_file.unlink(missing_ok=True)

    async def open(self, on_text):
        from websockets.asyncio.client import connect
        await self.cleanup_pending()  # Never create more tasks after unconfirmed stop.
        data = await asyncio.to_thread(self.api, {
            "AppKey": self.config["app_key"],
            "Input": {"Format": "pcm", "SampleRate": 16000, "SourceLanguage": "cn",
                      "TaskKey": "chan-" + uuid.uuid4().hex, "ProgressiveCallbacksEnabled": False},
            "Parameters": {"Transcription": {"OutputLevel": 2, "DiarizationEnabled": False}},
        })
        self.task_id = data["TaskId"]
        self.pending_file.write_text(json.dumps({"task_id": self.task_id}))
        self.pending_file.chmod(0o600)
        url = urlsplit(data["MeetingJoinUrl"])
        if url.scheme != "wss" or not (url.hostname or "").endswith(".aliyuncs.com"):
            raise RuntimeError("Unexpected Tingwu WSS endpoint")
        self.ws = await connect(data["MeetingJoinUrl"], open_timeout=8, close_timeout=2,
                                max_size=128 * 1024, max_queue=16, proxy=None)
        await self.ws.send(json.dumps({"header": {"name": "StartTranscription", "namespace": "SpeechTranscriber"},
                                       "payload": {"format": "pcm", "sample_rate": 16000}}))
        started = False
        async with asyncio.timeout(10):
            while not started:
                msg = json.loads(await self.ws.recv())
                if msg.get("header", {}).get("name") == "TaskFailed":
                    raise RuntimeError("Tingwu stream rejected")
                started = msg.get("header", {}).get("name") == "TranscriptionStarted"
        self.completed.clear()
        self.receiver = asyncio.create_task(self.receive(on_text))

    async def receive(self, on_text):
        async for wire in self.ws:
            msg = json.loads(wire)
            name = msg.get("header", {}).get("name")
            if name == "TaskFailed":
                raise RuntimeError("Tingwu stream failure")
            if name == "TranscriptionCompleted":
                self.completed.set()
                return
            event = transcript_event(msg)
            if event:
                on_text(*event)

    async def send(self, pcm):
        if self.receiver and self.receiver.done():
            self.receiver.result()
            raise RuntimeError("Tingwu stream ended")
        await asyncio.wait_for(self.ws.send(pcm), 2)

    async def close(self):
        # Close the socket even if StopTranscription fails, then end the task.
        try:
            if self.ws:
                try:
                    await asyncio.wait_for(self.ws.send(json.dumps({"header": {
                        "name": "StopTranscription", "namespace": "SpeechTranscriber"}, "payload": {}})), 2)
                    await asyncio.wait_for(self.completed.wait(), 2)
                except Exception:
                    pass
                finally:
                    await self.ws.close()
        finally:
            if self.receiver:
                self.receiver.cancel()
                await asyncio.gather(self.receiver, return_exceptions=True)
            self.receiver = self.ws = None
            # Journal remains if this fails; caller retries before any new task.
            if self.task_id or self.pending_file.exists():
                await self.cleanup_pending()
            self.task_id = None
