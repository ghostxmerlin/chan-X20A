"""听悟 (DashScope tingwu-meeting) realtime transcription of one ear connection (2026-11-10).

Flow (validated by hand on 2026-11-09/10, see docs/soul-mind.md):
  POST createTask (type realtime, 16 kHz pcm, diarization on) -> dataId
  wss run-task {appId, dataId, directive:start}; after "speech-listen", stream PCM16 binary
  recognize-result messages: one per partial / final sentence (sentenceEnd)
  finish-task {directive:stop}; then POST createTask {type:realtime, dataId} ends the task and
  POST getTask polls until transcriptionPath (paragraphs with speakerId) is ready.
Keys come from the hubmix env file on the server (DASHSCOPE_API_KEY / DASHSCOPE_APP_ID); they
never enter this repository. Every network call has a timeout; failures end this stream only.
"""
import asyncio
import json
import time
import urllib.request
import uuid

GEN = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
WS = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
SR = 16000
CHUNK = 3200                   # 100 ms of PCM16 per websocket frame
MAX_BUFFER = SR * 2 * 30       # audio kept while connecting: 30 s; older audio is dropped


def _http(key, body, timeout=30):
    req = urllib.request.Request(GEN, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def sentence_from(msg):
    """recognize-result payload → {"id","begin_ms","end_ms","text","final"} or None."""
    out = ((msg.get("payload") or {}).get("output") or {})
    if out.get("action") != "recognize-result":
        return None
    t = out.get("transcription") or {}
    text = (t.get("text") or "").strip()
    if not text:
        return None
    # Realtime results carry times per word (ms from the stream start), not per sentence.
    words = [w for w in (t.get("words") or []) if isinstance(w, dict)]
    begin = t.get("beginTime", words[0].get("beginTime") if words else None)
    end = t.get("endTime", words[-1].get("endTime") if words else None)
    return {"id": t.get("sentenceId"), "begin_ms": begin, "end_ms": end, "text": text[:1000], "final": bool(t.get("sentenceEnd"))}


def paragraphs_from(result):
    """transcriptionPath JSON → [{"speaker","begin_ms","end_ms","text"}]."""
    tr = result.get("Transcription", result)
    out = []
    for p in tr.get("Paragraphs") or tr.get("paragraphs") or []:
        words = p.get("Words") or p.get("words") or []
        text = "".join(w.get("Text", w.get("text", "")) for w in words).strip()
        if not text:
            continue
        begin = words[0].get("Start", words[0].get("start")) if words else None
        end = words[-1].get("End", words[-1].get("end")) if words else None
        out.append({"speaker": str(p.get("SpeakerId", p.get("speakerId", ""))), "begin_ms": begin, "end_ms": end,
                    "text": text[:4000]})
    return out


class TingwuStream:
    """One realtime task. feed() never blocks; callbacks run on the event loop."""

    def __init__(self, key, app_id, on_sentence, on_final, on_error, loop=None):
        self.key, self.app = key, app_id
        self.on_sentence, self.on_final, self.on_error = on_sentence, on_final, on_error
        self.loop = loop or asyncio.get_event_loop()
        self.buffer = bytearray()
        self.sent_bytes = 0
        self.data_id = None
        self.task_id = uuid.uuid4().hex[:16]
        self.ready = asyncio.Event()
        self.closing = False
        self.failed = None
        self.ws = None
        self.runner = None
        self.sentences = 0

    def start(self):
        self.runner = self.loop.create_task(self._run())

    def feed(self, pcm16: bytes):
        if self.closing or self.failed:
            return
        self.buffer += pcm16
        if len(self.buffer) > MAX_BUFFER:              # still connecting: keep only the newest 30 s
            del self.buffer[:len(self.buffer) - MAX_BUFFER]

    def _payload(self, inp):
        return {"model": "tingwu-meeting-realtime", "task_group": "aigc", "task": "multimodal-generation",
                "function": "generation", "input": inp}

    def _head(self, action):
        return {"action": action, "task_id": self.task_id, "streaming": "duplex"}

    async def _run(self):
        from websockets.asyncio.client import connect
        try:
            created = await asyncio.to_thread(_http, self.key, {"model": "tingwu-meeting", "input": {
                "task": "createTask", "appId": self.app, "type": "realtime", "format": "pcm", "sampleRate": SR},
                "parameters": {"transcription": {"model": "multilingual", "languageHints": ["cn"],
                                                 "diarizationEnabled": True, "diarizationSpeakerCount": 0}}})
            self.data_id = created["output"]["dataId"]
            async with connect(WS, additional_headers={"Authorization": f"Bearer {self.key}"}, proxy=None,
                               max_size=None, open_timeout=15) as ws:
                self.ws = ws
                reader = asyncio.create_task(self._read(ws))
                await ws.send(json.dumps({"header": self._head("run-task"),
                                          "payload": self._payload({"appId": self.app, "dataId": self.data_id,
                                                                    "directive": "start"})}))
                await asyncio.wait_for(self.ready.wait(), 30)
                while True:
                    if len(self.buffer) >= CHUNK:
                        chunk = bytes(self.buffer[:CHUNK]); del self.buffer[:CHUNK]
                        await ws.send(chunk); self.sent_bytes += len(chunk)
                        continue
                    if self.closing:
                        if self.buffer:
                            await ws.send(bytes(self.buffer)); self.sent_bytes += len(self.buffer); self.buffer.clear()
                        break
                    if reader.done():
                        raise RuntimeError("tingwu reader ended")
                    await asyncio.sleep(0.05)
                await ws.send(json.dumps({"header": self._head("finish-task"),
                                          "payload": self._payload({"directive": "stop"})}))
                try:
                    await asyncio.wait_for(reader, 60)
                except asyncio.TimeoutError:
                    reader.cancel()
        except Exception as e:                           # noqa: BLE001 - any failure ends this stream only
            self.failed = f"{type(e).__name__}: {str(e)[:160]}"
            self.on_error(self.failed)
        finally:
            self.ws = None
        if self.data_id:
            await self._final()

    async def _read(self, ws):
        async for m in ws:
            if isinstance(m, bytes):
                continue
            j = json.loads(m)
            event = (j.get("header") or {}).get("event")
            act = ((j.get("payload") or {}).get("output") or {}).get("action")
            if act == "speech-listen":
                self.ready.set()
            s = sentence_from(j)
            if s:
                if s["final"]:
                    self.sentences += 1
                self.on_sentence(s)
            if event in ("task-finished", "task-failed"):
                if event == "task-failed":
                    self.on_error("task-failed: " + json.dumps(j.get("header", {}), ensure_ascii=False)[:160])
                self.ready.set()
                return

    async def _final(self):
        """End the task and fetch the diarized result (can take a minute)."""
        try:
            await asyncio.to_thread(_http, self.key, {"model": "tingwu-meeting", "input": {
                "task": "createTask", "type": "realtime", "appId": self.app, "dataId": self.data_id}})
            for _ in range(40):
                out = (await asyncio.to_thread(_http, self.key, {"model": "tingwu-meeting", "input": {
                    "task": "getTask", "appId": self.app, "dataId": self.data_id}})).get("output", {})
                url = out.get("transcriptionPath")
                if url:
                    self.on_final(paragraphs_from(await asyncio.to_thread(_fetch, url)))
                    return
                if out.get("status") in (-1, 2) or out.get("errorCode"):
                    self.on_error(f"final failed: status {out.get('status')} {out.get('errorCode', '')}")
                    return
                await asyncio.sleep(5)
            self.on_error("final not ready after 200 s")
        except Exception as e:                           # noqa: BLE001
            self.on_error(f"final {type(e).__name__}: {str(e)[:160]}")

    def close(self):
        """Stop sending; the runner finishes the task and fetches the final result in background."""
        self.closing = True
        if not self.ready.is_set() and self.runner and not self.data_id:
            self.runner.cancel()

    def audio_seconds(self):
        return self.sent_bytes / (SR * 2)


def load_env(path):
    """KEY=VALUE lines (quotes stripped); missing file → {}."""
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return cfg
